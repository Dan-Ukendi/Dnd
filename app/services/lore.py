import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.chronology import ERAS, ERAS_BY_SLUG, EventVisibility, era_of, format_span
from app.models import (
    LORE_PLACEMENTS,
    Artefact,
    Character,
    CharacterItem,
    LoreEntry,
    LoreGroup,
    LoreStory,
    LoreType,
    TimelineEvent,
    User,
    WaArticle,
)
from app.services.artefact_split import artefact_row, fold, split_item_article
from app.services.chronology import EraView
from app.services.lore_sections import fiche_placement, narrative_sections

logger = logging.getLogger(__name__)

ITEM_TEMPLATE = "item"
LEGEND = "legende"
BOOK = "ouvrage"
ARTEFACT = "artefact"
GENRES = (LEGEND, ARTEFACT, BOOK)
GENRE_LABELS = {LEGEND: "Légende", ARTEFACT: "Artefact", BOOK: "Ouvrage"}
DEFAULT_LEGEND_TYPE = "Légende"
PLACEMENT_LABELS = {
    "part": "Partie d'un récit",
    "physical": "Description physique (fiche)",
    "characteristic": "Caractéristique (fiche)",
    "dm_note": "Note du MJ (fiche)",
}
FICHE_PLACEMENTS = ("physical", "characteristic", "dm_note")
TEMPLATE_ELEMENTS = {
    "item": "artefact",
    "settlement": "lieu",
    "landmark": "lieu",
    "location": "lieu",
    "person": "personnage",
    "organization": "organisation",
    "profession": "organisation",
    "rank": "organisation",
    "formation": "organisation",
}
DEFAULT_ELEMENT = "evenement"
ADD_NOTE = "Ajouté au Lore depuis l'article « {title} » : réservé au MJ par défaut ; à vérifier."
MAX_TITLE = 300
MAX_TEXT = 60_000
MAX_NOTE = 20_000
MAX_YEAR = 100_000
MAX_SORT_ORDER = 1_000_000
MAX_LABEL = 60
ARTEFACT_LIMITS = {
    "tagline": 300,
    "object_type": 300,
    "other_names": 500,
    "rarity": 300,
    "attunement": 300,
    "properties": MAX_TEXT,
    "bonuses": MAX_TEXT,
    "charges": 100,
    "recharge": 200,
    "curse": MAX_TEXT,
    "holder": 200,
    "location": 300,
    "status": 200,
    "review_note": MAX_NOTE,
}
ERA_INDEX = {era.slug: index for index, era in enumerate(ERAS)}
RARITIES = (
    ("commun", "Commun"),
    ("peu-commun", "Peu commun"),
    ("rare", "Rare"),
    ("tres-rare", "Très rare"),
    ("legendaire", "Légendaire"),
    ("artefact", "Artefact"),
)
RARITY_RE = re.compile(r"\b(?:peu commun|tres rare|legendaire|artefact|commun|rare)\b")
TYPE_SPLIT_RE = re.compile(r"[,(;:]")


class LoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Scope:
    dm: bool
    accessible: frozenset[str] = frozenset()
    visible_events: frozenset[int] = frozenset()

    @property
    def base(self) -> str:
        return "/mj" if self.dm else "/joueur"

    def article_ok(self, wa_id: str | None) -> bool:
        return wa_id is None or self.dm or wa_id in self.accessible

    def event_ok(self, event: TimelineEvent | None) -> bool:
        if event is None or event.removed_at is not None:
            return False
        return self.dm or event.id in self.visible_events


DM_SCOPE = Scope(dm=True)


def players_event_ids(db: Session) -> set[int]:
    return set(
        db.scalars(
            select(TimelineEvent.id).where(
                TimelineEvent.removed_at.is_(None),
                TimelineEvent.visibility == EventVisibility.PLAYERS,
            )
        ).all()
    )


def player_scope(db: Session, accessible: Iterable[str]) -> Scope:
    return Scope(
        dm=False,
        accessible=frozenset(accessible),
        visible_events=frozenset(players_event_ids(db)),
    )


def story_visible(story: LoreStory, scope: Scope) -> bool:
    if scope.dm:
        return True
    return (
        not story.gm_only
        and story.wa_article_id is not None
        and story.wa_article_id in scope.accessible
    )


