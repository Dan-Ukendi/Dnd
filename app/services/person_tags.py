from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models import (
    PERSON_CATEGORIES,
    ArticleCategory,
    Character,
    CharacterRelation,
    Npc,
    User,
    WaArticle,
)
from app.presentation import article_image, natural_order
from app.sections import article_section_key, section_key
from app.services.access import accessible_article_ids
from app.services.characters import characters_of
from app.services.dossiers import mark_relations_edited

PERSON_SECTION = "personnage"
DEFAULT_CATEGORY = "encounter"
CHARACTER_CATEGORY = "character"
LINKS_TAG = "liens"
LINK_CATEGORY = "autres"
CATEGORY_LABELS = {"lore": "Lore", "character": "Character", "encounter": "Rencontre campagne"}
TAG_ORDER = ("lore", "character", LINKS_TAG, "encounter")
TAG_LABELS = {**CATEGORY_LABELS, LINKS_TAG: "Liens"}
PLAYER_TAGS = ("character", LINKS_TAG)
DM_DESCRIPTIONS = {
    "lore": "Les figures de l'histoire du monde et les personnes très connues.",
    "character": "Les fiches des personnages joués du groupe.",
    LINKS_TAG: "Les personnes rattachées à la backstory d'un personnage joué.",
    "encounter": "Les personnages rencontrés au fil de la campagne.",
}
PLAYER_DESCRIPTIONS = {
    "character": "La fiche de votre personnage.",
    LINKS_TAG: "Les personnes liées à l'histoire de votre personnage.",
}
MAX_PREVIEWS = 4


class PersonTagError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Badge:
    label: str
    kind: str


@dataclass(frozen=True)
class Preview:
    image: str | None
    initial: str


@dataclass(frozen=True)
class PersonEntry:
    name: str
    article: WaArticle | None
    href: str | None
    role: str | None
    badges: list[Badge]
    image: str | None


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    description: str
    count: int
    previews: list[Preview]
    href: str


@dataclass(frozen=True)
class LinkGroup:
    character: Character
    entries: list[PersonEntry]
    previews: list[Preview]
    href: str


@dataclass(frozen=True)
class LinkChoice:
    character: Character
    checked: bool
    locked: bool


@dataclass(frozen=True)
class FicheTags:
    category: str
    choices: list[LinkChoice]
    options: list[tuple[str, str]]

    @property
    def links_enabled(self) -> bool:
        return self.category != CHARACTER_CATEGORY


def is_person(article: WaArticle) -> bool:
    return article_section_key(article) == PERSON_SECTION


def category_badge(category: str) -> Badge:
    return Badge(CATEGORY_LABELS[category], category)


def link_badge(character: Character) -> Badge:
    return Badge(f"Lien {character.name}", "lien")


def category_map(db: Session) -> dict[str, str]:
    return {row.wa_id: row.category for row in db.scalars(select(ArticleCategory))}


def category_of(categories: dict[str, str], wa_id: str) -> str:
    return categories.get(wa_id, DEFAULT_CATEGORY)


def person_articles(db: Session) -> list[WaArticle]:
    articles = db.scalars(select(WaArticle).where(WaArticle.removed_at.is_(None))).all()
    return natural_order(article for article in articles if is_person(article))


def ensure_categories(db: Session) -> int:
    rows = db.execute(select(WaArticle.wa_id, WaArticle.template_type)).all()
    values = [
        {"wa_id": row.wa_id, "category": DEFAULT_CATEGORY}
        for row in rows
        if section_key(row.wa_id, row.template_type) == PERSON_SECTION
    ]
    if not values:
        return 0
    result = db.execute(
        insert(ArticleCategory).values(values).on_conflict_do_nothing(index_elements=["wa_id"])
    )
    return result.rowcount or 0


def _all_characters(db: Session) -> list[Character]:
    return list(
        db.scalars(
            select(Character).options(selectinload(Character.main_article)).order_by(Character.name)
        ).all()
    )


def _links(
    db: Session, characters: Iterable[Character], categories: dict[str, str]
) -> dict[int, list[tuple[WaArticle, list[CharacterRelation]]]]:
    by_id = {character.id: character for character in characters}
    links: dict[int, dict[str, tuple[WaArticle, list[CharacterRelation]]]] = {
        character_id: {} for character_id in by_id
    }
    if not by_id:
        return {}
    rows = db.execute(
        select(CharacterRelation, WaArticle)
        .join(WaArticle, WaArticle.wa_id == CharacterRelation.wa_article_id)
        .where(CharacterRelation.character_id.in_(by_id), WaArticle.removed_at.is_(None))
        .order_by(CharacterRelation.character_id, CharacterRelation.position)
    ).all()
    for relation, article in rows:
        if not is_person(article) or category_of(categories, article.wa_id) == CHARACTER_CATEGORY:
            continue
        found = links[relation.character_id].setdefault(article.wa_id, (article, []))
        found[1].append(relation)
    return {
        character_id: [
            entries[article.wa_id] for article in natural_order(a for a, _ in entries.values())
        ]
        for character_id, entries in links.items()
    }


