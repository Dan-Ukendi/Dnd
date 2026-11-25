import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PLACE_KINDS,
    STRUCTURE_STATUSES,
    STRUCTURE_VISIBILITIES,
    Place,
    Position,
    WaArticle,
)
from app.services.imports import ImportLine
from app.services.media import find_article
from app.services.structure_excerpts import AnchorStatus, find_excerpt

PLACE_KEYS = frozenset(
    {
        "key",
        "name",
        "kind",
        "parent",
        "article",
        "article_id",
        "owner",
        "owner_id",
        "location",
        "source",
        "source_id",
        "anchor",
        "description",
        "gm_notes",
        "visibility",
        "status",
    }
)
POSITION_KEYS = frozenset(
    {
        "key",
        "organisation",
        "organisation_id",
        "place",
        "person",
        "person_id",
        "name",
        "title",
        "scope",
        "superior",
        "site",
        "head",
        "group",
        "hierarchy",
        "source",
        "source_id",
        "anchor",
        "gm_notes",
        "visibility",
        "status",
    }
)
LIMITS = {"key": 80, "name": 200, "title": 300, "scope": 200, "location": 300, "anchor": 300}
PLACE_COLUMNS = (
    "name",
    "kind",
    "parent_id",
    "wa_article_id",
    "owner_id",
    "location",
    "source_article_id",
    "source_anchor",
    "description",
    "gm_notes",
    "visibility",
    "status",
    "position",
)
POSITION_COLUMNS = (
    "organisation_id",
    "place_id",
    "person_article_id",
    "name",
    "title",
    "scope",
    "superior_id",
    "site_id",
    "is_head",
    "is_group",
    "in_hierarchy",
    "source_article_id",
    "source_anchor",
    "gm_notes",
    "visibility",
    "status",
    "position",
)


class StructureError(Exception):
    pass


@dataclass
class _Entry:
    file: str
    data: dict[str, Any]
    position: int
    values: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        value = self.data.get("key")
        return value.strip() if isinstance(value, str) else ""


def load_structure_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StructureError(f"cannot read {path.name}: {exc}") from exc
    unknown = set(data) - {"places", "positions"}
    if unknown:
        raise StructureError(f"{path.name}: unknown sections {sorted(unknown)}")
    for section in ("places", "positions"):
        entries = data.get(section, [])
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise StructureError(f"{path.name}: '{section}' must be an array of tables")
    return data


def structure_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.toml")))
        elif path.is_file():
            files.append(path)
    return files


class _Articles:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cache: dict[tuple[str, str], WaArticle | LookupError] = {}

    def resolve(self, entry: _Entry, name: str, required: bool = False) -> str | None:
        title = _text(entry.data, name)
        wa_id = _text(entry.data, f"{name}_id")
        if title is None and wa_id is None:
            if required:
                entry.errors.append(f"'{name}' is required")
            return None
        cache_key = (wa_id or "", title or "")
        if cache_key not in self.cache:
            try:
                self.cache[cache_key] = find_article(self.db, wa_id=wa_id, title=title)
            except LookupError as exc:
                self.cache[cache_key] = exc
        found = self.cache[cache_key]
        if isinstance(found, LookupError):
            entry.errors.append(f"{name}: {found}")
            return None
        return found.wa_id


