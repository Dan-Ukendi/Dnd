import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Battle, BattleToken, Character, User

PLAYER_WINDOW = 7
IMAGE_TYPES = {"WEBP": "image/webp", "PNG": "image/png", "JPEG": "image/jpeg"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class TokenSpec:
    label: str
    color: str
    size: str
    character_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class BattleSpec:
    name: str
    cols: int
    rows: int
    tokens: tuple[TokenSpec, ...]


FIRST_BATTLE = BattleSpec(
    name="Combat 1",
    cols=32,
    rows=24,
    tokens=(
        TokenSpec("Troll", "#111111", "large"),
        TokenSpec("Gobelin 1", "#2f9e44", "small"),
        TokenSpec("Gobelin 2", "#2f9e44", "small"),
        TokenSpec("Zilvra", "#9a9a9a", "medium", ("zilvra",)),
        TokenSpec("Khaetar", "#c0392b", "medium", ("khaetar", "keator")),
    ),
)


class BattleError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    col: int
    row: int
    cols: int
    rows: int

    @property
    def key(self) -> str:
        return f"{self.col}-{self.row}-{self.cols}-{self.rows}"

    def contains(self, token: BattleToken) -> bool:
        return (
            token.col is not None
            and token.row is not None
            and self.col <= token.col < self.col + self.cols
            and self.row <= token.row < self.row + self.rows
        )


def _find_character(db: Session, names: tuple[str, ...]) -> Character | None:
    if not names:
        return None
    for character in db.scalars(select(Character).order_by(Character.id)):
        if character.name.casefold().startswith(names):
            return character
    return None


def install_battle(
    db: Session, spec: BattleSpec, image: bytes | None = None
) -> tuple[Battle, list[str]]:
    notes: list[str] = []
    battle = db.scalar(select(Battle).where(Battle.name == spec.name))
    if battle is None:
        battle = Battle(name=spec.name, cols=spec.cols, rows=spec.rows)
        db.add(battle)
        db.flush()
        notes.append(f"created battle '{spec.name}' ({spec.cols}x{spec.rows})")
    if image is not None:
        battle.image_type, width, height = _check_image(image)
        battle.image = image
        battle.image_sha256 = hashlib.sha256(image).hexdigest()
        notes.append(
            f"map {width}x{height} px, cell {width / battle.cols:.1f}x{height / battle.rows:.1f} px"
        )
    existing = {token.label: token for token in battle.tokens}
    for position, token_spec in enumerate(spec.tokens):
        token = existing.get(token_spec.label)
        character = _find_character(db, token_spec.character_names)
        if token_spec.character_names and character is None:
            notes.append(f"no character found for token '{token_spec.label}'")
        if token is None:
            token = BattleToken(
                label=token_spec.label,
                color=token_spec.color,
                size=token_spec.size,
                position=position,
            )
            battle.tokens.append(token)
            notes.append(f"added token '{token_spec.label}'")
        if character is not None and token.character_id is None:
            token.character_id = character.id
            notes.append(f"token '{token_spec.label}' linked to {character.name}")
    db.commit()
    return battle, notes


def _check_image(data: bytes) -> tuple[str, int, int]:
    if len(data) > MAX_IMAGE_BYTES:
        raise BattleError("image too large")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format or ""
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise BattleError("unreadable image") from exc
    if image_format not in IMAGE_TYPES:
        raise BattleError(f"unsupported image format {image_format}")
    return IMAGE_TYPES[image_format], width, height


def latest_battle(db: Session) -> Battle | None:
    return db.scalar(
        select(Battle).options(selectinload(Battle.tokens)).order_by(Battle.id.desc()).limit(1)
    )


def player_token(db: Session, player: User) -> BattleToken | None:
    return db.scalar(
        select(BattleToken)
        .join(Character, Character.id == BattleToken.character_id)
        .where(Character.player_id == player.id)
        .order_by(BattleToken.battle_id.desc(), BattleToken.position)
        .limit(1)
    )


def player_window(battle: Battle, token: BattleToken, size: int = PLAYER_WINDOW) -> Window | None:
    if token.col is None or token.row is None:
        return None
    cols = min(size, battle.cols)
    rows = min(size, battle.rows)
    col = min(max(token.col - cols // 2, 0), battle.cols - cols)
    row = min(max(token.row - rows // 2, 0), battle.rows - rows)
    return Window(col, row, cols, rows)


def visible_tokens(battle: Battle, window: Window) -> list[BattleToken]:
    return [token for token in battle.tokens if window.contains(token)]


def move_token(token: BattleToken, col: int, row: int) -> None:
    battle = token.battle
    if not (0 <= col < battle.cols and 0 <= row < battle.rows):
        raise BattleError("cell outside the map")
    token.col = col
    token.row = row


def crop_image(battle: Battle, window: Window) -> bytes:
    if battle.image is None:
        raise BattleError("no map image")
    with Image.open(io.BytesIO(battle.image)) as image:
        width, height = image.size
        box = (
            round(window.col * width / battle.cols),
            round(window.row * height / battle.rows),
            round((window.col + window.cols) * width / battle.cols),
            round((window.row + window.rows) * height / battle.rows),
        )
        output = io.BytesIO()
        image.convert("RGB").crop(box).save(output, "WEBP", quality=82)
    return output.getvalue()