def part_visible(entry: LoreEntry, scope: Scope) -> bool:
    if entry.placement != "part" or entry.story is None:
        return False
    if scope.dm:
        return True
    return (
        story_visible(entry.story, scope)
        and not entry.gm_only
        and scope.article_ok(entry.wa_article_id)
        and scope.article_ok(entry.source_article_id)
    )


def artefact_visible(artefact: Artefact, scope: Scope) -> bool:
    if scope.dm:
        return True
    return not artefact.gm_only and artefact.wa_article_id in scope.accessible


def section_visible(entry: LoreEntry, artefact: Artefact, scope: Scope) -> bool:
    if scope.dm:
        return True
    return (
        entry.placement in ("physical", "characteristic")
        and not entry.gm_only
        and artefact_visible(artefact, scope)
        and scope.article_ok(entry.source_article_id)
    )


@dataclass(frozen=True)
class EntryDate:
    start: int | None
    end: int | None
    era: str | None
    approximate: bool

    @property
    def dated(self) -> bool:
        return self.start is not None or self.era is not None

    @property
    def era_slug(self) -> str | None:
        if self.start is not None:
            return era_of(self.start).slug
        return self.era if self.era in ERAS_BY_SLUG else None


UNDATED = EntryDate(None, None, None, False)


def entry_date(entry: LoreEntry, event: TimelineEvent | None) -> EntryDate:
    if entry.timeline_event_id is not None:
        if event is None:
            return EntryDate(None, None, None, entry.approximate)
        return EntryDate(event.start_year, event.end_year, None, entry.approximate)
    return EntryDate(entry.start_year, entry.end_year, entry.era, entry.approximate)


def scoped_entry_date(entry: LoreEntry, scope: Scope) -> EntryDate:
    event = entry.event if scope.event_ok(entry.event) else None
    return entry_date(entry, event)


def story_date(story: LoreStory, parts: Iterable[LoreEntry], scope: Scope) -> EntryDate:
    if story.start_year is not None or story.era is not None:
        return EntryDate(story.start_year, story.end_year, story.era, story.approximate)
    dates = [scoped_entry_date(part, scope) for part in parts]
    years = [date for date in dates if date.start is not None]
    if years:
        start = min(date.start for date in years if date.start is not None)
        end = max(date.end if date.end is not None else date.start or 0 for date in years)
        approximate = story.approximate or any(date.approximate for date in years)
        return EntryDate(start, end if end != start else None, None, approximate)
    eras = [date for date in dates if date.era_slug is not None]
    if eras:
        first = min(eras, key=lambda date: ERA_INDEX[date.era_slug or ""])
        return EntryDate(None, None, first.era_slug, True)
    return EntryDate(None, None, None, story.approximate)


def date_label(date: EntryDate, eras: Mapping[str, EraView]) -> str:
    if date.start is not None:
        label = format_span(date.start, date.end)
    elif date.era_slug is not None:
        era = eras.get(date.era_slug)
        label = era.name if era else ERAS_BY_SLUG[date.era_slug].name
    else:
        return "Non daté"
    return f"≈ {label}" if date.approximate else label


SortKey = tuple[int, float, int, float]


def date_key(date: EntryDate) -> SortKey:
    slug = date.era_slug
    if slug is None:
        return (len(ERAS), 0.0, 0, 0.0)
    era = ERAS_BY_SLUG[slug]
    if date.start is None:
        floor = float(era.start) if era.start is not None else float("-inf")
        return (ERA_INDEX[slug], floor, 0, floor)
    end = date.end if date.end is not None else date.start
    return (ERA_INDEX[slug], float(date.start), 1, float(end))


def sort_key(date: EntryDate, entry: LoreEntry) -> tuple[Any, ...]:
    return (
        *date_key(date),
        entry.sort_order,
        entry.wa_article_id or "",
        entry.position,
        entry.id or 0,
    )


def lore_types(db: Session) -> list[LoreType]:
    return list(db.scalars(select(LoreType).order_by(LoreType.position, LoreType.id)).all())


def lore_groups(db: Session) -> list[LoreGroup]:
    return list(db.scalars(select(LoreGroup).order_by(LoreGroup.position, LoreGroup.id)).all())


def all_stories(db: Session, kind: str | None = None) -> list[LoreStory]:
    statement = select(LoreStory).order_by(LoreStory.position, LoreStory.id)
    if kind is not None:
        statement = statement.where(LoreStory.kind == kind)
    return list(db.scalars(statement).all())


