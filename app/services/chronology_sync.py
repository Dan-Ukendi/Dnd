import html
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chronology import EventVisibility, Importance, Origin
from app.config import WA_TIMELINE_TITLE, get_settings
from app.models import SyncRun, TimelineEvent, utcnow
from app.services.chronology import (
    ChronologyError,
    EventData,
    apply_content,
    fold,
    parse_day,
    replace_source_links,
    validate_event_data,
)
from app.worldanvil.client import WorldAnvilSource
from app.worldanvil.dates import parse_wa_date

logger = logging.getLogger(__name__)

UUID_RE = re.compile(r"^[0-9a-f-]{36}$")
FIELD_RE = re.compile(r"^\s*-\s*\[b\]\s*(?P<label>[^\[\]:]+?)\s*:\s*\[/b\]\s*(?P<value>.*?)\s*$")
PARAGRAPH_RE = re.compile(r"\[p\](?P<text>.*?)\[/p\]", re.S)
TAG_RE = re.compile(r"\[/?[a-z0-9]+(?:[:=|][^\]]*)?\]", re.I)
CODE_RE = re.compile(r"\[b\]Code :\[/b\] (\S+)")
CHRONICLE_RE = re.compile(r"s[ée]ance\s+(?P<session>\d+)(?:.*?jour\s+(?P<day>J\d))?", re.I)


@dataclass(frozen=True)
class ParsedHistory:
    summary: str
    place: str | None
    origin: str
    linked_titles: tuple[str, ...]
    session: int | None
    day: str | None
    code: str | None


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub("", value))).strip()


def parse_history_content(content: str) -> ParsedHistory:
    fields: dict[str, str] = {}
    for line in content.splitlines():
        match = FIELD_RE.match(line)
        if match is not None:
            fields[fold(match.group("label"))] = _plain(match.group("value"))
    paragraphs = [_plain(match.group("text")) for match in PARAGRAPH_RE.finditer(content)]
    summary = "\n\n".join(text for text in paragraphs if text)
    if not summary and not fields:
        summary = _plain(content)
    origin_value = fold(fields.get("origine", ""))
    origin = Origin.CANON if origin_value == Origin.CANON else Origin.ADDITION
    linked = tuple(item.strip() for item in fields.get("lie a", "").split(";") if item.strip())
    session = day = None
    chronicle = CHRONICLE_RE.search(fields.get("chronique", ""))
    if chronicle is not None:
        session = int(chronicle.group("session"))
        try:
            day = parse_day((chronicle.group("day") or "").upper())
        except ChronologyError:
            day = None
    code_match = CODE_RE.search(content)
    code = code_match.group(1) if code_match else (fields.get("code") or None)
    return ParsedHistory(
        summary=summary,
        place=fields.get("lieu") or None,
        origin=origin,
        linked_titles=linked,
        session=session,
        day=day,
        code=code,
    )


def importance_from_significance(value: object) -> str:
    try:
        significance = int(str(value))
    except ValueError:
        significance = 0
    if significance >= 4:
        return Importance.MAJOR
    if significance >= 2:
        return Importance.NOTABLE
    return Importance.MINOR


def history_data(detail: dict[str, Any], parsed: ParsedHistory, code: str) -> EventData:
    return validate_event_data(
        {
            "code": code,
            "title": detail.get("title") if isinstance(detail.get("title"), str) else None,
            "start_year": detail.get("year"),
            "end_year": detail.get("endingYear"),
            "importance": importance_from_significance(detail.get("significance")),
            "origin": parsed.origin,
            "place": parsed.place[:300] if parsed.place else None,
            "summary": parsed.summary,
        }
    )


def find_timeline(source: WorldAnvilSource, world_id: str) -> dict[str, Any] | None:
    timeline_id = get_settings().wa_timeline_id.strip()
    if not timeline_id:
        wanted = fold(WA_TIMELINE_TITLE)
        timeline_id = next(
            (
                str(item.get("id"))
                for item in source.iter_world_timelines(world_id)
                if isinstance(item.get("title"), str) and fold(item["title"]) == wanted
            ),
            "",
        )
    if not timeline_id:
        return None
    return source.get_timeline(timeline_id, granularity=2)


