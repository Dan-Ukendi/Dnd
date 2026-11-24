from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WaArticle


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    description: str


SECTIONS: tuple[Section, ...] = (
    Section("monde", "Monde", "Les cités, régions et lieux remarquables du monde."),
    Section("personnage", "Personnage", "Les personnages joués et ceux qui croisent leur route."),
    Section("lore", "Lore", "L'histoire, les mythes, les artefacts et le récit de la campagne."),
    Section(
        "organisation",
        "Organisation",
        "Guildes, clergés, factions, ordres et autres groupes de personnes.",
    ),
)
SECTIONS_BY_KEY = {section.key: section for section in SECTIONS}
DEFAULT_SECTION = "lore"

TEMPLATE_SECTIONS: dict[str, str] = {
    "settlement": "monde",
    "landmark": "monde",
    "location": "monde",
    "person": "personnage",
    "organization": "organisation",
    "profession": "organisation",
    "rank": "organisation",
    "formation": "organisation",
}

ARTICLE_SECTION_OVERRIDES: dict[str, str] = {
    "3936fd24-8259-43ed-a359-24bdbfc6e6e9": "monde",
    "9ef5d01c-1871-4c1d-8520-ba581c056e7e": "monde",
    "ea2b7304-a3e1-41ac-aa67-0ab4462a18fa": "monde",
    "9a0ff719-7202-4be9-a52c-997e5af92751": "lore",
}


def section_key(wa_id: str, template_type: str | None) -> str:
    override = ARTICLE_SECTION_OVERRIDES.get(wa_id)
    if override in SECTIONS_BY_KEY:
        return override
    return TEMPLATE_SECTIONS.get((template_type or "").lower(), DEFAULT_SECTION)


def section_of(article: WaArticle) -> Section:
    return SECTIONS_BY_KEY[section_key(article.wa_id, article.template_type)]


def section_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(WaArticle.wa_id, WaArticle.template_type).where(WaArticle.removed_at.is_(None))
    ).all()
    counts = Counter(section_key(row.wa_id, row.template_type) for row in rows)
    return {section.key: counts.get(section.key, 0) for section in SECTIONS}