def _text(data: dict[str, Any], name: str) -> str | None:
    value = data.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _common(entry: _Entry, allowed: frozenset[str], articles: _Articles) -> None:
    data = entry.data
    unknown = sorted(set(data) - allowed)
    if unknown:
        entry.errors.append(f"unknown fields {unknown}")
    for name, value in data.items():
        if name in {"head", "group", "hierarchy"}:
            if not isinstance(value, bool):
                entry.errors.append(f"'{name}' must be true or false")
        elif not isinstance(value, str):
            entry.errors.append(f"'{name}' must be a string")
    if not entry.key:
        entry.errors.append("'key' is required")
    for name, limit in LIMITS.items():
        value = data.get(name)
        if isinstance(value, str) and len(value.strip()) > limit:
            entry.errors.append(f"'{name}' is longer than {limit} characters")
    visibility = _text(data, "visibility") or "mj"
    status = _text(data, "status") or "a_valider"
    if visibility not in STRUCTURE_VISIBILITIES:
        entry.errors.append(f"visibility must be one of {', '.join(STRUCTURE_VISIBILITIES)}")
    if status not in STRUCTURE_STATUSES:
        entry.errors.append(f"status must be one of {', '.join(STRUCTURE_STATUSES)}")
    source = articles.resolve(entry, "source", required=True)
    anchor = _text(data, "anchor") or ""
    if anchor and source is not None:
        article = articles.db.get(WaArticle, source)
        found = find_excerpt(article.content if article else "", anchor)
        if found.status == AnchorStatus.FORBIDDEN:
            entry.errors.append(f'anchor "{anchor}" points into a section reserved to the DM')
        elif found.status == AnchorStatus.MISSING:
            entry.warnings.append(f'anchor "{anchor}" not found in the source article')
    entry.values.update(
        {
            "source_article_id": source,
            "source_anchor": anchor,
            "gm_notes": _text(data, "gm_notes") or "",
            "visibility": visibility,
            "status": status,
            "position": entry.position,
        }
    )


def _prepare_place(entry: _Entry, articles: _Articles) -> None:
    _common(entry, PLACE_KEYS, articles)
    name = _text(entry.data, "name")
    kind = _text(entry.data, "kind")
    if name is None:
        entry.errors.append("'name' is required")
    if kind not in PLACE_KINDS:
        entry.errors.append(f"kind must be one of {', '.join(PLACE_KINDS)}")
    entry.values.update(
        {
            "name": name or "",
            "kind": kind or "",
            "wa_article_id": articles.resolve(entry, "article"),
            "owner_id": articles.resolve(entry, "owner"),
            "location": _text(entry.data, "location") or "",
            "description": _text(entry.data, "description") or "",
        }
    )
    parent = _text(entry.data, "parent")
    if parent:
        entry.refs["parent"] = parent


def _prepare_position(entry: _Entry, articles: _Articles) -> None:
    _common(entry, POSITION_KEYS, articles)
    organisation = articles.resolve(entry, "organisation")
    place = _text(entry.data, "place")
    has_organisation = any(name in entry.data for name in ("organisation", "organisation_id"))
    if has_organisation == (place is not None):
        entry.errors.append("give exactly one of 'organisation' or 'place'")
    person = articles.resolve(entry, "person")
    name = _text(entry.data, "name") or ""
    title = _text(entry.data, "title") or ""
    if not (person or name or title):
        entry.errors.append("give a 'person', a 'name' or a 'title'")
    entry.values.update(
        {
            "organisation_id": organisation,
            "person_article_id": person,
            "name": name,
            "title": title,
            "scope": _text(entry.data, "scope") or "",
            "is_head": entry.data.get("head") is True,
            "is_group": entry.data.get("group") is True,
            "in_hierarchy": entry.data.get("hierarchy", True) is not False,
        }
    )
    for ref in ("place", "superior", "site"):
        value = _text(entry.data, ref)
        if value:
            entry.refs[ref] = value


