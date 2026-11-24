import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Character, Role, User
from app.sections import SECTIONS_BY_KEY
from app.services.accounts import (
    AccountError,
    create_user,
    normalize_login,
    set_temporary_password,
)
from app.services.characters import CharacterError, create_character
from app.services.media import attach_media, find_article, parse_role, set_banner
from app.services.media_processing import MAX_UPLOAD_BYTES, MediaError, ProcessedMedia

DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "media-manifest.toml"


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class ImportLine:
    status: str
    label: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "failed"


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    for key in ("media", "players", "characters"):
        entries = data.get(key, [])
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise ManifestError(f"'{key}' must be an array of tables")
    return data


def _text(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _describe_sizes(before: int, processed: ProcessedMedia) -> str:
    variants = ", ".join(
        f"{variant.name} {len(variant.data)} B"
        + (f" {variant.width}x{variant.height}" if variant.width and variant.height else "")
        for variant in processed.variants
    )
    return f"{before} B -> {variants}"


def import_media(db: Session, folder: Path, manifest: dict[str, Any]) -> list[ImportLine]:
    lines: list[ImportLine] = []
    for entry in manifest.get("media", []):
        filename = _text(entry, "file") or "?"
        label = f"{entry.get('role', '?')} {filename}"
        try:
            role = parse_role(str(entry.get("role", "")))
            path = folder / filename
            if not path.is_file():
                raise LookupError(f"file not found in {folder}")
            if path.stat().st_size > MAX_UPLOAD_BYTES:
                raise MediaError("file_too_large")
            banner = _text(entry, "banner")
            if banner is not None and banner not in SECTIONS_BY_KEY:
                raise LookupError(f"unknown section '{banner}'")
            article = find_article(
                db, wa_id=_text(entry, "article_id"), title=_text(entry, "article")
            )
            data = path.read_bytes()
            result = attach_media(
                db,
                article=article,
                role=role,
                data=data,
                filename=filename,
                title=_text(entry, "title"),
            )
            set_banner(db, result.asset, banner)
        except MediaError as exc:
            db.rollback()
            lines.append(ImportLine("failed", label, exc.code))
            continue
        except LookupError as exc:
            db.rollback()
            lines.append(ImportLine("failed", label, str(exc)))
            continue
        detail = f"-> {article.title}"
        if result.processed is not None:
            detail += f" ({_describe_sizes(len(data), result.processed)})"
        if banner:
            detail += f" [banner: {banner}]"
        lines.append(ImportLine(result.status, label, detail))
    return lines


def import_characters(db: Session, manifest: dict[str, Any]) -> list[ImportLine]:
    lines: list[ImportLine] = []
    for entry in manifest.get("players", []):
        login = normalize_login(_text(entry, "login") or "")
        label = f"player {login or '?'}"
        if db.scalar(select(User.id).where(User.login == login)) is not None:
            lines.append(ImportLine("unchanged", label, "already exists"))
            continue
        try:
            user = create_user(
                db, display_name=_text(entry, "display_name") or "", login=login, role=Role.PLAYER
            )
        except AccountError as exc:
            db.rollback()
            lines.append(ImportLine("failed", label, exc.code))
            continue
        set_temporary_password(db, user, get_settings().player_initial_password.get_secret_value())
        lines.append(ImportLine("created", label, f"{user.display_name} (temporary password)"))
    for entry in manifest.get("characters", []):
        name = _text(entry, "name") or ""
        label = f"character {name or '?'}"
        login = normalize_login(_text(entry, "player") or "")
        player = db.scalar(select(User).where(User.login == login, User.role == Role.PLAYER))
        if player is None:
            lines.append(ImportLine("failed", label, f"player '{login}' not found"))
            continue
        existing = db.scalar(
            select(Character).where(Character.name == name, Character.player_id == player.id)
        )
        if existing is not None:
            lines.append(ImportLine("unchanged", label, "already exists"))
            continue
        try:
            article = find_article(
                db, wa_id=_text(entry, "article_id"), title=_text(entry, "article")
            )
            create_character(db, name=name, player_id=player.id, main_article_id=article.wa_id)
        except LookupError as exc:
            lines.append(ImportLine("failed", label, str(exc)))
            continue
        except CharacterError as exc:
            db.rollback()
            lines.append(ImportLine("failed", label, exc.code))
            continue
        lines.append(ImportLine("created", label, f"{player.display_name} -> {article.title}"))
    return lines
