from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.branding import PARTY_DESCRIPTION, PARTY_NAME
from app.models import WaArticle, WorldCategory


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    description: str


ADVENTURE_KEY = "aventure"
WORLD_KEY = "monde"

SECTIONS: tuple[Section, ...] = (
    Section(ADVENTURE_KEY, PARTY_NAME, PARTY_DESCRIPTION),
    Section(WORLD_KEY, "Monde", "Les pays, les villes et les bâtiments du monde."),
    Section("personnage", "Personnage", "Les personnages joués et ceux qui croisent leur route."),
    Section("lore", "Lore", "L'histoire, les mythes, les artefacts et les légendes du monde."),
    Section(
        "organisation",
        "Organisation",
        "Guildes, clergés, factions, ordres et autres groupes de personnes.",
    ),
)
SECTIONS_BY_KEY = {section.key: section for section in SECTIONS}
DEFAULT_SECTION = "lore"

TEMPLATE_SECTIONS: dict[str, str] = {
    "settlement": WORLD_KEY,
    "landmark": WORLD_KEY,
    "location": WORLD_KEY,
    "person": "personnage",
    "organization": "organisation",
    "profession": "organisation",
    "rank": "organisation",
    "formation": "organisation",
    "report": ADVENTURE_KEY,
    "plot": ADVENTURE_KEY,
}

ARTICLE_SECTION_OVERRIDES: dict[str, str] = {
    "9a0ff719-7202-4be9-a52c-997e5af92751": "lore",
}


def section_key(wa_id: str, template_type: str | None, world_category: str | None = None) -> str:
    if world_category:
        return WORLD_KEY
    override = ARTICLE_SECTION_OVERRIDES.get(wa_id)
    if override in SECTIONS_BY_KEY:
        return override
    return TEMPLATE_SECTIONS.get((template_type or "").lower(), DEFAULT_SECTION)


def article_section_key(article: WaArticle) -> str:
    return section_key(article.wa_id, article.template_type, article.world_category)


def section_of(article: WaArticle) -> Section:
    return SECTIONS_BY_KEY[article_section_key(article)]


def section_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(WaArticle.wa_id, WaArticle.template_type, WorldCategory.category)
        .outerjoin(WorldCategory, WorldCategory.wa_id == WaArticle.wa_id)
        .where(WaArticle.removed_at.is_(None))
    ).all()
    counts = Counter(section_key(row.wa_id, row.template_type, row.category) for row in rows)
    return {section.key: counts.get(section.key, 0) for section in SECTIONS}