def _snapshot(row: Place | Position, columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(getattr(row, column) for column in columns)


def _find_cycle(parents: dict[int, int | None]) -> int | None:
    for start in parents:
        seen = {start}
        current = parents.get(start)
        while current is not None:
            if current in seen:
                return start
            seen.add(current)
            current = parents.get(current)
    return None


def _apply(
    db: Session,
    entries: list[_Entry],
    model: type[Place] | type[Position],
    columns: tuple[str, ...],
) -> tuple[dict[str, Place | Position], dict[str, tuple[Any, ...] | None]]:
    existing = {row.key: row for row in db.scalars(select(model))}
    before: dict[str, tuple[Any, ...] | None] = {}
    rows: dict[str, Place | Position] = {}
    for entry in entries:
        row = existing.get(entry.key)
        before[entry.key] = _snapshot(row, columns) if row is not None else None
        if row is None:
            row = model(key=entry.key)
            db.add(row)
        for column, value in entry.values.items():
            setattr(row, column, value)
        rows[entry.key] = row
    db.flush()
    return rows, before


def _status(before: tuple[Any, ...] | None, row: Place | Position, columns: tuple[str, ...]) -> str:
    if before is None:
        return "created"
    return "unchanged" if before == _snapshot(row, columns) else "updated"


def _check_unique(entries: list[_Entry], kind: str) -> None:
    seen: dict[str, str] = {}
    for entry in entries:
        if entry.key and entry.key in seen:
            entry.errors.append(f"{kind} key already used in {seen[entry.key]}")
        seen.setdefault(entry.key, entry.file)


def import_structures(
    db: Session,
    documents: list[tuple[str, dict[str, Any]]],
    *,
    dry_run: bool = False,
    prune: bool = False,
) -> list[ImportLine]:
    articles = _Articles(db)
    places: list[_Entry] = []
    positions: list[_Entry] = []
    counter = 0
    for name, data in documents:
        for section, target in (("places", places), ("positions", positions)):
            for item in data.get(section, []):
                counter += 1
                target.append(_Entry(name, item, counter))
    for entry in places:
        _prepare_place(entry, articles)
    for entry in positions:
        _prepare_position(entry, articles)
    _check_unique(places, "place")
    _check_unique(positions, "position")
    _check_unique_articles(places)
    if prune:
        _check_kept_references(places, positions)
    statuses: dict[tuple[str, str], str] = {}
    removed: list[str] = []
    try:
        if not _has_errors(places, positions):
            statuses, removed = _write(db, places, positions, prune)
    except Exception:
        db.rollback()
        raise
    lines = _lines(places, positions, statuses, removed, dry_run)
    if dry_run or any(line.failed for line in lines):
        db.rollback()
    else:
        db.commit()
    return lines


def _has_errors(places: list[_Entry], positions: list[_Entry]) -> bool:
    return any(entry.errors for entry in places + positions)


def _check_kept_references(places: list[_Entry], positions: list[_Entry]) -> None:
    place_keys = {entry.key for entry in places}
    position_keys = {entry.key for entry in positions}
    for entry in places:
        parent = entry.refs.get("parent")
        if parent and parent not in place_keys:
            entry.errors.append(f"parent: '{parent}' is not in the files and would be pruned")
    for entry in positions:
        for ref in ("place", "site"):
            target = entry.refs.get(ref)
            if target and target not in place_keys:
                entry.errors.append(f"{ref}: '{target}' is not in the files and would be pruned")
        superior = entry.refs.get("superior")
        if superior and superior not in position_keys:
            entry.errors.append(f"superior: '{superior}' is not in the files and would be pruned")


def _write(
    db: Session, places: list[_Entry], positions: list[_Entry], prune: bool
) -> tuple[dict[tuple[str, str], str], list[str]]:
    place_rows, place_before = _apply(db, places, Place, PLACE_COLUMNS)
    places_by_key = {row.key: row for row in db.scalars(select(Place))}
    _link(places, place_rows, places_by_key, "parent", "parent_id")
    db.flush()
    cycle = _find_cycle({row.id: row.parent_id for row in places_by_key.values()})
    if cycle is not None:
        key = next(row.key for row in places_by_key.values() if row.id == cycle)
        _error_for(places, key, "places form a cycle through their parents")
    for entry in positions:
        for ref, column in (("place", "place_id"), ("site", "site_id")):
            target = entry.refs.get(ref)
            if target is not None and target not in places_by_key:
                entry.errors.append(f"{ref}: unknown place '{target}'")
            entry.values[column] = places_by_key[target].id if target in places_by_key else None
    if _has_errors(places, positions):
        return {}, []
    position_rows, position_before = _apply(db, positions, Position, POSITION_COLUMNS)
    positions_by_key = {row.key: row for row in db.scalars(select(Position))}
    _link(positions, position_rows, positions_by_key, "superior", "superior_id")
    db.flush()
    _check_superiors(positions, positions_by_key)
    removed = _prune(db, places, positions) if prune else []
    db.flush()
    statuses = {
        ("place", key): _status(place_before[key], row, PLACE_COLUMNS)
        for key, row in place_rows.items()
    }
    statuses.update(
        {
            ("position", key): _status(position_before[key], row, POSITION_COLUMNS)
            for key, row in position_rows.items()
        }
    )
    return statuses, removed


def _check_unique_articles(places: list[_Entry]) -> None:
    seen: dict[str, str] = {}
    for entry in places:
        wa_id = entry.values.get("wa_article_id")
        if not wa_id:
            continue
        if wa_id in seen:
            entry.errors.append(f"article already used by place '{seen[wa_id]}'")
        seen.setdefault(wa_id, entry.key)


def _link(
    entries: list[_Entry],
    rows: dict[str, Place | Position],
    by_key: dict[str, Any],
    ref: str,
    column: str,
) -> None:
    for entry in entries:
        target = entry.refs.get(ref)
        if target is None:
            setattr(rows[entry.key], column, None)
        elif target not in by_key:
            entry.errors.append(f"{ref}: unknown key '{target}'")
        elif target == entry.key:
            entry.errors.append(f"{ref}: an entry cannot point to itself")
        else:
            setattr(rows[entry.key], column, by_key[target].id)


def _error_for(entries: list[_Entry], key: str, message: str) -> None:
    for entry in entries:
        if entry.key == key:
            entry.errors.append(message)
            return
    if entries:
        entries[0].errors.append(f"{message} (via '{key}')")


def _check_superiors(entries: list[_Entry], by_key: dict[str, Position]) -> None:
    by_id = {row.id: row for row in by_key.values()}
    for entry in entries:
        row = by_key[entry.key]
        superior = by_id.get(row.superior_id) if row.superior_id else None
        if superior is None:
            continue
        if row.organisation_id is not None and superior.organisation_id != row.organisation_id:
            entry.errors.append("superior must belong to the same organisation")
        if row.place_id is not None and superior.place_id is None:
            entry.errors.append("superior must be attached to a place as well")
    cycle = _find_cycle({row.id: row.superior_id for row in by_key.values()})
    if cycle is not None:
        _error_for(entries, by_id[cycle].key, "positions form a cycle through their superiors")


def _prune(db: Session, places: list[_Entry], positions: list[_Entry]) -> list[str]:
    keep_places = {entry.key for entry in places}
    keep_positions = {entry.key for entry in positions}
    removed: list[str] = []
    for row in db.scalars(select(Position).order_by(Position.key)):
        if row.key not in keep_positions:
            removed.append(f"position {row.key}")
            db.delete(row)
    db.flush()
    for row in db.scalars(select(Place).order_by(Place.key)):
        if row.key not in keep_places:
            removed.append(f"place {row.key}")
            db.delete(row)
    return removed


def _lines(
    places: list[_Entry],
    positions: list[_Entry],
    statuses: dict[tuple[str, str], str],
    removed: list[str],
    dry_run: bool,
) -> list[ImportLine]:
    lines: list[ImportLine] = []
    for kind, entries in (("place", places), ("position", positions)):
        for entry in entries:
            label = f"{kind} {entry.key or '?'}"
            if entry.errors:
                detail = f"({entry.file}) {'; '.join(entry.errors)}"
                lines.append(ImportLine("failed", label, detail))
                continue
            status = statuses.get((kind, entry.key), "checked")
            lines.append(ImportLine(status, label, f"({entry.file})"))
            lines.extend(ImportLine("warning", label, warning) for warning in entry.warnings)
    lines.extend(ImportLine("removed", item, "(--prune)") for item in removed)
    if dry_run:
        lines = [
            ImportLine(line.status, line.label, f"{line.detail} [dry run]".strip())
            for line in lines
        ]
    return lines
