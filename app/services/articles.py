from dataclasses import dataclass
from typing import Any

from sqlalchemy import any_, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models import WaArticle

SEARCH_LIMIT = 200

DM_EXTRA_FIELDS: dict[str, str] = {
    "subheading": "Sous-titre",
    "pronunciation": "Prononciation",
    "excerpt": "Extrait",
    "snippet": "Aperçu au survol",
    "seeded": "Graines du conteur (MJ)",
    "sidebarcontent": "Barre latérale : haut",
    "sidepanelcontenttop": "Panneau latéral : haut",
    "sidepanelcontent": "Panneau latéral : bas",
    "sidebarcontentbottom": "Barre latérale : bas",
    "footnotes": "Notes de bas de page",
    "fullfooter": "Pied de page",
    "authornotes": "Notes de l'auteur",
    "scrapbook": "Album",
    "credits": "Crédits",
}
TECHNICAL_FIELDS = frozenset(
    {
        "id",
        "title",
        "slug",
        "state",
        "entityClass",
        "icon",
        "url",
        "folderId",
        "tags",
        "templateType",
        "customArticleTemplate",
        "editor",
        "content",
        "cssClasses",
        "displayCss",
        "coverSource",
        "editURL",
        "metaTitle",
        "metaDescription",
        "metadDescription",
        "commentPlaceholder",
        "badge",
        "articleMetadata",
        "userMetadata",
    }
)


@dataclass(frozen=True)
class ExtraField:
    key: str
    label: str
    value: str


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_articles(
    db: Session, *, query: str = "", template_type: str = "", limit: int = SEARCH_LIMIT
) -> list[WaArticle]:
    statement = select(WaArticle).where(WaArticle.removed_at.is_(None))
    if template_type:
        statement = statement.where(WaArticle.template_type == template_type)
    query = query.strip()[:200]
    if query:
        tsquery = func.websearch_to_tsquery("french", query)
        statement = statement.where(
            or_(
                WaArticle.search_vector.op("@@")(tsquery),
                WaArticle.title.ilike(f"%{_escape_like(query)}%", escape="\\"),
            )
        ).order_by(func.ts_rank(WaArticle.search_vector, tsquery).desc(), WaArticle.title)
    else:
        statement = statement.order_by(WaArticle.title)
    return list(db.scalars(statement.limit(limit)).all())


def template_type_counts(db: Session) -> list[tuple[str, int]]:
    rows = db.execute(
        select(WaArticle.template_type, func.count())
        .where(WaArticle.removed_at.is_(None), WaArticle.template_type.is_not(None))
        .group_by(WaArticle.template_type)
        .order_by(WaArticle.template_type)
    ).all()
    return [(row[0], row[1]) for row in rows]


def all_available_articles(db: Session) -> list[WaArticle]:
    return list(
        db.scalars(
            select(WaArticle).where(WaArticle.removed_at.is_(None)).order_by(WaArticle.title)
        ).all()
    )


def articles_by_ids(db: Session, ids: list[str] | set[str]) -> list[WaArticle]:
    if not ids:
        return []
    return list(
        db.scalars(
            select(WaArticle)
            .where(WaArticle.wa_id.in_(list(ids)), WaArticle.removed_at.is_(None))
            .order_by(WaArticle.title)
        ).all()
    )


def backlinks(db: Session, wa_id: str) -> list[WaArticle]:
    return list(
        db.scalars(
            select(WaArticle)
            .where(literal(wa_id) == any_(WaArticle.linked_ids), WaArticle.removed_at.is_(None))
            .order_by(WaArticle.title)
        ).all()
    )


def dm_extra_fields(raw: dict[str, Any]) -> list[ExtraField]:
    fields: list[ExtraField] = []
    for key, label in DM_EXTRA_FIELDS.items():
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            fields.append(ExtraField(key, label, value))
    for key, value in raw.items():
        if key in DM_EXTRA_FIELDS or key in TECHNICAL_FIELDS:
            continue
        if isinstance(value, str) and value.strip():
            fields.append(ExtraField(key, key, value))
    return fields


def attached_secrets(raw: dict[str, Any]) -> list[str]:
    secrets = raw.get("secrets")
    if not isinstance(secrets, list):
        return []
    return [str(item.get("title") or item.get("id")) for item in secrets if isinstance(item, dict)]
