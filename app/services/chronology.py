import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import Select, delete, or_, select
from sqlalchemy.orm import Session

from app.chronology import (
    CURRENT_YEAR,
    ERAS,
    ERAS_BY_SLUG,
    MILESTONES,
    Era,
    EventStatus,
    EventVisibility,
    Importance,
    Milestone,
    Origin,
    SessionDay,
    era_of,
    format_year,
)
from app.config import WA_HISTORY_EDIT_URL
from app.models import (
    TimelineEra,
    TimelineEvent,
    TimelineEventFiche,
    TimelineEventLink,
    WaArticle,
    utcnow,
)

DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "timeline_events.json"
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_TITLE = 300
MAX_PLACE = 300
MAX_CODE = 40
MAX_SUMMARY = 20_000
MAX_ERA_NAME = 100
MAX_ERA_SUBTITLE = 300
MAX_SORT_ORDER = 1_000_000
MAX_SESSION = 999
CLUSTER_MIN = 3


class ChronologyError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EventData:
    code: str
    title: str
    start_year: int
    end_year: int | None
    importance: str
    origin: str
    place: str | None
    summary: str
    linked: tuple[str, ...] = ()
    status: str = EventStatus.PENDING
    visibility: str = EventVisibility.DM
    chronicle: bool = False
    session: int | None = None
    day: str | None = None
    order: int = 0
    wa_history_id: str | None = None


