import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, UnidentifiedImageError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Battle, BattleMap, BattleToken, Character, User, utcnow

PLAYER_WINDOW_SHARE = 0.5
PLAYER_WINDOW_MIN = 5
MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAP_LONG_SIDE = 2400
MAP_QUALITY = 80
THUMBNAIL_SIZE = 480
THUMBNAIL_QUALITY = 70
MAX_ENEMIES_PER_COLOR = 12
LABEL_LENGTH = 60
NAME_LENGTH = 100
GRID_IN_NAME = re.compile(r"(?<!\d)(\d{1,3})\s*[x×]\s*(\d{1,3})(?!\d)")


@dataclass(frozen=True)
class TokenColor:
    key: str
    label: str
    color: str


ENEMY_COLORS = (
    TokenColor("noir", "Noir", "#111111"),
    TokenColor("vert", "Vert", "#2f9e44"),
    TokenColor("violet", "Violet", "#7b2cbf"),
    TokenColor("orange", "Orange", "#e8590c"),
    TokenColor("bleu", "Bleu", "#1c7ed6"),
)
ENEMY_COLOR_BY_KEY = {color.key: color for color in ENEMY_COLORS}


@dataclass(frozen=True)
class PartyMember:
    label: str
    color: str
    character_names: tuple[str, ...]


PARTY = (
    PartyMember("Zilvra", "#9a9a9a", ("zilvra",)),
    PartyMember("Khaetar", "#c0392b", ("khaetar", "keator")),
)


@dataclass(frozen=True)
class LibraryEntry:
    title: str
    path: str
    grid: tuple[int, int] | None = None


