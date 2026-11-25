import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Artefact,
    CharacterProfile,
    LinkAlias,
    LinkExclusion,
    SyncRun,
    SyncStatus,
    WaArticle,
)
from app.presentation import article_tagline
from app.sections import section_key
from app.worldanvil.linking import MIN_NAME_LENGTH, Matcher, Name, Target, name_key

logger = logging.getLogger("app.linking")

ITEM_TEMPLATE = "item"
KINDS = {
    "personnage": ("personnage", "Personnage"),
    "monde": ("lieu", "Lieu"),
    "organisation": ("organisation", "Organisation"),
    "lore": ("lore", "Lore"),
    "aventure": ("aventure", "Aventure"),
}
OBJECT_KIND = ("objet", "Objet")
MAX_TERM_LENGTH = 200

_lock = threading.Lock()
_cache: tuple[tuple[object, ...], Matcher] | None = None


class LinkError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def kind_of(article: WaArticle) -> tuple[str, str]:
    if (article.template_type or "").lower() == ITEM_TEMPLATE:
        return OBJECT_KIND
    return KINDS[section_key(article.wa_id, article.template_type)]


def dossier_alias_keys(db: Session) -> set[str]:
    keys: set[str] = set()
    for aliases in db.scalars(select(CharacterProfile.aliases)):
        for alias in aliases or []:
            if isinstance(alias, dict) and alias.get("name"):
                keys.add(name_key(str(alias["name"]))[0])
    return keys


def _fingerprint(db: Session) -> tuple[object, ...]:
    statements = (
        select(func.count()).select_from(WaArticle),
        select(func.max(WaArticle.synced_at)),
        select(func.count(WaArticle.removed_at)),
        select(func.max(SyncRun.id)).where(SyncRun.status == SyncStatus.SUCCESS),
        select(func.count()).select_from(LinkAlias),
        select(func.max(LinkAlias.id)),
        select(func.count()).select_from(LinkExclusion),
        select(func.max(LinkExclusion.id)),
        select(func.count()).select_from(CharacterProfile),
        select(func.max(CharacterProfile.updated_at)),
    )
    return tuple(db.execute(select(*(item.scalar_subquery() for item in statements))).one())


def build_matcher(db: Session) -> Matcher:
    articles = db.scalars(select(WaArticle).where(WaArticle.removed_at.is_(None))).all()
    targets = [Target(article.wa_id, article.title, *kind_of(article)) for article in articles]
    names = [Name(article.title, article.wa_id) for article in articles]
    blocked = dossier_alias_keys(db)
    for alias in db.scalars(select(LinkAlias).order_by(LinkAlias.id)):
        if name_key(alias.alias)[0] in blocked:
            logger.warning("Alias %r ignored: it is a character dossier alias", alias.alias)
            continue
        names.append(Name(alias.alias, alias.wa_article_id, is_title=False))
    exclusions = db.scalars(select(LinkExclusion.term)).all()
    matcher = Matcher.build(targets, names, exclusions)
    for key, wa_ids in sorted(matcher.conflicts.items()):
        logger.warning("Name conflict, no automatic link for %r: %s", key, ", ".join(wa_ids))
    return matcher


def matcher(db: Session) -> Matcher:
    global _cache
    fingerprint = _fingerprint(db)
    with _lock:
        if _cache is not None and _cache[0] == fingerprint:
            return _cache[1]
    built = build_matcher(db)
    with _lock:
        _cache = (fingerprint, built)
    return built


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def refresh(db: Session) -> Matcher:
    invalidate()
    return matcher(db)


def preview_index(db: Session, accessible: set[str] | None) -> dict[str, Any]:
    query = select(WaArticle).where(WaArticle.removed_at.is_(None)).order_by(WaArticle.wa_id)
    if accessible is not None:
        if not accessible:
            return {"fiches": {}}
        query = query.where(WaArticle.wa_id.in_(accessible))
    articles = db.scalars(query).all()
    taglines = dict(
        db.execute(
            select(Artefact.wa_article_id, Artefact.tagline).where(
                Artefact.wa_article_id.in_([article.wa_id for article in articles])
            )
        ).all()
    )
    fiches = {}
    for article in articles:
        kind, label = kind_of(article)
        tagline = (
            taglines.get(article.wa_id) if article.wa_id in taglines else article_tagline(article)
        )
        fiches[article.wa_id] = {"kind": kind, "type": label, "title": article.title}
        if tagline:
            fiches[article.wa_id]["excerpt"] = tagline
    return {"fiches": fiches}


def preview_payload(db: Session, accessible: set[str] | None) -> tuple[bytes, str]:
    body = json.dumps(
        preview_index(db, accessible), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return body, f'"{hashlib.sha256(body).hexdigest()[:32]}"'


@dataclass(frozen=True)
class Conflict:
    name: str
    articles: list[WaArticle]


def conflicts(db: Session, current: Matcher) -> list[Conflict]:
    ids = {wa_id for wa_ids in current.conflicts.values() for wa_id in wa_ids}
    found = (
        {
            article.wa_id: article
            for article in db.scalars(select(WaArticle).where(WaArticle.wa_id.in_(ids)))
        }
        if ids
        else {}
    )
    return [
        Conflict(key, [found[wa_id] for wa_id in wa_ids if wa_id in found])
        for key, wa_ids in sorted(current.conflicts.items())
    ]


def list_aliases(db: Session) -> list[LinkAlias]:
    return list(
        db.scalars(
            select(LinkAlias)
            .join(WaArticle, WaArticle.wa_id == LinkAlias.wa_article_id)
            .order_by(func.lower(LinkAlias.alias), WaArticle.title)
        ).all()
    )


def list_exclusions(db: Session) -> list[LinkExclusion]:
    return list(db.scalars(select(LinkExclusion).order_by(func.lower(LinkExclusion.term))).all())


def _clean_term(value: str) -> str:
    term = " ".join(value.split())
    if len(name_key(term)[0]) < MIN_NAME_LENGTH or len(term) > MAX_TERM_LENGTH:
        raise LinkError("term")
    return term


def add_alias(db: Session, alias: str, article_title: str) -> LinkAlias:
    term = _clean_term(alias)
    title = article_title.strip()
    article = db.scalar(
        select(WaArticle).where(WaArticle.title == title, WaArticle.removed_at.is_(None))
    )
    if article is None:
        raise LinkError("article")
    key = name_key(term)[0]
    if key in dossier_alias_keys(db):
        raise LinkError("dossier_alias")
    existing = db.scalars(select(LinkAlias).where(LinkAlias.wa_article_id == article.wa_id))
    if key == name_key(article.title)[0] or any(name_key(row.alias)[0] == key for row in existing):
        raise LinkError("duplicate")
    row = LinkAlias(alias=term, wa_article_id=article.wa_id)
    db.add(row)
    db.commit()
    invalidate()
    return row


def add_exclusion(db: Session, value: str) -> LinkExclusion:
    term = _clean_term(value)
    key = name_key(term)[0]
    if any(name_key(row.term)[0] == key for row in list_exclusions(db)):
        raise LinkError("duplicate")
    row = LinkExclusion(term=term)
    db.add(row)
    db.commit()
    invalidate()
    return row


def delete_alias(db: Session, alias: LinkAlias) -> None:
    db.delete(alias)
    db.commit()
    invalidate()


def delete_exclusion(db: Session, exclusion: LinkExclusion) -> None:
    db.delete(exclusion)
    db.commit()
    invalidate()