def _content_state(event: TimelineEvent) -> tuple[object, ...]:
    return (
        event.title,
        event.summary,
        event.start_year,
        event.end_year,
        event.place,
        event.importance,
        event.origin,
    )


def _chronological_order(events: list[TimelineEvent], year: int) -> int:
    before = [event.sort_order for event in events if event.start_year <= year]
    if before:
        return max(before) + 1
    return min((event.sort_order for event in events), default=1) - 1


def sync_timeline(db: Session, source: WorldAnvilSource, world_id: str, run: SyncRun) -> None:
    timeline = find_timeline(source, world_id)
    if timeline is None:
        logger.info("No chronology timeline found on World Anvil")
        return
    refs = timeline.get("histories") or []
    if not isinstance(refs, list):
        raise ChronologyError("invalid_timeline")
    events = list(db.scalars(select(TimelineEvent)).all())
    by_history = {event.wa_history_id: event for event in events if event.wa_history_id}
    by_code = {event.code: event for event in events}
    seen: set[str] = set()
    fetched: list[tuple[TimelineEvent, ParsedHistory]] = []
    now = utcnow()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        history_id = str(ref.get("id") or "").lower()
        if not UUID_RE.match(history_id):
            logger.warning("Skipping history with unexpected id format")
            continue
        seen.add(history_id)
        updated_at = parse_wa_date(ref.get("updateDate"))
        event = by_history.get(history_id)
        if (
            not run.full
            and event is not None
            and event.removed_at is None
            and updated_at is not None
            and event.wa_updated_at == updated_at
        ):
            continue
        detail = source.get_history(history_id, granularity=1)
        content = detail.get("content") if isinstance(detail.get("content"), str) else ""
        parsed = parse_history_content(content)
        if event is None and parsed.code:
            candidate = by_code.get(parsed.code)
            if candidate is not None and candidate.wa_history_id in (None, history_id):
                event = candidate
        code = event.code if event is not None else parsed.code or ""
        if event is None and (not code or code in by_code):
            code = f"WA-{history_id[:8]}"
        try:
            data = history_data(detail, parsed, code)
        except ChronologyError as exc:
            logger.warning("Skipping history %s: %s", history_id, exc.code)
            continue
        if event is None:
            event = TimelineEvent(
                code=code,
                wa_history_id=history_id,
                visibility=EventVisibility.DM,
                chronicle=parsed.session is not None,
                session=parsed.session,
                day=parsed.day,
                sort_order=_chronological_order(events, data.start_year),
            )
            db.add(event)
            events.append(event)
            by_code[code] = event
            run.events_created += 1
            changed = False
        else:
            before = _content_state(event)
            apply_content(event, data)
            changed = event.removed_at is not None or _content_state(event) != before
        apply_content(event, data)
        if changed:
            run.events_updated += 1
        event.wa_history_id = history_id
        by_history[history_id] = event
        event.wa_updated_at = updated_at
        event.synced_at = now
        event.removed_at = None
        fetched.append((event, parsed))
    for event in events:
        if event.wa_history_id and event.wa_history_id not in seen and event.removed_at is None:
            event.removed_at = now
            run.events_removed += 1
    db.flush()
    index: dict[str, int] = {}
    for event in events:
        if event.removed_at is None:
            index.setdefault(fold(event.title), event.id)
            index.setdefault(fold(event.code), event.id)
    for event, parsed in fetched:
        replace_source_links(db, event, resolve_links(parsed.linked_titles, index))


def resolve_links(segments: tuple[str, ...], index: dict[str, int]) -> list[int]:
    targets: list[int] = []
    start = 0
    while start < len(segments):
        for end in range(len(segments), start, -1):
            key = fold(" ; ".join(segments[start:end]))
            if key in index:
                targets.append(index[key])
                start = end
                break
        else:
            logger.debug("Unresolved chronology link")
            start += 1
    return targets