def _text(value: object, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ChronologyError("invalid_text")
    text = value.strip()
    if len(text) > limit:
        raise ChronologyError("text_too_long")
    return text


def _year(value: object, *, required: bool) -> int | None:
    if value is None or value == "":
        if required:
            raise ChronologyError("missing_start_year")
        return None
    if isinstance(value, bool):
        raise ChronologyError("invalid_year")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d{1,6}\s*", value):
        return int(value)
    raise ChronologyError("invalid_year")


def _choice(value: object, allowed: Iterable[str], code: str, default: str | None = None) -> str:
    if (value is None or value == "") and default is not None:
        return default
    if not isinstance(value, str) or value not in set(allowed):
        raise ChronologyError(code)
    return value


def parse_day(value: object) -> str | None:
    if value is None or value == "":
        return None
    return _choice(value, [day.value for day in SessionDay], "invalid_day")


def parse_session(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_SESSION:
        raise ChronologyError("invalid_session")
    return value


def parse_order(value: object) -> int:
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d{1,7}\s*", value):
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or abs(value) > MAX_SORT_ORDER:
        raise ChronologyError("invalid_order")
    return value


def validate_event_data(raw: Mapping[str, Any]) -> EventData:
    code = _text(raw.get("code"), MAX_CODE)
    if not code or not re.fullmatch(r"[A-Za-z0-9-]+", code):
        raise ChronologyError("invalid_code")
    title = _text(raw.get("title"), MAX_TITLE)
    if not title:
        raise ChronologyError("missing_title")
    start_year = _year(raw.get("start_year"), required=True)
    assert start_year is not None
    end_year = _year(raw.get("end_year"), required=False)
    if end_year is not None and end_year < start_year:
        raise ChronologyError("end_before_start")
    linked = raw.get("linked") or []
    if not isinstance(linked, list) or not all(isinstance(item, str) for item in linked):
        raise ChronologyError("invalid_links")
    history_id = raw.get("wa_history_id")
    if history_id is not None and not (
        isinstance(history_id, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", history_id)
    ):
        raise ChronologyError("invalid_history_id")
    chronicle = raw.get("chronicle", False)
    if not isinstance(chronicle, bool):
        raise ChronologyError("invalid_chronicle")
    return EventData(
        code=code,
        title=title,
        start_year=start_year,
        end_year=end_year,
        importance=_choice(
            raw.get("importance"), [item.value for item in Importance], "invalid_importance"
        ),
        origin=_choice(raw.get("origin"), [item.value for item in Origin], "invalid_origin"),
        place=_text(raw.get("place"), MAX_PLACE) or None,
        summary=_text(raw.get("summary"), MAX_SUMMARY),
        linked=tuple(dict.fromkeys(item.strip() for item in linked if item.strip())),
        status=_choice(
            raw.get("status"),
            [item.value for item in EventStatus],
            "invalid_status",
            EventStatus.PENDING,
        ),
        visibility=_choice(
            raw.get("visibility"),
            [item.value for item in EventVisibility],
            "invalid_visibility",
            EventVisibility.DM,
        ),
        chronicle=chronicle,
        session=parse_session(raw.get("session")),
        day=parse_day(raw.get("day")),
        order=parse_order(raw.get("order", 0)),
        wa_history_id=history_id.lower() if isinstance(history_id, str) else None,
    )


def load_dataset(path: Path = DATASET_PATH) -> list[EventData]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ChronologyError("invalid_dataset")
    events = [validate_event_data(row) for row in rows]
    codes = [event.code for event in events]
    if len(set(codes)) != len(codes):
        raise ChronologyError("duplicate_code")
    return events


def apply_content(event: TimelineEvent, data: EventData) -> None:
    event.title = data.title
    event.summary = data.summary
    event.start_year = data.start_year
    event.end_year = data.end_year
    event.place = data.place
    event.importance = data.importance
    event.origin = data.origin


def _apply_app_fields(event: TimelineEvent, data: EventData) -> None:
    event.status = data.status
    event.visibility = data.visibility
    event.chronicle = data.chronicle
    event.session = data.session
    event.day = data.day
    event.sort_order = data.order


def _event_state(event: TimelineEvent) -> tuple[object, ...]:
    return (
        event.title,
        event.summary,
        event.start_year,
        event.end_year,
        event.place,
        event.importance,
        event.origin,
        event.status,
        event.visibility,
        event.chronicle,
        event.session,
        event.day,
        event.sort_order,
    )


def replace_source_links(
    db: Session, event: TimelineEvent, target_ids: Iterable[int], origin: str = "wa"
) -> bool:
    wanted = {target for target in target_ids if target != event.id}
    rows = db.scalars(
        select(TimelineEventLink).where(TimelineEventLink.source_id == event.id)
    ).all()
    current = {row.target_id: row for row in rows}
    changed = False
    for target, row in current.items():
        if row.origin == origin and target not in wanted:
            db.delete(row)
            changed = True
    for target in wanted - set(current):
        db.add(TimelineEventLink(source_id=event.id, target_id=target, origin=origin))
        changed = True
    return changed


@dataclass
class SeedReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0


def load_seed(db: Session, dataset: list[EventData] | None = None) -> SeedReport:
    dataset = load_dataset() if dataset is None else dataset
    report = SeedReport()
    existing = {event.code: event for event in db.scalars(select(TimelineEvent)).all()}
    used_history_ids = {event.wa_history_id for event in existing.values() if event.wa_history_id}
    pending: list[tuple[TimelineEvent, EventData, bool, bool]] = []
    for data in dataset:
        event = existing.get(data.code)
        if event is None:
            if data.wa_history_id and data.wa_history_id in used_history_ids:
                report.skipped += 1
                continue
            event = TimelineEvent(code=data.code, wa_history_id=data.wa_history_id)
            apply_content(event, data)
            _apply_app_fields(event, data)
            db.add(event)
            existing[data.code] = event
            pending.append((event, data, True, True))
        elif event.modified_by_dm:
            report.skipped += 1
        elif event.synced_at is not None:
            report.unchanged += 1
        else:
            before = _event_state(event)
            apply_content(event, data)
            _apply_app_fields(event, data)
            pending.append((event, data, False, _event_state(event) != before))
    db.flush()
    ids = {code: event.id for code, event in existing.items()}
    for event, data, created, changed in pending:
        targets = [ids[code] for code in data.linked if code in ids]
        links_changed = replace_source_links(db, event, targets)
        if created:
            report.created += 1
        elif changed or links_changed:
            report.updated += 1
        else:
            report.unchanged += 1
    db.commit()
    return report


@dataclass(frozen=True)
class EraView:
    slug: str
    name: str
    subtitle: str
    color: str
    start: int | None
    end: int | None
    customized: bool
    default: Era

    @property
    def range_label(self) -> str:
        if self.start is None and self.end is not None:
            return f"Jusqu'en {format_year(self.end)}"
        if self.end is None and self.start is not None:
            return f"Depuis {format_year(self.start)}"
        return f"{format_year(self.start or 0)} – {format_year(self.end or 0)}"


def era_views(db: Session) -> list[EraView]:
    overrides = {row.slug: row for row in db.scalars(select(TimelineEra)).all()}
    views = []
    for era in ERAS:
        row = overrides.get(era.slug)
        views.append(
            EraView(
                slug=era.slug,
                name=(row.name if row and row.name else era.name),
                subtitle=(row.subtitle if row and row.subtitle else era.subtitle),
                color=(row.color if row and row.color else era.color),
                start=era.start,
                end=era.end,
                customized=row is not None and bool(row.name or row.subtitle or row.color),
                default=era,
            )
        )
    return views


def update_era(db: Session, slug: str, *, name: str, subtitle: str, color: str) -> None:
    era = ERAS_BY_SLUG.get(slug)
    if era is None:
        raise ChronologyError("unknown_era")
    name = name.strip()
    subtitle = subtitle.strip()
    color = color.strip()
    if not name or len(name) > MAX_ERA_NAME:
        raise ChronologyError("invalid_era_name")
    if len(subtitle) > MAX_ERA_SUBTITLE:
        raise ChronologyError("invalid_era_subtitle")
    if not COLOR_RE.match(color):
        raise ChronologyError("invalid_era_color")
    row = db.get(TimelineEra, slug) or TimelineEra(slug=slug)
    row.name = None if name == era.name else name
    row.subtitle = None if subtitle in ("", era.subtitle) else subtitle
    row.color = None if color.upper() == era.color.upper() else color.upper()
    db.add(row)
    db.commit()


def reset_era(db: Session, slug: str) -> None:
    if slug not in ERAS_BY_SLUG:
        raise ChronologyError("unknown_era")
    db.execute(delete(TimelineEra).where(TimelineEra.slug == slug))
    db.commit()


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


@dataclass(frozen=True)
class Filters:
    era: str = ""
    importance: str = ""
    origin: str = ""
    status: str = ""
    q: str = ""

    @classmethod
    def parse(cls, params: Mapping[str, str], *, dm: bool) -> "Filters":
        def pick(key: str, allowed: Iterable[str]) -> str:
            value = params.get(key, "")
            return value if value in set(allowed) else ""

        return cls(
            era=pick("ere", ERAS_BY_SLUG),
            importance=pick("importance", [item.value for item in Importance]),
            origin=pick("origine", [item.value for item in Origin]) if dm else "",
            status=pick("statut", [item.value for item in EventStatus]) if dm else "",
            q=params.get("q", "").strip()[:100],
        )

    @property
    def active(self) -> bool:
        return any((self.era, self.importance, self.origin, self.status, self.q))

    def params(self) -> dict[str, str]:
        pairs = {
            "ere": self.era,
            "importance": self.importance,
            "origine": self.origin,
            "statut": self.status,
            "q": self.q,
        }
        return {key: value for key, value in pairs.items() if value}

    def query(self, **extra: str | int) -> str:
        params: dict[str, str | int] = {**self.params(), **extra}
        return urlencode(params)

    def matches(self, event: TimelineEvent, *, dm: bool) -> bool:
        if self.era and era_of(event.start_year).slug != self.era:
            return False
        if self.importance and event.importance != self.importance:
            return False
        if self.origin and event.origin != self.origin:
            return False
        if self.status and event.status != self.status:
            return False
        if self.q:
            haystack = [event.title, event.summary, event.place or ""]
            if dm:
                haystack.append(event.code)
            folded = fold(" ".join(haystack))
            return all(word in folded for word in fold(self.q).split(" "))
        return True


def visible_query(*, dm: bool) -> Select[tuple[TimelineEvent]]:
    statement = select(TimelineEvent).where(TimelineEvent.removed_at.is_(None))
    if not dm:
        statement = statement.where(TimelineEvent.visibility == EventVisibility.PLAYERS)
    return statement.order_by(TimelineEvent.start_year, TimelineEvent.sort_order, TimelineEvent.id)


def visible_events(db: Session, *, dm: bool) -> list[TimelineEvent]:
    return list(db.scalars(visible_query(dm=dm)).all())


def get_visible_event(db: Session, event_id: int, *, dm: bool) -> TimelineEvent | None:
    event = db.get(TimelineEvent, event_id)
    if event is None or event.removed_at is not None:
        return None
    if not dm and event.visibility != EventVisibility.PLAYERS:
        return None
    return event


@dataclass(frozen=True)
class EventView:
    event: TimelineEvent
    era: EraView

    @property
    def id(self) -> int:
        return self.event.id


def to_views(events: Iterable[TimelineEvent], eras: list[EraView]) -> list[EventView]:
    by_slug = {era.slug: era for era in eras}
    return [EventView(event, by_slug[era_of(event.start_year).slug]) for event in events]


@dataclass(frozen=True)
class LinkedEvent:
    view: EventView
    origin: str


def linked_events(
    db: Session, event: TimelineEvent, eras: list[EraView], *, dm: bool
) -> list[LinkedEvent]:
    rows = db.execute(
        select(TimelineEventLink).where(
            or_(TimelineEventLink.source_id == event.id, TimelineEventLink.target_id == event.id)
        )
    ).scalars()
    origins: dict[int, str] = {}
    for row in rows:
        other = row.target_id if row.source_id == event.id else row.source_id
        if row.source_id == event.id or other not in origins:
            origins[other] = row.origin if row.source_id == event.id else "inverse"
    if not origins:
        return []
    others = db.scalars(visible_query(dm=dm).where(TimelineEvent.id.in_(list(origins)))).all()
    return [LinkedEvent(view, origins[view.event.id]) for view in to_views(others, eras)]


@dataclass(frozen=True)
class FicheLink:
    fiche: TimelineEventFiche
    href: str | None


def event_fiches(
    db: Session, event: TimelineEvent, *, dm: bool, accessible: set[str] | None = None
) -> list[FicheLink]:
    fiches = db.scalars(
        select(TimelineEventFiche)
        .where(TimelineEventFiche.event_id == event.id)
        .order_by(TimelineEventFiche.title, TimelineEventFiche.id)
    ).all()
    links = []
    for fiche in fiches:
        article = fiche.article
        href = None
        if article is not None and article.removed_at is None:
            if dm:
                href = f"/mj/articles/{article.wa_id}"
            elif accessible is not None and article.wa_id in accessible:
                href = f"/joueur/articles/{article.wa_id}"
            else:
                continue
        links.append(FicheLink(fiche, href))
    return links


def history_edit_url(event: TimelineEvent) -> str | None:
    if not event.wa_history_id:
        return None
    return WA_HISTORY_EDIT_URL.format(history_id=event.wa_history_id)


def _touch(db: Session, event: TimelineEvent) -> None:
    event.modified_by_dm = True
    event.updated_at = utcnow()
    db.commit()


def set_status(db: Session, event: TimelineEvent, status: str) -> None:
    event.status = _choice(status, [item.value for item in EventStatus], "invalid_status")
    _touch(db, event)


def set_visibility(db: Session, event: TimelineEvent, visibility: str) -> None:
    event.visibility = _choice(
        visibility, [item.value for item in EventVisibility], "invalid_visibility"
    )
    _touch(db, event)


def set_chronicle(
    db: Session, event: TimelineEvent, *, chronicle: bool, session: str, day: str, order: str
) -> None:
    parsed_session = parse_session(session)
    parsed_day = parse_day(day)
    parsed_order = parse_order(order)
    event.chronicle = chronicle
    event.session = parsed_session
    event.day = parsed_day
    event.sort_order = parsed_order
    _touch(db, event)


def add_link(db: Session, event: TimelineEvent, target_id: int) -> TimelineEvent:
    target = get_visible_event(db, target_id, dm=True)
    if target is None or target.id == event.id:
        raise ChronologyError("invalid_link")
    existing = db.scalar(
        select(TimelineEventLink).where(
            or_(
                (TimelineEventLink.source_id == event.id)
                & (TimelineEventLink.target_id == target.id),
                (TimelineEventLink.source_id == target.id)
                & (TimelineEventLink.target_id == event.id),
            )
        )
    )
    if existing is not None:
        raise ChronologyError("duplicate_link")
    db.add(TimelineEventLink(source_id=event.id, target_id=target.id, origin="mj"))
    _touch(db, event)
    return target


def remove_link(db: Session, event: TimelineEvent, target_id: int) -> None:
    link = db.scalar(
        select(TimelineEventLink).where(
            or_(
                (TimelineEventLink.source_id == event.id)
                & (TimelineEventLink.target_id == target_id),
                (TimelineEventLink.source_id == target_id)
                & (TimelineEventLink.target_id == event.id),
            ),
            TimelineEventLink.origin == "mj",
        )
    )
    if link is None:
        raise ChronologyError("unknown_link")
    db.delete(link)
    _touch(db, event)


def add_fiche(
    db: Session, event: TimelineEvent, *, article_id: str, title: str
) -> TimelineEventFiche:
    article_id = article_id.strip()
    title = title.strip()
    if article_id:
        article = db.get(WaArticle, article_id)
        if article is None or article.removed_at is not None:
            raise ChronologyError("invalid_fiche_article")
        fiche = TimelineEventFiche(
            event_id=event.id, wa_article_id=article.wa_id, title=article.title[:MAX_TITLE]
        )
    elif title and len(title) <= MAX_TITLE:
        fiche = TimelineEventFiche(event_id=event.id, title=title)
    else:
        raise ChronologyError("invalid_fiche_title")
    duplicate = db.scalar(
        select(TimelineEventFiche).where(
            TimelineEventFiche.event_id == event.id,
            TimelineEventFiche.wa_article_id == fiche.wa_article_id
            if fiche.wa_article_id
            else TimelineEventFiche.title == fiche.title,
        )
    )
    if duplicate is not None:
        raise ChronologyError("duplicate_fiche")
    db.add(fiche)
    _touch(db, event)
    return fiche


def remove_fiche(db: Session, event: TimelineEvent, fiche_id: int) -> None:
    fiche = db.get(TimelineEventFiche, fiche_id)
    if fiche is None or fiche.event_id != event.id:
        raise ChronologyError("unknown_fiche")
    db.delete(fiche)
    _touch(db, event)


@dataclass
class SessionGroup:
    session: int | None
    days: list[tuple[str | None, list[EventView]]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(items) for _, items in self.days)


def chronicle_sessions(views: Iterable[EventView]) -> list[SessionGroup]:
    grouped: dict[int | None, dict[str | None, list[EventView]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for view in views:
        if view.event.chronicle:
            grouped[view.event.session][view.event.day].append(view)
    day_order = {None: 2, SessionDay.DAY_1.value: 0, SessionDay.DAY_2.value: 1}
    groups = []
    for session in sorted(grouped, key=lambda value: (value is None, value or 0)):
        days = grouped[session]
        group = SessionGroup(session)
        for day in sorted(days, key=lambda value: day_order.get(value, 3)):
            items = sorted(
                days[day],
                key=lambda view: (view.event.sort_order, view.event.start_year, view.event.id),
            )
            group.days.append((day, items))
        groups.append(group)
    return groups


BAND_MIN_PX = 300
BAND_SLOT_PX = 40
BAND_PAD_PX = 26
POINT_GAP_PX = 26
RANGE_GAP_PX = 6
RANGE_MIN_PX = 8
MAX_POINT_LANES = 7


@dataclass(frozen=True)
class Band:
    era: EraView
    x: float
    width: float
    first_year: int
    last_year: int
    count: int

    @property
    def span(self) -> int:
        return self.last_year - self.first_year + 1


@dataclass
class Marker:
    kind: str
    year: int
    x: float
    width: float
    lane: int
    events: list[EventView]

    @property
    def first(self) -> EventView:
        return self.events[0]

    @property
    def importance(self) -> str:
        levels = [view.event.importance for view in self.events]
        for level in (Importance.MAJOR, Importance.NOTABLE, Importance.MINOR):
            if level in levels:
                return level
        return Importance.MINOR

    @property
    def pending(self) -> bool:
        return all(view.event.status == EventStatus.PENDING for view in self.events)


@dataclass(frozen=True)
class MilestoneMark:
    milestone: Milestone
    x: float


@dataclass(frozen=True)
class Tick:
    year: int
    x: float


@dataclass
class Frise:
    width: float
    bands: list[Band]
    ranges: list[Marker]
    points: list[Marker]
    milestones: list[MilestoneMark]
    ticks: list[Tick]
    range_lanes: int
    point_lanes: int


TICK_STEPS = (5, 10, 25, 50, 100, 200, 250, 500)
TICK_MIN_PX = 90


def _band_ticks(band: Band) -> list[int]:
    inner = band.width - 2 * BAND_PAD_PX
    step = next(
        (value for value in TICK_STEPS if value / band.span * inner >= TICK_MIN_PX),
        TICK_STEPS[-1],
    )
    first = -(-band.first_year // step) * step
    margin = band.span * 0.06
    return [
        year
        for year in range(first, band.last_year + 1, step)
        if band.first_year + margin <= year <= band.last_year - margin
    ]


def _band_domain(era: EraView, years: list[int]) -> tuple[int, int]:
    first = era.start
    last = era.end
    if first is None:
        lowest = min(years) if years else (last or 0) - 299
        first = (lowest - 25) // 50 * 50
    if last is None:
        last = max([CURRENT_YEAR, *years])
    return first, last


def build_frise(domain: list[EventView], shown: list[EventView], eras: list[EraView]) -> Frise:
    years_by_era: dict[str, list[int]] = defaultdict(list)
    for view in domain:
        years_by_era[view.era.slug].append(view.event.start_year)
    bands: list[Band] = []
    cursor = 0.0
    for era in eras:
        years = years_by_era.get(era.slug, [])
        first, last = _band_domain(era, years)
        width = float(max(BAND_MIN_PX, len(set(years)) * BAND_SLOT_PX))
        bands.append(Band(era, cursor, width, first, last, len(years)))
        cursor += width
    by_slug = {band.era.slug: band for band in bands}

    def position(year: int, offset: float) -> float:
        band = by_slug[era_of(year).slug]
        clamped = min(max(year, band.first_year), band.last_year)
        inner = band.width - 2 * BAND_PAD_PX
        return band.x + BAND_PAD_PX + (clamped - band.first_year + offset) / band.span * inner

    ranges: list[Marker] = []
    singles: dict[int, list[EventView]] = defaultdict(list)
    for view in shown:
        event = view.event
        if event.is_range and event.end_year is not None:
            left = position(event.start_year, 0)
            right = position(event.end_year, 1)
            ranges.append(
                Marker("range", event.start_year, left, max(RANGE_MIN_PX, right - left), 0, [view])
            )
        else:
            singles[event.start_year].append(view)
    points: list[Marker] = []
    for year in sorted(singles):
        views = singles[year]
        x = position(year, 0.5)
        if len(views) >= CLUSTER_MIN:
            points.append(Marker("cluster", year, x, 0, 0, views))
        else:
            points.extend(Marker("point", year, x, 0, 0, [view]) for view in views)

    range_ends: list[float] = []
    for marker in sorted(ranges, key=lambda item: (item.x, -item.width)):
        lane = next(
            (index for index, end in enumerate(range_ends) if end + RANGE_GAP_PX <= marker.x),
            None,
        )
        if lane is None:
            lane = len(range_ends)
            range_ends.append(0.0)
        marker.lane = lane
        range_ends[lane] = marker.x + marker.width

    point_ends: list[float] = []
    for marker in points:
        lane = next(
            (index for index, end in enumerate(point_ends) if end + POINT_GAP_PX <= marker.x),
            None,
        )
        if lane is None and len(point_ends) < MAX_POINT_LANES:
            lane = len(point_ends)
            point_ends.append(float("-inf"))
        if lane is None:
            lane = min(range(len(point_ends)), key=lambda index: point_ends[index])
        marker.lane = lane
        point_ends[lane] = marker.x

    milestones = [MilestoneMark(item, position(item.year, 0.5)) for item in MILESTONES]
    milestone_years = {item.year for item in MILESTONES}
    ticks = [
        Tick(year, position(year, 0.5))
        for band in bands
        for year in _band_ticks(band)
        if year not in milestone_years
    ]
    return Frise(
        width=cursor,
        bands=bands,
        ranges=ranges,
        points=points,
        milestones=milestones,
        ticks=ticks,
        range_lanes=len(range_ends),
        point_lanes=max(1, len(point_ends)),
    )


def group_by_era(
    views: Iterable[EventView], eras: list[EraView]
) -> list[tuple[EraView, list[EventView]]]:
    grouped: dict[str, list[EventView]] = defaultdict(list)
    for view in views:
        grouped[view.era.slug].append(view)
    return [(era, grouped[era.slug]) for era in eras if grouped.get(era.slug)]


def event_options(db: Session, eras: list[EraView]) -> list[EventView]:
    return to_views(visible_events(db, dm=True), eras)