def _previews(entries: Iterable[PersonEntry]) -> list[Preview]:
    ranked = sorted(entries, key=lambda entry: entry.image is None)
    return [
        Preview(entry.image, (entry.name.strip()[:1] or "?").upper())
        for entry in ranked[:MAX_PREVIEWS]
    ]


GM_BADGE = Badge("MJ seulement", "muted")


def app_npc_entries(db: Session, category: str, query: str = "") -> list[PersonEntry]:
    needle = query.strip().casefold()
    rows = db.scalars(
        select(Npc).where(Npc.category == category, Npc.wa_article_id.is_(None)).order_by(Npc.name)
    )
    entries = []
    for npc in rows:
        text = " ".join((npc.name, npc.title, npc.race, npc.description)).casefold()
        if needle and needle not in text:
            continue
        badges = [category_badge(category)]
        if npc.visibility == "mj":
            badges.append(GM_BADGE)
        entries.append(
            PersonEntry(npc.name, None, f"/mj/pnj/{npc.key}", npc.title or None, badges, None)
        )
    return entries


def dm_badge_index(db: Session) -> dict[str, list[Badge]]:
    categories = category_map(db)
    index = {
        article.wa_id: [category_badge(category_of(categories, article.wa_id))]
        for article in person_articles(db)
    }
    characters = _all_characters(db)
    links = _links(db, characters, categories)
    for character in characters:
        for article, _ in links.get(character.id, []):
            index.setdefault(article.wa_id, []).append(link_badge(character))
    return index


def player_badge_index(db: Session, player: User) -> dict[str, list[Badge]]:
    categories = category_map(db)
    characters = characters_of(db, player)
    index: dict[str, list[Badge]] = {}
    for character in characters:
        main = character.main_article
        if main is not None and category_of(categories, main.wa_id) == CHARACTER_CATEGORY:
            index.setdefault(main.wa_id, []).append(category_badge(CHARACTER_CATEGORY))
    links = _links(db, characters, categories)
    for character in characters:
        for article, _ in links.get(character.id, []):
            index.setdefault(article.wa_id, []).append(link_badge(character))
    return index


def _dm_entry(article: WaArticle, badges: dict[str, list[Badge]]) -> PersonEntry:
    return PersonEntry(
        article.title,
        article,
        f"/mj/articles/{article.wa_id}",
        None,
        badges.get(article.wa_id, []),
        article_image(article),
    )


def dm_articles_in(db: Session, articles: Iterable[WaArticle], category: str) -> list[WaArticle]:
    categories = category_map(db)
    return [
        article
        for article in articles
        if is_person(article) and category_of(categories, article.wa_id) == category
    ]


def dm_link_groups(db: Session, badges: dict[str, list[Badge]] | None = None) -> list[LinkGroup]:
    badges = dm_badge_index(db) if badges is None else badges
    characters = _all_characters(db)
    links = _links(db, characters, category_map(db))
    groups = []
    for character in characters:
        entries = [_dm_entry(article, badges) for article, _ in links.get(character.id, [])]
        groups.append(
            LinkGroup(
                character,
                entries,
                _previews(entries),
                f"/mj/articles?section={PERSON_SECTION}&tag={LINKS_TAG}&pj={character.id}",
            )
        )
    return groups


def dm_tiles(db: Session) -> list[Tile]:
    categories = category_map(db)
    badges = dm_badge_index(db)
    entries: dict[str, list[PersonEntry]] = {category: [] for category in PERSON_CATEGORIES}
    for article in person_articles(db):
        entries[category_of(categories, article.wa_id)].append(_dm_entry(article, badges))
    for category in PERSON_CATEGORIES:
        entries[category].extend(app_npc_entries(db, category))
    linked: dict[str, PersonEntry] = {}
    for group in dm_link_groups(db, badges):
        for entry in group.entries:
            if entry.article is not None:
                linked.setdefault(entry.article.wa_id, entry)
    entries[LINKS_TAG] = list(linked.values())
    return [
        Tile(
            key,
            TAG_LABELS[key],
            DM_DESCRIPTIONS[key],
            len(entries[key]),
            _previews(entries[key]),
            f"/mj/articles?section={PERSON_SECTION}&tag={key}",
        )
        for key in TAG_ORDER
    ]


