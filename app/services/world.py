import json
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import WORLD_CATEGORIES, Place, WaArticle, WorldCategory
from app.presentation import article_image, natural_order
from app.sections import WORLD_KEY, section_key
from app.services.artefact_split import fold
from app.services.structures import Viewer

COUNTRY, CITY, BUILDING = WORLD_CATEGORIES
CATEGORY_LABELS = {COUNTRY: "Pays", CITY: "Ville", BUILDING: "Bâtiment"}
TILE_LABELS = {COUNTRY: "Pays", CITY: "Villes", BUILDING: "Bâtiments"}
SLUGS = {COUNTRY: "pays", CITY: "villes", BUILDING: "batiments"}
CATEGORY_BY_SLUG = {slug: category for category, slug in SLUGS.items()}
DESCRIPTIONS = {
    COUNTRY: "Les nations et les États du monde.",
    CITY: "Les cités, villes et agglomérations.",
    BUILDING: "Les temples, tavernes, demeures et autres édifices.",
}
EMPTY_LABELS = {COUNTRY: "Aucun pays", CITY: "Aucune ville", BUILDING: "Aucun bâtiment"}
ELIGIBLE_SECTIONS = frozenset({WORLD_KEY, "organisation"})
TEMPLATE_DEFAULTS = {"settlement": CITY, "landmark": BUILDING}
COUNTRY_TEMPLATE = "organization"
COUNTRY_VARIANT = "country"
MAX_PREVIEWS = 4


class WorldCategoryError(Exception):
    pass


def category_label(category: str | None) -> str | None:
    return CATEGORY_LABELS.get(category or "")


def static_section(article: WaArticle) -> str:
    return section_key(article.wa_id, article.template_type)


def is_eligible(article: WaArticle) -> bool:
    return article.world_category_row is not None or static_section(article) in ELIGIBLE_SECTIONS


