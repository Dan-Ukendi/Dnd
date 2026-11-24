import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import MediaAsset, MediaRole, MediaVariant, User, WaArticle, utcnow
from app.services.access import accessible_article_ids
from app.services.media_processing import MediaError, ProcessedMedia, process_upload

ROLE_TITLES = {
    MediaRole.PORTRAIT: "Portrait de {title}",
    MediaRole.MAP: "Carte : {title}",
    MediaRole.SHEET: "Fiche de personnage de {title}",
}
MAX_FILENAME_LENGTH = 255
MAX_TITLE_LENGTH = 300

AttachStatus = Literal["created", "replaced", "unchanged"]


@dataclass(frozen=True)
class AttachResult:
    asset: MediaAsset
    status: AttachStatus
    processed: ProcessedMedia | None


def parse_role(value: str) -> MediaRole:
    try:
        return MediaRole(value)
    except ValueError as exc:
        raise MediaError("invalid_role") from exc


def default_title(role: MediaRole, article: WaArticle) -> str:
    return ROLE_TITLES[role].format(title=article.title)[:MAX_TITLE_LENGTH]


def article_asset(db: Session, article_id: str, role: MediaRole) -> MediaAsset | None:
    return db.scalar(
        select(MediaAsset).where(MediaAsset.wa_article_id == article_id, MediaAsset.role == role)
    )


def _clean_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(char for char in name if char.isprintable())
    return name[:MAX_FILENAME_LENGTH] or "fichier"


def attach_media(
    db: Session,
    *,
    article: WaArticle,
    role: MediaRole,
    data: bytes,
    filename: str,
    title: str | None = None,
) -> AttachResult:
    digest = hashlib.sha256(data).hexdigest()
    title = (title or default_title(role, article)).strip()[:MAX_TITLE_LENGTH]
    asset = article_asset(db, article.wa_id, role)
    if asset is not None and asset.sha256 == digest:
        if asset.title != title:
            asset.title = title
            db.commit()
        return AttachResult(asset, "unchanged", None)
    processed = process_upload(role, data)
    status: AttachStatus = "replaced"
    if asset is None:
        status = "created"
        asset = MediaAsset(wa_article_id=article.wa_id, role=role)
        db.add(asset)
    else:
        db.execute(delete(MediaVariant).where(MediaVariant.asset_id == asset.id))
    full = processed.full
    asset.title = title
    asset.original_filename = _clean_filename(filename)
    asset.content_type = full.content_type
    asset.width = full.width
    asset.height = full.height
    asset.size = len(data)
    asset.sha256 = digest
    asset.updated_at = utcnow()
    db.flush()
    for variant in processed.variants:
        db.add(
            MediaVariant(
                asset_id=asset.id,
                name=variant.name,
                content_type=variant.content_type,
                data=variant.data,
                gzip_data=variant.gzip_data,
                size=len(variant.data),
                width=variant.width,
                height=variant.height,
                etag=variant.etag,
            )
        )
    db.commit()
    return AttachResult(asset, status, processed)


def set_banner(db: Session, asset: MediaAsset, section: str | None) -> None:
    if asset.banner_section == section:
        return
    if section is not None:
        db.execute(
            update(MediaAsset)
            .where(MediaAsset.banner_section == section, MediaAsset.id != asset.id)
            .values(banner_section=None)
        )
        db.flush()
    asset.banner_section = section
    db.commit()


def remove_media(db: Session, asset: MediaAsset) -> None:
    db.delete(asset)
    db.commit()


def section_banner(db: Session, section: str) -> MediaAsset | None:
    if not section:
        return None
    return db.scalar(select(MediaAsset).where(MediaAsset.banner_section == section))


def player_section_banner(db: Session, player: User, section: str) -> MediaAsset | None:
    banner = section_banner(db, section)
    if banner is None or banner.wa_article_id not in accessible_article_ids(db, player):
        return None
    return banner


def asset_for_user(db: Session, user: User, asset_id: int) -> MediaAsset | None:
    asset = db.get(MediaAsset, asset_id)
    if asset is None or user.is_dm:
        return asset
    if asset.wa_article_id is None or asset.wa_article_id not in accessible_article_ids(db, user):
        return None
    return asset


def load_variant(db: Session, asset: MediaAsset, name: str) -> MediaVariant | None:
    variant = db.get(MediaVariant, (asset.id, name))
    if variant is None and name == "thumb":
        variant = db.get(MediaVariant, (asset.id, "full"))
    return variant


def _normalize_title(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def find_article(db: Session, *, wa_id: str | None = None, title: str | None = None) -> WaArticle:
    if wa_id:
        article = db.get(WaArticle, wa_id)
        if article is None or article.removed_at is not None:
            raise LookupError(f"article {wa_id} not found")
        return article
    if not title:
        raise LookupError("no article given")
    wanted = _normalize_title(title)
    matches = [
        article
        for article in db.scalars(select(WaArticle).where(WaArticle.removed_at.is_(None)))
        if _normalize_title(article.title) == wanted
    ]
    if not matches:
        raise LookupError(f'article "{title}" not found')
    if len(matches) > 1:
        raise LookupError(f'several articles are titled "{title}"')
    return matches[0]