@dataclass(frozen=True)
class PlayerPersonView:
    characters: list[PersonEntry]
    groups: list[LinkGroup]

    def tiles(self) -> list[Tile]:
        linked = [entry for group in self.groups for entry in group.entries]
        entries = {"character": self.characters, LINKS_TAG: linked}
        return [
            Tile(
                key,
                TAG_LABELS[key],
                PLAYER_DESCRIPTIONS[key],
                len(entries[key]),
                _previews(entries[key]),
                f"/joueur/articles?section={PERSON_SECTION}&tag={key}",
            )
            for key in PLAYER_TAGS
        ]

    def group(self, character_id: int) -> LinkGroup | None:
        return next((group for group in self.groups if group.character.id == character_id), None)


def player_view(db: Session, player: User) -> PlayerPersonView:
    accessible = accessible_article_ids(db, player)
    categories = category_map(db)
    characters = characters_of(db, player)
    own: list[PersonEntry] = []
    for character in characters:
        main = character.main_article
        if (
            main is None
            or main.removed_at is not None
            or category_of(categories, main.wa_id) != CHARACTER_CATEGORY
        ):
            continue
        shared = main.wa_id in accessible
        own.append(
            PersonEntry(
                main.title if shared else character.name,
                main if shared else None,
                f"/joueur/articles/{main.wa_id}" if shared else "/joueur",
                None,
                [category_badge(CHARACTER_CATEGORY)],
                article_image(main, True) if shared else None,
            )
        )
    links = _links(db, characters, categories)
    groups = []
    for character in characters:
        entries = []
        for article, relations in links.get(character.id, []):
            shared = article.wa_id in accessible
            entries.append(
                PersonEntry(
                    article.title if shared else relations[0].name,
                    article if shared else None,
                    f"/joueur/articles/{article.wa_id}" if shared else None,
                    relations[0].role,
                    [link_badge(character)],
                    article_image(article, True) if shared else None,
                )
            )
        groups.append(
            LinkGroup(
                character,
                entries,
                _previews(entries),
                f"/joueur/articles?section={PERSON_SECTION}&tag={LINKS_TAG}&pj={character.id}",
            )
        )
    return PlayerPersonView(own, groups)


def _is_minimal(relation: CharacterRelation) -> bool:
    return not any(
        (value or "").strip()
        for value in (relation.role, relation.status, relation.text, relation.gm_notes)
    )


def _link_candidates(db: Session, article: WaArticle) -> list[Character]:
    return [
        character for character in _all_characters(db) if character.main_article_id != article.wa_id
    ]


def fiche_tags(db: Session, article: WaArticle) -> FicheTags | None:
    if not is_person(article):
        return None
    choices = []
    for character in _link_candidates(db, article):
        relations = [r for r in character.relations if r.wa_article_id == article.wa_id]
        choices.append(
            LinkChoice(
                character,
                bool(relations),
                any(not _is_minimal(relation) for relation in relations),
            )
        )
    return FicheTags(
        category_of(category_map(db), article.wa_id),
        choices,
        list(CATEGORY_LABELS.items()),
    )


def set_category(db: Session, article: WaArticle, category: str) -> None:
    if not is_person(article):
        raise PersonTagError("article")
    if category not in PERSON_CATEGORIES:
        raise PersonTagError("category")
    row = db.get(ArticleCategory, article.wa_id)
    if row is None:
        db.add(ArticleCategory(wa_id=article.wa_id, category=category))
    else:
        row.category = category


def _set_links(db: Session, article: WaArticle, wanted: set[int]) -> None:
    for character in _link_candidates(db, article):
        relations = [r for r in character.relations if r.wa_article_id == article.wa_id]
        if character.id in wanted and not relations:
            position = max((r.position for r in character.relations), default=-1) + 1
            character.relations.append(
                CharacterRelation(
                    position=position,
                    category=LINK_CATEGORY,
                    name=article.title[:200],
                    wa_article_id=article.wa_id,
                    text="",
                    gm_notes="",
                )
            )
            mark_relations_edited(character)
        elif (
            character.id not in wanted
            and relations
            and all(_is_minimal(relation) for relation in relations)
        ):
            for relation in relations:
                character.relations.remove(relation)
            mark_relations_edited(character)


def update_fiche(db: Session, article: WaArticle, category: str, wanted: set[int]) -> None:
    set_category(db, article, category)
    if category != CHARACTER_CATEGORY:
        _set_links(db, article, wanted)
    db.commit()