LIBRARY = (
    LibraryEntry("Clairière en forêt", "il_1588xN.2796267092_aezx.webp"),
    LibraryEntry(
        "Mine abandonnée",
        "AbandonedMinePublic/Abandoned Mine Public/Abandoned-Mine-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Village englouti",
        "FloodedAbandonedVillagePublic/Flooded Abandoned Village Public/"
        "Flooded-Abandoned-Village-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Pont hanté",
        "HauntedBridgePublic/Haunted Bridge Public/Haunted-Bridge-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Tombeau de la jungle",
        "JungleDungeonTombPublic/Jungle Dungeon Tomb Public/"
        "Jungle-Dungeon-Tomb-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Cathédrale sacrée",
        "SacredCathedralPublic/Sacred Cathedral Public/"
        "Sacred-Cathedral-Gridded-46x66-MapPublic.jpg",
    ),
    LibraryEntry(
        "Site rituel du marais",
        "SwampRitualSitePublic/Swamp Ritual Site Public/"
        "Swamp-Ritual-Site-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Repaire des loups-garous",
        "WerewolfDenPublic/Werewolf Den Public/Werewolf-Den-Gridded-26x39-MapPublic.jpg",
    ),
    LibraryEntry(
        "Sous la ville",
        "Isabella's Hidden Room Maps/Underneath the City (digital, grid, clutter).jpg",
    ),
    LibraryEntry(
        "Toundra arctique",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/Arctic Tundra (digital, grid).jpg",
    ),
    LibraryEntry(
        "Donjon classique",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/"
        "Classic Dungeon (digital, clutter).jpg",
    ),
    LibraryEntry(
        "Oasis du désert",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/Desert Oasis (digital, grid).jpg",
    ),
    LibraryEntry(
        "Campement forestier",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/"
        "Forest Encampment (digital, day, grid).jpg",
    ),
    LibraryEntry(
        "Carrefour de montagne",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/"
        "Mountain Crossroads (digital, grid).jpg",
    ),
    LibraryEntry(
        "Plage tropicale",
        "Battle Map Variety Pack #1 from Rune Foundry/Digital/Tropical Beach (digital, grid).jpg",
    ),
    LibraryEntry(
        "Taverne et auberge (rez-de-chaussée)",
        "Taverns and Inns Map Pack from Rune Foundry/Digital/"
        "Tavern and Inn Combo (main floor, digital).jpg",
        (23, 16),
    ),
    LibraryEntry(
        "Taverne et auberge (étage)",
        "Taverns and Inns Map Pack from Rune Foundry/Digital/"
        "Tavern and Inn Combo (upstairs, digital).jpg",
        (23, 16),
    ),
    LibraryEntry(
        "Taverne et auberge (sous-sol)",
        "Taverns and Inns Map Pack from Rune Foundry/Digital/"
        "Tavern and Inn Combo (downstairs, covered, digital).jpg",
        (23, 16),
    ),
    LibraryEntry(
        "L'auberge du coin",
        "Taverns and Inns Map Pack from Rune Foundry/Digital/The Local Inn (digital).jpg",
        (23, 16),
    ),
    LibraryEntry(
        "Taverne traditionnelle",
        "Taverns and Inns Map Pack from Rune Foundry/Digital/Traditional Tavern (digital).jpg",
        (23, 16),
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


@dataclass(frozen=True)
class EncodedMap:
    image: bytes
    thumbnail: bytes
    cols: int
    rows: int


def _webp(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, "WEBP", quality=quality, method=5)
    return output.getvalue()


def _edge_profile(gray: Image.Image, vertical: bool) -> list[int]:
    width, height = gray.size
    if vertical:
        diff = ImageChops.difference(
            gray.crop((1, 0, width, height)), gray.crop((0, 0, width - 1, height))
        )
        return list(diff.resize((width - 1, 1), Image.Resampling.BOX).tobytes())
    diff = ImageChops.difference(
        gray.crop((0, 1, width, height)), gray.crop((0, 0, width, height - 1))
    )
    return list(diff.resize((1, height - 1), Image.Resampling.BOX).tobytes())


def _grid_count(profile: list[int], length: int) -> tuple[int, float] | None:
    mean = sum(profile) / len(profile) or 1
    scores: list[tuple[int, float]] = []
    for count in range(4, 121):
        cell = length / count
        if cell < 20:
            break
        peaks = [
            max(profile[max(round(k * cell) - 2, 0) : round(k * cell) + 1]) for k in range(1, count)
        ]
        scores.append((count, sum(peaks) / len(peaks) / mean))
    best = max((score for _, score in scores), default=0)
    if best < 2:
        return None
    count = max(count for count, score in scores if score >= 0.8 * best)
    return count, length / count


def detect_grid(image: Image.Image) -> tuple[int, int] | None:
    gray = image.convert("L")
    width, height = gray.size
    cols = _grid_count(_edge_profile(gray, vertical=True), width)
    rows = _grid_count(_edge_profile(gray, vertical=False), height)
    if cols is None or rows is None or abs(cols[1] / rows[1] - 1) > 0.1:
        return None
    return cols[0], rows[0]


def grid_from_name(name: str, width: int, height: int) -> tuple[int, int] | None:
    match = GRID_IN_NAME.search(name)
    if match is None:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if first == 0 or second == 0:
        return None
    straight = abs((width / first) / (height / second) - 1)
    swapped = abs((width / second) / (height / first) - 1)
    return (first, second) if straight <= swapped else (second, first)


def encode_map(data: bytes, name: str, grid: tuple[int, int] | None = None) -> EncodedMap:
    if len(data) > MAX_SOURCE_BYTES:
        raise BattleError("image too large")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.draft("RGB", (MAP_LONG_SIDE, MAP_LONG_SIDE))
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise BattleError("unreadable image") from exc
    image.thumbnail((MAP_LONG_SIDE, MAP_LONG_SIDE), Image.Resampling.LANCZOS)
    grid = grid or grid_from_name(name, *image.size) or detect_grid(image)
    if grid is None:
        raise BattleError("grid not found")
    thumbnail = image.copy()
    thumbnail.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
    return EncodedMap(_webp(image, MAP_QUALITY), _webp(thumbnail, THUMBNAIL_QUALITY), *grid)


def import_map(
    db: Session, title: str, data: bytes, filename: str, grid: tuple[int, int] | None = None
) -> tuple[BattleMap, str]:
    source_sha = hashlib.sha256(data).hexdigest()
    battle_map = db.scalar(
        select(BattleMap).where(or_(BattleMap.source_sha256 == source_sha, BattleMap.name == title))
    )
    if (
        battle_map is not None
        and battle_map.source_sha256 == source_sha
        and battle_map.name == title
        and battle_map.thumbnail is not None
    ):
        return battle_map, "unchanged"
    known_grid = grid
    if battle_map is not None and battle_map.source_sha256 == source_sha:
        known_grid = (battle_map.cols, battle_map.rows)
    encoded = encode_map(data, filename, known_grid)
    status = "updated"
    if battle_map is None:
        battle_map = BattleMap()
        db.add(battle_map)
        status = "created"
    battle_map.name = title
    battle_map.cols = encoded.cols
    battle_map.rows = encoded.rows
    battle_map.image = encoded.image
    battle_map.image_type = "image/webp"
    battle_map.image_sha256 = hashlib.sha256(encoded.image).hexdigest()
    battle_map.thumbnail = encoded.thumbnail
    battle_map.source_sha256 = source_sha
    db.commit()
    return battle_map, status


def import_library(
    db: Session, root: Path, entries: tuple[LibraryEntry, ...] = LIBRARY
) -> list[tuple[LibraryEntry, BattleMap | None, str]]:
    results: list[tuple[LibraryEntry, BattleMap | None, str]] = []
    for entry in entries:
        path = root / entry.path
        if not path.is_file():
            results.append((entry, None, "missing"))
            continue
        try:
            battle_map, status = import_map(
                db, entry.title, path.read_bytes(), path.name, entry.grid
            )
        except BattleError as exc:
            db.rollback()
            results.append((entry, None, f"error: {exc}"))
            continue
        results.append((entry, battle_map, status))
    return results


def library_size(db: Session) -> int:
    total = db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.length(BattleMap.image)
                    + func.coalesce(func.length(BattleMap.thumbnail), 0)
                ),
                0,
            )
        )
    )
    return int(total or 0)


