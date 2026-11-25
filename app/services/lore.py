import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.orm import Session

from app.chronology import ERAS, ERAS_BY_SLUG, EventVisibility, era_of, format_span
from app.models import (
    LORE_ELEMENT_TYPES,
    Artefact,
    Character,
    CharacterItem,
    LoreEntry,
    TimelineEvent,
    User,
    WaArticle,
)
from app.services.artefact_split import artefact_row, entry_rows, review_lines, split_item_article
from app.services.chronology import EraView

logger = logging.getLogger(__name__)

ITEM_TEMPLATE = "item"
ELEMENT_LABELS = {
    "artefact": "Artefact",
    "lieu": "Lieu",
    "personnage": "Personnage",
    "organisation": "Organisation",
    "divinite": "Divinité",
    "evenement": "Événement",
}
MAX_TITLE = 300
MAX_TEXT = 60_000
MAX_NOTE = 20_000
MAX_YEAR = 100_000
MAX_SORT_ORDER = 1_000_000
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


class LoreError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


def _event_ids(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(TimelineEvent.code, TimelineEvent.id).where(TimelineEvent.removed_at.is_(None))
    ).all()
    return {row.code: row.id for row in rows}


def create_from_article(db: Session, article: WaArticle) -> Artefact | None:
    if get_artefact(db, article.wa_id) is not None:
        return None
    draft = split_item_article(article.wa_id, article.title, article.content)
    fiche = artefact_row(article.wa_id, draft)
    rows = entry_rows(article.wa_id, draft, _event_ids(db))
    artefact = Artefact(**fiche)
    db.add(artefact)
    db.add_all(LoreEntry(**row) for row in rows)
    db.flush()
    logger.info("Split item article %s into a fiche and %d Lore entries", article.wa_id, len(rows))
    for line in review_lines(article.title, fiche, rows):
        logger.info("To review: %s", line)
    return artefact


def ensure_artefacts(db: Session) -> list[Artefact]:
    created = []
    for article in item_articles(db):
        artefact = create_from_article(db, article)
        if artefact is not None:
            created.append(artefact)
    return created


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


def entry_date(entry: LoreEntry, event: TimelineEvent | None) -> EntryDate:
    if entry.timeline_event_id is not None:
        if event is None:
            return EntryDate(None, None, None, entry.approximate)
        return EntryDate(event.start_year, event.end_year, None, entry.approximate)
    return EntryDate(entry.start_year, entry.end_year, entry.era, entry.approximate)


def date_label(date: EntryDate, eras: Mapping[str, EraView]) -> str:
    if date.start is not None:
        label = format_span(date.start, date.end)
    elif date.era_slug is not None:
        era = eras.get(date.era_slug)
        label = era.name if era else ERAS_BY_SLUG[date.era_slug].name
    else:
        return "Non daté"
    return f"≈ {label}" if date.approximate else label


SortKey = tuple[int, float, int, float, int, str, int, int]


def sort_key(date: EntryDate, entry: LoreEntry) -> SortKey:
    tail = (entry.sort_order, entry.wa_article_id or "", entry.position, entry.id or 0)
    slug = date.era_slug
    if slug is None:
        return (len(ERAS), 0.0, 0, 0.0, *tail)
    era = ERAS_BY_SLUG[slug]
    if date.start is None:
        floor = float(era.start) if era.start is not None else float("-inf")
        return (ERA_INDEX[slug], floor, 0, floor, *tail)
    end = date.end if date.end is not None else date.start
    return (ERA_INDEX[slug], float(date.start), 1, float(end), *tail)


def players_event_ids(db: Session) -> set[int]:
    return set(
        db.scalars(
            select(TimelineEvent.id).where(
                TimelineEvent.removed_at.is_(None),
                TimelineEvent.visibility == EventVisibility.PLAYERS,
            )
        ).all()
    )


def _player_filter(accessible: set[str], visible_events: set[int]) -> ColumnElement[bool]:
    by_article = LoreEntry.wa_article_id.in_(list(accessible)) if accessible else false()
    by_event = (
        and_(
            LoreEntry.wa_article_id.is_(None),
            LoreEntry.timeline_event_id.in_(list(visible_events)),
        )
        if visible_events
        else false()
    )
    return and_(LoreEntry.gm_only.is_(False), or_(by_article, by_event))


def entries_for(
    db: Session,
    *,
    dm: bool,
    accessible: set[str] | None = None,
    visible_events: set[int] | None = None,
    article_id: str | None = None,
) -> list[LoreEntry]:
    statement = select(LoreEntry)
    if not dm:
        statement = statement.where(_player_filter(accessible or set(), visible_events or set()))
    if article_id is not None:
        statement = statement.where(LoreEntry.wa_article_id == article_id)
    return list(db.scalars(statement.order_by(LoreEntry.id)).all())


def get_entry(
    db: Session,
    entry_id: int,
    *,
    dm: bool,
    accessible: set[str] | None = None,
    visible_events: set[int] | None = None,
) -> LoreEntry | None:
    statement = select(LoreEntry).where(LoreEntry.id == entry_id)
    if not dm:
        statement = statement.where(_player_filter(accessible or set(), visible_events or set()))
    return db.scalar(statement)


@dataclass(frozen=True)
class EntryView:
    entry: LoreEntry
    date: EntryDate
    era: EraView | None
    label: str
    href: str
    article_href: str | None
    event_href: str | None
    event: TimelineEvent | None

    @property
    def element_label(self) -> str:
        return ELEMENT_LABELS.get(self.entry.element_type, self.entry.element_type)