def story_parts(db: Session, story: LoreStory, scope: Scope) -> list[LoreEntry]:
    parts = db.scalars(
        select(LoreEntry)
        .where(LoreEntry.story_id == story.id, LoreEntry.placement == "part")
        .order_by(LoreEntry.position, LoreEntry.id)
    ).all()
    return [part for part in parts if part_visible(part, scope)]


def get_story(db: Session, story_id: int, kind: str, scope: Scope) -> LoreStory | None:
    story = db.get(LoreStory, story_id)
    if story is None or story.kind != kind or not story_visible(story, scope):
        return None
    return story


def story_href(story: LoreStory, scope: Scope) -> str:
    segment = "recits" if story.kind == LEGEND else "ouvrages"
    return f"{scope.base}/lore/{segment}/{story.id}"


@dataclass(frozen=True)
class StoryView:
    story: LoreStory
    href: str
    parts: list[LoreEntry]
    date: EntryDate
    label: str
    era: EraView | None

    @property
    def type_label(self) -> str:
        return self.story.type.label if self.story.type else ""

    @property
    def group(self) -> LoreGroup | None:
        return self.story.group


def story_view(db: Session, story: LoreStory, scope: Scope, eras: list[EraView]) -> StoryView:
    by_slug = {era.slug: era for era in eras}
    parts = story_parts(db, story, scope)
    date = story_date(story, parts, scope)
    return StoryView(
        story=story,
        href=story_href(story, scope),
        parts=parts,
        date=date,
        label=date_label(date, by_slug),
        era=by_slug.get(date.era_slug) if date.era_slug else None,
    )


def story_views(db: Session, kind: str, scope: Scope, eras: list[EraView]) -> list[StoryView]:
    return [
        story_view(db, story, scope, eras)
        for story in all_stories(db, kind)
        if story_visible(story, scope)
    ]


@dataclass(frozen=True)
class LegendFilters:
    type_id: int | None = None
    query: str = ""
    sort: str = "chrono"
    review: bool = False

    @classmethod
    def parse(cls, params: Mapping[str, str], *, dm: bool) -> "LegendFilters":
        raw_type = params.get("type", "")
        return cls(
            type_id=int(raw_type) if raw_type.isdigit() and len(raw_type) < 9 else None,
            query=params.get("q", "").strip()[:200],
            sort="alpha" if params.get("tri") == "alpha" else "chrono",
            review=dm and params.get("a_verifier") == "1",
        )

    @property
    def active(self) -> bool:
        return self.type_id is not None or bool(self.query) or self.review

    def matches(self, view: StoryView) -> bool:
        if self.type_id is not None and view.story.type_id != self.type_id:
            return False
        if self.query and fold(self.query) not in fold(view.story.title):
            return False
        return not (self.review and not view.story.review_note.strip())

    def ordered(self, views: list[StoryView]) -> list[StoryView]:
        if self.sort == "alpha":
            return sorted(views, key=lambda view: (fold(view.story.title), view.story.id))
        return sorted(
            views,
            key=lambda view: (
                *date_key(view.date),
                view.story.position,
                fold(view.story.title),
                view.story.id,
            ),
        )


@dataclass(frozen=True)
class GroupBlock:
    group: LoreGroup | None
    views: list[StoryView]


def grouped(views: list[StoryView], groups: list[LoreGroup]) -> list[GroupBlock]:
    blocks = [
        GroupBlock(group, [view for view in views if view.story.group_id == group.id])
        for group in groups
    ]
    known = {group.id for group in groups}
    loose = [view for view in views if view.story.group_id not in known]
    blocks.append(GroupBlock(None, loose))
    return [block for block in blocks if block.views]