def list_maps(db: Session) -> list[BattleMap]:
    return list(db.scalars(select(BattleMap).order_by(BattleMap.name)))


def list_battles(db: Session) -> list[Battle]:
    return list(
        db.scalars(
            select(Battle)
            .options(selectinload(Battle.map))
            .order_by(Battle.started_at.desc(), Battle.id.desc())
        )
    )


def active_battle(db: Session) -> Battle | None:
    return db.scalar(
        select(Battle)
        .options(selectinload(Battle.tokens), selectinload(Battle.map))
        .where(Battle.ended_at.is_(None))
        .order_by(Battle.started_at.desc(), Battle.id.desc())
        .limit(1)
    )


def enemy_counts(values: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for color in ENEMY_COLORS:
        try:
            count = int(values.get(color.key, "0") or 0)
        except ValueError:
            count = 0
        counts[color.key] = min(max(count, 0), MAX_ENEMIES_PER_COLOR)
    return counts


def default_enemies(counts: dict[str, int]) -> list[tuple[TokenColor, str]]:
    return [
        (color, f"{color.label} {number}")
        for color in ENEMY_COLORS
        for number in range(1, counts.get(color.key, 0) + 1)
    ]


def _unique(wanted: str, taken: set[str], limit: int = LABEL_LENGTH) -> str:
    candidate, number = wanted, 2
    while candidate.casefold() in taken:
        suffix = f" {number}"
        candidate = wanted[: limit - len(suffix)] + suffix
        number += 1
    taken.add(candidate.casefold())
    return candidate


def default_battle_name(db: Session) -> str:
    taken = {name.casefold() for name in db.scalars(select(Battle.name))}
    number = len(taken) + 1
    while f"combat {number}" in taken:
        number += 1
    return f"Combat {number}"


def _find_character(db: Session, names: tuple[str, ...]) -> Character | None:
    for character in db.scalars(select(Character).order_by(Character.id)):
        if character.name.casefold().startswith(names):
            return character
    return None


def create_battle(
    db: Session, battle_map: BattleMap, name: str, enemies: list[tuple[str, str]]
) -> Battle:
    if len(enemies) > MAX_ENEMIES_PER_COLOR * len(ENEMY_COLORS):
        raise BattleError("too many enemies")
    name = " ".join(name.split())[:NAME_LENGTH] or default_battle_name(db)
    if db.scalar(select(Battle.id).where(func.lower(Battle.name) == name.casefold())):
        names = {value.casefold() for value in db.scalars(select(Battle.name))}
        name = _unique(name, names, NAME_LENGTH)
    battle = Battle(name=name, map_id=battle_map.id)
    db.add(battle)
    taken: set[str] = set()
    for member in PARTY:
        character = _find_character(db, member.character_names)
        battle.tokens.append(
            BattleToken(
                label=_unique(member.label, taken),
                color=member.color,
                size="medium",
                character_id=character.id if character else None,
                position=len(battle.tokens),
            )
        )
    for color_key, label in enemies:
        color = ENEMY_COLOR_BY_KEY.get(color_key)
        if color is None:
            raise BattleError("unknown colour")
        label = " ".join(label.split())[:LABEL_LENGTH] or color.label
        battle.tokens.append(
            BattleToken(
                label=_unique(label, taken),
                color=color.color,
                size="medium",
                position=len(battle.tokens),
            )
        )
    db.commit()
    return battle


def end_battle(battle: Battle) -> None:
    battle.ended_at = utcnow()


def resume_battle(battle: Battle) -> None:
    battle.ended_at = None
    battle.started_at = utcnow()


def player_token(db: Session, player: User) -> BattleToken | None:
    battle = active_battle(db)
    if battle is None:
        return None
    return db.scalar(
        select(BattleToken)
        .join(Character, Character.id == BattleToken.character_id)
        .where(BattleToken.battle_id == battle.id, Character.player_id == player.id)
        .order_by(BattleToken.position)
        .limit(1)
    )


def _window_side(cells: int) -> int:
    return min(cells, max(PLAYER_WINDOW_MIN, round(cells * PLAYER_WINDOW_SHARE)))


def player_window(battle: Battle, token: BattleToken) -> Window | None:
    if token.col is None or token.row is None or battle.map is None:
        return None
    cols = _window_side(battle.cols)
    rows = _window_side(battle.rows)
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
    if battle.map is None:
        raise BattleError("no map image")
    with Image.open(io.BytesIO(battle.map.image)) as image:
        width, height = image.size
        box = (
            round(window.col * width / battle.cols),
            round(window.row * height / battle.rows),
            round((window.col + window.cols) * width / battle.cols),
            round((window.row + window.rows) * height / battle.rows),
        )
        return _webp(image.convert("RGB").crop(box), 82)