def build_views(
    entries: Iterable[LoreEntry],
    eras: list[EraView],
    *,
    dm: bool,
    accessible: set[str] | None = None,
    visible_events: set[int] | None = None,
) -> list[EntryView]:
    by_slug = {era.slug: era for era in eras}
    base = "/mj" if dm else "/joueur"
    views = []
    for entry in entries:
        event = entry.event
        if event is not None and (
            event.removed_at is not None or (not dm and event.id not in (visible_events or set()))
        ):
            event = None
        date = entry_date(entry, event)
        article = entry.article
        article_href = None
        if (
            article is not None
            and article.removed_at is None
            and (dm or article.wa_id in (accessible or set()))
        ):
            article_href = f"{base}/articles/{article.wa_id}"
        views.append(
            EntryView(
                entry=entry,
                date=date,
                era=by_slug.get(date.era_slug) if date.era_slug else None,
                label=date_label(date, by_slug),
                href=f"{base}/lore/{entry.id}",
                article_href=article_href,
                event_href=f"{base}/chronologie/evenements/{event.id}" if event else None,
                event=event,
            )
        )
    views.sort(key=lambda view: sort_key(view.date, view.entry))
    return views


@dataclass(frozen=True)
class FriseGroup:
    era: EraView
    views: list[EntryView]


def frise_groups(views: list[EntryView], eras: list[EraView]) -> list[FriseGroup]:
    grouped: dict[str, list[EntryView]] = {}
    for view in views:
        if view.era is not None:
            grouped.setdefault(view.era.slug, []).append(view)
    return [FriseGroup(era, grouped[era.slug]) for era in eras if grouped.get(era.slug)]


def undated(views: list[EntryView]) -> list[EntryView]:
    return [view for view in views if view.era is None]


@dataclass(frozen=True)
class FriseFilters:
    element: str = ""
    review: bool = False

    @classmethod
    def parse(cls, params: Mapping[str, str], *, dm: bool) -> "FriseFilters":
        element = params.get("element", "")
        return cls(
            element=element if element in ELEMENT_LABELS else "",
            review=dm and params.get("a_verifier") == "1",
        )

    def matches(self, view: EntryView) -> bool:
        if self.element and view.entry.element_type != self.element:
            return False
        return not (self.review and not view.entry.review_note.strip())


def element_counts(views: Iterable[EntryView]) -> dict[str, int]:
    counts = dict.fromkeys(ELEMENT_LABELS, 0)
    for view in views:
        counts[view.entry.element_type] = counts.get(view.entry.element_type, 0) + 1
    return counts


def review_count(db: Session) -> int:
    entries = db.scalars(select(LoreEntry.review_note)).all()
    fiches = db.scalars(select(Artefact.review_note)).all()
    return sum(1 for note in [*entries, *fiches] if note.strip())


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


@dataclass(frozen=True)
class EntryForm:
    title: str = ""
    element_type: str = ""
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


def _validated(db: Session, form: EntryForm) -> dict[str, object]:
    title = _clean(form.title, MAX_TITLE, "title", required=True)
    if form.element_type not in LORE_ELEMENT_TYPES:
        raise LoreError("element_type")
    text = _clean(form.text, MAX_TEXT, "text")
    review_note = _clean(form.review_note, MAX_NOTE, "review_note")
    start = _year(form.start_year, "start_year")
    end = _year(form.end_year, "end_year")
    era = form.era.strip() or None
    if era is not None and era not in ERAS_BY_SLUG:
        raise LoreError("era")
    if end is not None and (start is None or end < start):
        raise LoreError("end_year")
    if start is not None and era is not None and era_of(start).slug != era:
        raise LoreError("era_mismatch")
    if start is not None:
        era = None
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
    article_id = form.article_id.strip() or None
    if article_id is not None:
        article = db.get(WaArticle, article_id)
        if article is None or article.removed_at is not None:
            raise LoreError("article")
    order = form.sort_order.strip() or "0"
    if not order.lstrip("-").isdigit() or abs(int(order)) > MAX_SORT_ORDER:
        raise LoreError("sort_order")
    return {
        "title": title,
        "element_type": form.element_type,
        "text": text,
        "start_year": start,
        "end_year": end,
        "era": era,
        "approximate": form.approximate,
        "sort_order": int(order),
        "wa_article_id": article_id,
        "timeline_event_id": event_id,
        "gm_only": form.gm_only,
        "review_note": review_note,
    }


def create_entry(db: Session, form: EntryForm) -> LoreEntry:
    entry = LoreEntry(**_validated(db, form))
    db.add(entry)
    db.commit()
    return entry


def update_entry(db: Session, entry: LoreEntry, form: EntryForm) -> None:
    for key, value in _validated(db, form).items():
        setattr(entry, key, value)
    db.commit()


def clear_entry_review(db: Session, entry: LoreEntry) -> None:
    entry.review_note = ""
    db.commit()


def delete_entry(db: Session, entry: LoreEntry) -> None:
    db.delete(entry)
    db.commit()


def update_artefact(db: Session, artefact: Artefact, values: Mapping[str, str]) -> None:
    cleaned = {
        key: _clean(values.get(key, ""), limit, key) for key, limit in ARTEFACT_LIMITS.items()
    }
    for key, value in cleaned.items():
        setattr(artefact, key, value)
    db.commit()


def clear_artefact_review(db: Session, artefact: Artefact) -> None:
    artefact.review_note = ""
    db.commit()