def type_counts(views: Iterable[StoryView]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for view in views:
        if view.story.type_id is not None:
            counts[view.story.type_id] = counts.get(view.story.type_id, 0) + 1
    return counts


@dataclass(frozen=True)
class FriseGroup:
    era: EraView
    views: list[StoryView]


def frise_groups(views: list[StoryView], eras: list[EraView]) -> list[FriseGroup]:
    dated = sorted(
        (view for view in views if view.era is not None),
        key=lambda view: (*date_key(view.date), view.story.position, view.story.id),
    )
    grouped_views: dict[str, list[StoryView]] = {}
    for view in dated:
        if view.era is not None:
            grouped_views.setdefault(view.era.slug, []).append(view)
    return [FriseGroup(era, grouped_views[era.slug]) for era in eras if grouped_views.get(era.slug)]


def unplaced_entries(db: Session) -> list[LoreEntry]:
    return list(
        db.scalars(
            select(LoreEntry)
            .where(LoreEntry.placement == "part", LoreEntry.story_id.is_(None))
            .order_by(LoreEntry.id)
        ).all()
    )


def item_articles(db: Session) -> list[WaArticle]:
    return list(
        db.scalars(
            select(WaArticle)
            .where(WaArticle.template_type == ITEM_TEMPLATE, WaArticle.removed_at.is_(None))
            .order_by(WaArticle.title)
        ).all()
    )


def get_artefact(db: Session, wa_id: str) -> Artefact | None:
    return db.scalar(select(Artefact).where(Artefact.wa_article_id == wa_id))


def rarity_keys(rarity: str) -> list[str]:
    found = {match.group(0).replace(" ", "-") for match in RARITY_RE.finditer(fold(rarity))}
    return [key for key, _ in RARITIES if key in found]


def type_key(object_type: str) -> tuple[str, str]:
    head = TYPE_SPLIT_RE.split(object_type, maxsplit=1)[0].strip()
    if not head:
        return "", ""
    return fold(head).replace(" ", "-"), head[:1].upper() + head[1:]


@dataclass(frozen=True)
class ArtefactCard:
    article: WaArticle
    artefact: Artefact
    type_key: str
    type_label: str
    rarities: list[str]


def artefact_cards(db: Session, scope: Scope) -> list[ArtefactCard]:
    rows = db.execute(
        select(Artefact, WaArticle)
        .join(WaArticle, WaArticle.wa_id == Artefact.wa_article_id)
        .where(WaArticle.removed_at.is_(None))
        .order_by(WaArticle.title)
    ).all()
    cards = []
    for artefact, article in rows:
        if not artefact_visible(artefact, scope):
            continue
        key, label = type_key(artefact.object_type)
        cards.append(ArtefactCard(article, artefact, key, label, rarity_keys(artefact.rarity)))
    return cards


@dataclass(frozen=True)
class ArtefactFilters:
    rarity: str = ""
    type: str = ""
    query: str = ""
    review: bool = False

    @classmethod
    def parse(cls, params: Mapping[str, str], *, dm: bool) -> "ArtefactFilters":
        rarity = params.get("rarete", "")
        return cls(
            rarity=rarity if rarity in dict(RARITIES) else "",
            type=params.get("type", "")[:60],
            query=params.get("q", "").strip()[:200],
            review=dm and params.get("a_verifier") == "1",
        )

    def matches(self, card: ArtefactCard) -> bool:
        if self.rarity and self.rarity not in card.rarities:
            return False
        if self.type and card.type_key != self.type:
            return False
        if self.query:
            haystack = fold(
                " ".join((card.article.title, card.artefact.other_names, card.artefact.object_type))
            )
            if fold(self.query) not in haystack:
                return False
        return not (self.review and not card.artefact.review_note.strip())


def fiche_sections(db: Session, artefact: Artefact, scope: Scope) -> list[LoreEntry]:
    entries = db.scalars(
        select(LoreEntry)
        .where(
            LoreEntry.wa_article_id == artefact.wa_article_id,
            LoreEntry.placement.in_(FICHE_PLACEMENTS),
        )
        .order_by(LoreEntry.position, LoreEntry.id)
    ).all()
    return [entry for entry in entries if section_visible(entry, artefact, scope)]


def linked_stories(db: Session, artefact: Artefact, kind: str, scope: Scope) -> list[LoreStory]:
    stories = db.scalars(
        select(LoreStory)
        .where(LoreStory.artefact_id == artefact.id, LoreStory.kind == kind)
        .order_by(LoreStory.position, LoreStory.id)
    ).all()
    return [story for story in stories if story_visible(story, scope)]


def books_of_legend(db: Session, story: LoreStory, scope: Scope) -> list[LoreStory]:
    books = db.scalars(
        select(LoreStory)
        .where(LoreStory.legend_id == story.id, LoreStory.kind == BOOK)
        .order_by(LoreStory.position, LoreStory.id)
    ).all()
    return [book for book in books if story_visible(book, scope)]


def holders(db: Session, wa_id: str, player: User | None = None) -> list[str]:
    statement = (
        select(Character.name)
        .join(CharacterItem, CharacterItem.character_id == Character.id)
        .where(CharacterItem.wa_article_id == wa_id)
        .order_by(Character.name)
        .distinct()
    )
    if player is not None:
        statement = statement.where(Character.player_id == player.id)
    return list(db.scalars(statement).all())


def review_counts(db: Session) -> dict[str, int]:
    def count(model: type[Any], *conditions: ColumnElement[bool]) -> int:
        return (
            db.scalar(
                select(func.count()).select_from(model).where(model.review_note != "", *conditions)
            )
            or 0
        )

    return {
        "legendes": count(LoreStory, LoreStory.kind == LEGEND),
        "ouvrages": count(LoreStory, LoreStory.kind == BOOK),
        "artefacts": count(Artefact),
        "entrees": count(LoreEntry),
    }


def _year(value: str, code: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if not value.lstrip("-").isdigit() or len(value) > 7 or abs(int(value)) > MAX_YEAR:
        raise LoreError(code)
    return int(value)


def _clean(value: str, limit: int, code: str, *, required: bool = False) -> str:
    value = value.strip().replace("\r\n", "\n")
    if (required and not value) or len(value) > limit:
        raise LoreError(code)
    return value


def _order(value: str) -> int:
    order = value.strip() or "0"
    if not order.lstrip("-").isdigit() or abs(int(order)) > MAX_SORT_ORDER:
        raise LoreError("sort_order")
    return int(order)


def _dates(
    start_year: str, end_year: str, era_value: str
) -> tuple[int | None, int | None, str | None]:
    start = _year(start_year, "start_year")
    end = _year(end_year, "end_year")
    era = era_value.strip() or None
    if era is not None and era not in ERAS_BY_SLUG:
        raise LoreError("era")
    if end is not None and (start is None or end < start):
        raise LoreError("end_year")
    if start is not None and era is not None and era_of(start).slug != era:
        raise LoreError("era_mismatch")
    if start is not None:
        era = None
    return start, end, era


def _article_id(db: Session, value: str, code: str = "article") -> str | None:
    wa_id = value.strip() or None
    if wa_id is not None:
        article = db.get(WaArticle, wa_id)
        if article is None or article.removed_at is not None:
            raise LoreError(code)
    return wa_id


def _optional_id(db: Session, model: type[Any], value: str, code: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if not value.isdigit() or db.get(model, int(value)) is None:
        raise LoreError(code)
    return int(value)


@dataclass(frozen=True)
class EntryForm:
    title: str = ""
    text: str = ""
    start_year: str = ""
    end_year: str = ""
    era: str = ""
    approximate: bool = False
    sort_order: str = ""
    article_id: str = ""
    event_id: str = ""
    gm_only: bool = False
    review_note: str = ""
    story_id: str = ""
    placement: str = "part"
    position: str = ""
    source_article_id: str = ""
    source_section: str = ""


def _validated(db: Session, form: EntryForm) -> dict[str, object]:
    title = _clean(form.title, MAX_TITLE, "title", required=True)
    text = _clean(form.text, MAX_TEXT, "text")
    review_note = _clean(form.review_note, MAX_NOTE, "review_note")
    start, end, era = _dates(form.start_year, form.end_year, form.era)
    event_id = None
    if form.event_id.strip():
        if not form.event_id.strip().isdigit():
            raise LoreError("event")
        event = db.get(TimelineEvent, int(form.event_id))
        if event is None or event.removed_at is not None:
            raise LoreError("event")
        if start is not None or era is not None:
            raise LoreError("date_conflict")
        event_id = event.id
    if form.placement not in LORE_PLACEMENTS:
        raise LoreError("placement")
    story_id = _optional_id(db, LoreStory, form.story_id, "story")
    article_id = _article_id(db, form.article_id)
    if form.placement != "part":
        story_id = None
        if article_id is None or get_artefact(db, article_id) is None:
            raise LoreError("fiche")
    source_id = _article_id(db, form.source_article_id, "source")
    section = _clean(form.source_section, MAX_TITLE, "source_section") or None
    return {
        "title": title,
        "text": text,
        "start_year": start,
        "end_year": end,
        "era": era,
        "approximate": form.approximate,
        "sort_order": _order(form.sort_order),
        "position": _order(form.position),
        "wa_article_id": article_id,
        "timeline_event_id": event_id,
        "gm_only": form.gm_only,
        "review_note": review_note,
        "story_id": story_id,
        "placement": form.placement,
        "source_article_id": source_id,
        "source_section": section if source_id else None,
    }


def element_for(db: Session, wa_id: str | None) -> str:
    article = db.get(WaArticle, wa_id) if wa_id else None
    if article is None:
        return DEFAULT_ELEMENT
    return TEMPLATE_ELEMENTS.get((article.template_type or "").lower(), DEFAULT_ELEMENT)


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def create_entry(db: Session, form: EntryForm) -> LoreEntry:
    values = _validated(db, form)
    entry = LoreEntry(
        element_type=element_for(
            db, _text_or_none(values["source_article_id"] or values["wa_article_id"])
        ),
        **values,
    )
    db.add(entry)
    db.commit()
    return entry


def update_entry(db: Session, entry: LoreEntry, form: EntryForm) -> None:
    for key, value in _validated(db, form).items():
        setattr(entry, key, value)
    db.commit()


def next_position(db: Session, story_id: int) -> int:
    current = db.scalar(select(func.max(LoreEntry.position)).where(LoreEntry.story_id == story_id))
    return 0 if current is None else current + 1


def move_entry(db: Session, entry: LoreEntry, direction: int) -> None:
    if entry.story_id is not None:
        siblings = list(
            db.scalars(
                select(LoreEntry)
                .where(LoreEntry.story_id == entry.story_id, LoreEntry.placement == "part")
                .order_by(LoreEntry.position, LoreEntry.id)
            ).all()
        )
    else:
        siblings = list(
            db.scalars(
                select(LoreEntry)
                .where(
                    LoreEntry.wa_article_id == entry.wa_article_id,
                    LoreEntry.placement.in_(FICHE_PLACEMENTS),
                )
                .order_by(LoreEntry.position, LoreEntry.id)
            ).all()
        )
    if entry not in siblings:
        return
    index = siblings.index(entry)
    target = index + direction
    if not 0 <= target < len(siblings):
        return
    siblings[index], siblings[target] = siblings[target], siblings[index]
    for position, sibling in enumerate(siblings):
        sibling.position = position
    db.commit()


def clear_entry_review(db: Session, entry: LoreEntry) -> None:
    entry.review_note = ""
    db.commit()


def delete_entry(db: Session, entry: LoreEntry) -> None:
    db.delete(entry)
    db.commit()


@dataclass(frozen=True)
class StoryForm:
    title: str = ""
    description: str = ""
    intro_sections: str = ""
    type_id: str = ""
    group_id: str = ""
    new_group: str = ""
    position: str = ""
    start_year: str = ""
    end_year: str = ""
    era: str = ""
    approximate: bool = False
    article_id: str = ""
    artefact_id: str = ""
    legend_id: str = ""
    gm_only: bool = True
    review_note: str = ""


def _group_id(db: Session, form: StoryForm) -> int | None:
    title = _clean(form.new_group, MAX_TITLE, "group")
    if title:
        existing = db.scalar(select(LoreGroup).where(LoreGroup.title == title))
        if existing is not None:
            return existing.id
        top = db.scalar(select(func.max(LoreGroup.position))) or 0
        group = LoreGroup(title=title, position=top + 1)
        db.add(group)
        db.flush()
        return group.id
    return _optional_id(db, LoreGroup, form.group_id, "group")


def _story_values(
    db: Session, form: StoryForm, kind: str, story_id: int | None = None
) -> dict[str, object]:
    start, end, era = _dates(form.start_year, form.end_year, form.era)
    legend_id = _optional_id(db, LoreStory, form.legend_id, "legend")
    if legend_id is not None:
        legend = db.get(LoreStory, legend_id)
        if legend is None or legend.kind != LEGEND or legend_id == story_id:
            raise LoreError("legend")
    return {
        "title": _clean(form.title, MAX_TITLE, "title", required=True),
        "description": _clean(form.description, MAX_TEXT, "text"),
        "intro_sections": _clean(form.intro_sections, MAX_NOTE, "intro"),
        "type_id": _optional_id(db, LoreType, form.type_id, "type") if kind == LEGEND else None,
        "group_id": _group_id(db, form) if kind == LEGEND else None,
        "position": _order(form.position),
        "start_year": start,
        "end_year": end,
        "era": era,
        "approximate": form.approximate,
        "wa_article_id": _article_id(db, form.article_id),
        "artefact_id": _optional_id(db, Artefact, form.artefact_id, "artefact"),
        "legend_id": legend_id if kind == BOOK else None,
        "gm_only": form.gm_only,
        "review_note": _clean(form.review_note, MAX_NOTE, "review_note"),
    }


def create_story(db: Session, kind: str, form: StoryForm) -> LoreStory:
    if kind not in (LEGEND, BOOK):
        raise LoreError("kind")
    story = LoreStory(kind=kind, **_story_values(db, form, kind))
    db.add(story)
    db.commit()
    return story


def update_story(db: Session, story: LoreStory, form: StoryForm) -> None:
    for key, value in _story_values(db, form, story.kind, story.id).items():
        setattr(story, key, value)
    db.commit()


def clear_story_review(db: Session, story: LoreStory) -> None:
    story.review_note = ""
    db.commit()


def delete_story(db: Session, story: LoreStory) -> None:
    db.delete(story)
    db.commit()


def add_type(db: Session, label: str) -> LoreType:
    label = _clean(label, MAX_LABEL, "label", required=True)
    if db.scalar(select(LoreType).where(LoreType.label == label)) is not None:
        raise LoreError("label_taken")
    top = db.scalar(select(func.max(LoreType.position))) or 0
    lore_type = LoreType(label=label, position=top + 1)
    db.add(lore_type)
    db.commit()
    return lore_type


def update_type(db: Session, lore_type: LoreType, label: str, position: str) -> None:
    label = _clean(label, MAX_LABEL, "label", required=True)
    other = db.scalar(select(LoreType).where(LoreType.label == label, LoreType.id != lore_type.id))
    if other is not None:
        raise LoreError("label_taken")
    lore_type.label = label
    lore_type.position = _order(position)
    db.commit()


def delete_type(db: Session, lore_type: LoreType) -> None:
    used = db.scalar(
        select(func.count()).select_from(LoreStory).where(LoreStory.type_id == lore_type.id)
    )
    if used:
        raise LoreError("type_used")
    db.delete(lore_type)
    db.commit()


def update_group(
    db: Session, group: LoreGroup, title: str, description: str, position: str
) -> None:
    title = _clean(title, MAX_TITLE, "group", required=True)
    other = db.scalar(select(LoreGroup).where(LoreGroup.title == title, LoreGroup.id != group.id))
    if other is not None:
        raise LoreError("group_taken")
    group.title = title
    group.description = _clean(description, MAX_TEXT, "text")
    group.position = _order(position)
    db.commit()


def delete_group(db: Session, group: LoreGroup) -> None:
    db.delete(group)
    db.commit()


def update_artefact(
    db: Session, artefact: Artefact, values: Mapping[str, str], gm_only: bool
) -> None:
    cleaned = {
        key: _clean(values.get(key, ""), limit, key) for key, limit in ARTEFACT_LIMITS.items()
    }
    for key, value in cleaned.items():
        setattr(artefact, key, value)
    artefact.gm_only = gm_only
    db.commit()


def clear_artefact_review(db: Session, artefact: Artefact) -> None:
    artefact.review_note = ""
    db.commit()


def add_fiche_section(
    db: Session, artefact: Artefact, title: str, section: str, placement: str
) -> LoreEntry:
    if placement not in FICHE_PLACEMENTS:
        raise LoreError("placement")
    section = _clean(section, MAX_TITLE, "source_section", required=True)
    title = _clean(title, MAX_TITLE, "title") or section
    top = db.scalar(
        select(func.max(LoreEntry.position)).where(
            LoreEntry.wa_article_id == artefact.wa_article_id,
            LoreEntry.placement.in_(FICHE_PLACEMENTS),
        )
    )
    entry = LoreEntry(
        title=title,
        element_type=ARTEFACT,
        text="",
        position=0 if top is None else top + 1,
        wa_article_id=artefact.wa_article_id,
        source_article_id=artefact.wa_article_id,
        source_section=section,
        placement=placement,
        gm_only=True,
        review_note=ADD_NOTE.format(title=artefact.article.title),
    )
    db.add(entry)
    db.commit()
    return entry


def add_story_part(
    db: Session, story: LoreStory, title: str, section: str, source_id: str, text: str
) -> LoreEntry:
    source = _article_id(db, source_id, "source")
    section_value = _clean(section, MAX_TITLE, "source_section") or None
    body = _clean(text, MAX_TEXT, "text")
    title = _clean(title, MAX_TITLE, "title") or (section_value or "")
    if not title:
        raise LoreError("title")
    if source is None and not body:
        raise LoreError("part_source")
    entry = LoreEntry(
        title=title,
        element_type=element_for(db, source),
        text=body,
        position=next_position(db, story.id),
        wa_article_id=source,
        source_article_id=source,
        source_section=section_value if source else None,
        story_id=story.id,
        placement="part",
        gm_only=True,
    )
    db.add(entry)
    db.commit()
    return entry


@dataclass
class AddResult:
    genre: str
    story: LoreStory | None = None
    artefact: Artefact | None = None
    notes: list[str] = field(default_factory=list)


def _default_type_id(db: Session) -> int | None:
    lore_type = db.scalar(select(LoreType).where(LoreType.label == DEFAULT_LEGEND_TYPE))
    return lore_type.id if lore_type else None


def _add_story(db: Session, article: WaArticle, kind: str) -> LoreStory:
    existing = db.scalar(
        select(LoreStory).where(LoreStory.wa_article_id == article.wa_id, LoreStory.kind == kind)
    )
    if existing is not None:
        raise LoreError("exists")
    top = db.scalar(select(func.max(LoreStory.position)).where(LoreStory.kind == kind)) or 0
    story = LoreStory(
        kind=kind,
        title=article.title[:MAX_TITLE],
        type_id=_default_type_id(db) if kind == LEGEND else None,
        position=top + 1,
        wa_article_id=article.wa_id,
        gm_only=True,
        review_note=ADD_NOTE.format(title=article.title),
    )
    db.add(story)
    db.flush()
    element = element_for(db, article.wa_id)
    sections = narrative_sections(article) or [None]
    for position, section in enumerate(sections):
        db.add(
            LoreEntry(
                title=(section or article.title)[:MAX_TITLE],
                element_type=element,
                text="",
                position=position,
                wa_article_id=article.wa_id,
                source_article_id=article.wa_id,
                source_section=section,
                story_id=story.id,
                placement="part",
                gm_only=True,
            )
        )
    return story


def _add_artefact(db: Session, article: WaArticle) -> Artefact:
    if get_artefact(db, article.wa_id) is not None:
        raise LoreError("exists")
    draft = split_item_article(article.wa_id, article.title, article.content)
    row = artefact_row(article.wa_id, draft)
    notes = [ADD_NOTE.format(title=article.title), *row["review_note"].splitlines()]
    row["review_note"] = "\n".join(note for note in notes if note)
    artefact = Artefact(**row, gm_only=True)
    db.add(artefact)
    db.flush()
    position = 0
    for entry in draft.entries:
        placement = fiche_placement(entry.title)
        if placement is None:
            continue
        db.add(
            LoreEntry(
                title=entry.title[:MAX_TITLE],
                element_type=ARTEFACT,
                text="",
                position=position,
                wa_article_id=article.wa_id,
                source_article_id=article.wa_id,
                source_section=entry.title[:MAX_TITLE],
                placement=placement,
                gm_only=True,
            )
        )
        position += 1
    return artefact


def add_to_lore(db: Session, article: WaArticle, genre: str) -> AddResult:
    if genre not in GENRES:
        raise LoreError("genre")
    if article.removed_at is not None:
        raise LoreError("article")
    if genre == ARTEFACT:
        result = AddResult(genre, artefact=_add_artefact(db, article))
    else:
        result = AddResult(genre, story=_add_story(db, article, genre))
    db.commit()
    logger.info("Article %s added to the Lore as %s", article.wa_id, genre)
    return result


@dataclass(frozen=True)
class LoreLinks:
    stories: list[LoreStory]
    artefact: Artefact | None


def article_lore(db: Session, wa_id: str) -> LoreLinks:
    stories = list(
        db.scalars(
            select(LoreStory)
            .where(LoreStory.wa_article_id == wa_id)
            .order_by(LoreStory.kind, LoreStory.id)
        ).all()
    )
    return LoreLinks(stories, get_artefact(db, wa_id))