def _variant(raw: dict | None) -> str:
    metadata = (raw or {}).get("articleMetadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return ""
    if isinstance(metadata, dict) and isinstance(metadata.get("variant"), str):
        return metadata["variant"].strip().lower()
    return ""


def default_category(article: WaArticle) -> str | None:
    if static_section(article) not in ELIGIBLE_SECTIONS:
        return None
    template = (article.template_type or "").lower()
    if template in TEMPLATE_DEFAULTS:
        return TEMPLATE_DEFAULTS[template]
    if template == COUNTRY_TEMPLATE and _variant(article.raw) == COUNTRY_VARIANT:
        return COUNTRY
    return None


def ensure_categories(db: Session) -> int:
    known = set(db.scalars(select(WorldCategory.wa_id)))
    values = []
    for article in db.scalars(select(WaArticle).where(WaArticle.wa_id.not_in(known or {""}))):
        category = default_category(article)
        if category is not None:
            values.append({"wa_id": article.wa_id, "category": category})
    if not values:
        return 0
    result = db.execute(
        insert(WorldCategory).values(values).on_conflict_do_nothing(index_elements=["wa_id"])
    )
    return max(result.rowcount or 0, 0)


def set_category(db: Session, article: WaArticle, category: str) -> None:
    if not is_eligible(article):
        raise WorldCategoryError("article")
    if category and category not in WORLD_CATEGORIES:
        raise WorldCategoryError("category")
    row = db.get(WorldCategory, article.wa_id)
    if row is None:
        db.add(WorldCategory(wa_id=article.wa_id, category=category or None))
    else:
        row.category = category or None
    db.commit()
    db.expire(article, ["world_category_row"])


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    description: str
    count: int
    images: list[str]
    href: str
    empty_label: str


@dataclass(frozen=True)
class Relation:
    article: WaArticle
    href: str | None


@dataclass
class WorldFiche:
    category: str
    label: str
    country: Relation | None = None
    city: Relation | None = None
    cities: list[Relation] = field(default_factory=list)
    buildings: list[Relation] = field(default_factory=list)

    @property
    def tile_label(self) -> str:
        return TILE_LABELS[self.category]

    @property
    def slug(self) -> str:
        return SLUGS[self.category]

    @property
    def children(self) -> list[Relation]:
        return self.cities if self.category == COUNTRY else self.buildings

    @property
    def children_label(self) -> str:
        return TILE_LABELS[CITY] if self.category == COUNTRY else TILE_LABELS[BUILDING]

    @property
    def children_empty(self) -> str:
        return EMPTY_LABELS[CITY] if self.category == COUNTRY else EMPTY_LABELS[BUILDING]

    @property
    def has_children(self) -> bool:
        return self.category in (COUNTRY, CITY)


class _Graph:
    def __init__(self, db: Session, viewer: Viewer) -> None:
        self.viewer = viewer
        self.articles = {
            article.wa_id: article
            for article in db.scalars(
                select(WaArticle)
                .join(WorldCategory, WorldCategory.wa_id == WaArticle.wa_id)
                .where(WorldCategory.category.is_not(None), WaArticle.removed_at.is_(None))
            )
        }
        self.places = {place.id: place for place in db.scalars(select(Place))}
        self.place_of = {
            place.wa_article_id: place for place in self.places.values() if place.wa_article_id
        }
        self.parents = {wa_id: self._parent(article) for wa_id, article in self.articles.items()}

    def _place_visible(self, place: Place) -> bool:
        seen: set[int] = set()
        current: Place | None = place
        while current is not None and current.id not in seen:
            if current.visibility != "joueurs":
                return False
            seen.add(current.id)
            current = self.places.get(current.parent_id) if current.parent_id else None
        return True

    def _parent(self, article: WaArticle) -> tuple[str, bool] | None:
        parent = (article.raw or {}).get("parent")
        if isinstance(parent, dict) and str(parent.get("id")) in self.articles:
            return str(parent["id"]), True
        place = self.place_of.get(article.wa_id)
        seen: set[int] = set()
        current = self.places.get(place.parent_id) if place and place.parent_id else None
        while current is not None and current.id not in seen:
            seen.add(current.id)
            if current.wa_article_id in self.articles and current.wa_article_id != article.wa_id:
                return current.wa_article_id, self._place_visible(place)
            current = self.places.get(current.parent_id) if current.parent_id else None
        return None

    def readable(self, wa_id: str) -> bool:
        return self.viewer.can_read(self.articles.get(wa_id))

    def ancestor(self, wa_id: str, category: str) -> str | None:
        seen = {wa_id}
        current = self.parents.get(wa_id)
        while current is not None:
            parent_id, visible = current
            if parent_id in seen or not (self.viewer.dm or visible):
                return None
            if self.articles[parent_id].world_category == category:
                return parent_id if self.readable(parent_id) else None
            seen.add(parent_id)
            current = self.parents.get(parent_id)
        return None

    def relation(self, wa_id: str | None) -> Relation | None:
        if wa_id is None:
            return None
        article = self.articles[wa_id]
        return Relation(article, self.viewer.href(article))

    def children(self, wa_id: str, category: str, link: str) -> list[Relation]:
        found = [
            article
            for child_id, article in self.articles.items()
            if article.world_category == category
            and self.readable(child_id)
            and self.ancestor(child_id, link) == wa_id
        ]
        return [Relation(article, self.viewer.href(article)) for article in natural_order(found)]


def world_fiche(db: Session, article: WaArticle, viewer: Viewer) -> WorldFiche | None:
    category = article.world_category
    if category is None or article.removed_at is not None:
        return None
    graph = _Graph(db, viewer)
    fiche = WorldFiche(category, CATEGORY_LABELS[category])
    if category in (CITY, BUILDING):
        fiche.country = graph.relation(graph.ancestor(article.wa_id, COUNTRY))
    if category == BUILDING:
        fiche.city = graph.relation(graph.ancestor(article.wa_id, CITY))
    if category == COUNTRY:
        fiche.cities = graph.children(article.wa_id, CITY, COUNTRY)
    if category == CITY:
        fiche.buildings = graph.children(article.wa_id, BUILDING, CITY)
    return fiche


def categorised(articles: Iterable[WaArticle], category: str) -> list[WaArticle]:
    return natural_order(
        article
        for article in articles
        if article.removed_at is None and article.world_category == category
    )


def matches_name(article: WaArticle, query: str) -> bool:
    return not query or fold(query) in fold(article.title)


def tiles(articles: list[WaArticle], base: str, *, player_mode: bool) -> list[Tile]:
    result = []
    for category in WORLD_CATEGORIES:
        members = categorised(articles, category)
        images = [
            image for image in (article_image(article, player_mode) for article in members) if image
        ]
        result.append(
            Tile(
                category,
                TILE_LABELS[category],
                DESCRIPTIONS[category],
                len(members),
                images[:MAX_PREVIEWS],
                f"{base}/articles?section={WORLD_KEY}&categorie={SLUGS[category]}",
                EMPTY_LABELS[category],
            )
        )
    return result


def dm_articles(db: Session) -> list[WaArticle]:
    return list(
        db.scalars(
            select(WaArticle)
            .join(WorldCategory, WorldCategory.wa_id == WaArticle.wa_id)
            .where(WorldCategory.category.is_not(None), WaArticle.removed_at.is_(None))
        ).all()
    )


def player_articles(db: Session, accessible: set[str]) -> list[WaArticle]:
    if not accessible:
        return []
    return [article for article in dm_articles(db) if article.wa_id in accessible]
