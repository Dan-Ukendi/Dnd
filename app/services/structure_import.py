import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PERSON_CATEGORIES,
    PLACE_KINDS,
    STRUCTURE_VISIBILITIES,
    FicheSection,
    Npc,
    NpcRelation,
    NpcRole,
    Place,
    Position,
    StatBlock,
    WaArticle,
)
from app.services.imports import ImportLine
from app.services.media import find_article
from app.services.structure_excerpts import AnchorStatus, find_excerpt

DOCUMENT_SECTIONS = ("statblocks", "places", "npcs", "roles", "positions")
NPC_SECTIONS = frozenset({"statblocks", "npcs", "roles"})
SECTION_KEYS = frozenset({"title", "text", "visibility"})
RELATION_KEYS = frozenset({"npc", "article", "article_id", "label", "reverse_label", "visibility"})
SECRET_TITLES = frozenset({"secret", "secrets", "secret (mj)", "accroches"})
KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BOOLEAN_FIELDS = frozenset({"head", "group", "hierarchy", "staff"})
STRUCTURED_FIELDS = frozenset({"sections", "relations", "previous_keys"})

PLACE_KEYS = frozenset(
    {
        "key",
        "previous_keys",
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
        "sections",
    }
)
POSITION_KEYS = frozenset(
    {
        "key",
        "previous_keys",
        "organisation",
        "organisation_id",
        "place",
        "person",
        "person_id",
        "npc",
        "npc_role",
        "name",
        "title",
        "scope",
        "superior",
        "site",
        "head",
        "group",
        "hierarchy",
        "staff",
        "source",
        "source_id",
        "anchor",
        "gm_notes",
        "visibility",
        "status",
    }
)
NPC_KEYS = frozenset(
    {
        "key",
        "previous_keys",
        "name",
        "title",
        "gender",
        "race",
        "age",
        "standing",
        "description",
        "gm_notes",
        "tag",
        "statblock",
        "organisation",
        "organisation_id",
        "place",
        "article",
        "article_id",
        "visibility",
        "status",
        "sections",
        "relations",
    }
)
ROLE_KEYS = frozenset(
    {
        "key",
        "previous_keys",
        "name",
        "description",
        "headcount",
        "gm_notes",
        "statblock",
        "organisation",
        "organisation_id",
        "place",
        "visibility",
        "status",
        "sections",
    }
)
STAT_TEXT_FIELDS = {
    "n": "name",
    "sz": "size",
    "tp": "creature_type",
    "al": "alignment",
    "acd": "armor_desc",
    "hd": "hit_dice",
    "spd": "speed",
    "sv": "saves",
    "sk": "skills",
    "dv": "vulnerabilities",
    "dr": "resistances",
    "di": "immunities",
    "ci": "condition_immunities",
    "se": "senses",
    "la": "languages",
    "cr": "challenge",
    "src": "source",
}
STAT_NUMBER_FIELDS = {"ac": "armor_class", "hp": "hit_points"}
STAT_LIST_FIELDS = {"tr": "traits", "ac_": "actions", "ba": "bonus_actions", "re": "reactions"}
STAT_KEYS = frozenset(
    {"key", "previous_keys", "ab", *STAT_TEXT_FIELDS, *STAT_NUMBER_FIELDS, *STAT_LIST_FIELDS}
)
STAT_ENTRY_KEYS = frozenset({"n", "t", "sp"})
LIMITS = {
    "key": 80,
    "name": 200,
    "title": 300,
    "scope": 200,
    "location": 300,
    "anchor": 300,
    "gender": 60,
    "race": 100,
    "age": 60,
    "standing": 300,
    "headcount": 100,
}
STAT_LIMITS = {
    "name": 200,
    "size": 40,
    "creature_type": 200,
    "alignment": 200,
    "armor_desc": 200,
    "hit_dice": 60,
    "speed": 200,
    "saves": 300,
    "skills": 500,
    "vulnerabilities": 300,
    "resistances": 300,
    "immunities": 300,
    "condition_immunities": 300,
    "senses": 300,
    "languages": 300,
    "challenge": 20,
    "source": 300,
}
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
    "position",
)
POSITION_COLUMNS = (
    "organisation_id",
    "place_id",
    "person_article_id",
    "npc_id",
    "npc_role_id",
    "name",
    "title",
    "scope",
    "superior_id",
    "site_id",
    "is_head",
    "is_group",
    "in_hierarchy",
    "is_staff",
    "source_article_id",
    "source_anchor",
    "gm_notes",
    "visibility",
    "position",
)
STAT_COLUMNS = (
    *STAT_TEXT_FIELDS.values(),
    *STAT_NUMBER_FIELDS.values(),
    "abilities",
    *STAT_LIST_FIELDS.values(),
    "position",
)
NPC_COLUMNS = (
    "name",
    "title",
    "gender",
    "race",
    "age",
    "standing",
    "description",
    "gm_notes",
    "category",
    "stat_block_id",
    "organisation_id",
    "place_id",
    "wa_article_id",
    "visibility",
    "position",
)
ROLE_COLUMNS = (
    "name",
    "description",
    "headcount",
    "gm_notes",
    "stat_block_id",
    "organisation_id",
    "place_id",
    "visibility",
    "position",
)
SECTION_OWNER = {"place": "place_id", "npc": "npc_id", "role": "npc_role_id"}


class StructureError(Exception):
    pass


@dataclass
class _Entry:
    file: str
    data: dict[str, Any]
    position: int
    values: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    sections: list[tuple[str, str, str]] | None = None
    relations: list[dict[str, Any]] = field(default_factory=list)
    previous: list[str] = field(default_factory=list)
    renamed_from: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        value = self.data.get("key")
        return value.strip() if isinstance(value, str) else ""


def load_structure_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return _load_json(path)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StructureError(f"cannot read {path.name}: {exc}") from exc
    unknown = set(data) - set(DOCUMENT_SECTIONS)
    if unknown:
        raise StructureError(f"{path.name}: unknown sections {sorted(unknown)}")
    for section in DOCUMENT_SECTIONS:
        entries = data.get(section, [])
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise StructureError(f"{path.name}: '{section}' must be an array of tables")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StructureError(f"cannot read {path.name}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("statblocks")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise StructureError(f"{path.name}: expected a list of stat blocks")
    return {"statblocks": data}


def structure_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted([*path.glob("*.toml"), *path.glob("*.json")]))
        elif path.is_file():
            files.append(path)
    return files


class _Articles:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.cache: dict[tuple[str, str], WaArticle | LookupError] = {}

    def resolve(
        self,
        entry: _Entry,
        name: str,
        required: bool = False,
        data: dict[str, Any] | None = None,
    ) -> str | None:
        source = entry.data if data is None else data
        title = _text(source, name)
        wa_id = _text(source, f"{name}_id")
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


def _visibility(entry: _Entry, value: object, default: str) -> str:
    visibility = value.strip() if isinstance(value, str) and value.strip() else default
    if visibility not in STRUCTURE_VISIBILITIES:
        entry.errors.append(f"visibility must be one of {', '.join(STRUCTURE_VISIBILITIES)}")
    return visibility


def _basics(entry: _Entry, allowed: frozenset[str]) -> None:
    data = entry.data
    unknown = sorted(set(data) - allowed)
    if unknown:
        entry.errors.append(f"unknown fields {unknown}")
    for name, value in data.items():
        if name in BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                entry.errors.append(f"'{name}' must be true or false")
        elif name not in STRUCTURED_FIELDS and not isinstance(value, str):
            entry.errors.append(f"'{name}' must be a string")
    if not entry.key:
        entry.errors.append("'key' is required")
    elif not KEY_RE.match(entry.key):
        entry.errors.append("'key' must be lowercase kebab-case")
    for name, limit in LIMITS.items():
        value = data.get(name)
        if isinstance(value, str) and len(value.strip()) > limit:
            entry.errors.append(f"'{name}' is longer than {limit} characters")
    previous = data.get("previous_keys", [])
    if isinstance(previous, str):
        previous = [previous]
    if not isinstance(previous, list) or not all(isinstance(item, str) for item in previous):
        entry.errors.append("'previous_keys' must be a list of keys")
        previous = []
    entry.previous = [item.strip() for item in previous if item.strip()]
    if entry.key in entry.previous:
        entry.errors.append("'previous_keys' cannot contain the key itself")


def _sections(entry: _Entry) -> None:
    raw = entry.data.get("sections", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        entry.errors.append("'sections' must be an array of tables")
        entry.sections = []
        return
    sections: list[tuple[str, str, str]] = []
    for index, item in enumerate(raw, start=1):
        unknown = sorted(set(item) - SECTION_KEYS)
        if unknown:
            entry.errors.append(f"section {index}: unknown fields {unknown}")
        if not all(isinstance(value, str) for value in item.values()):
            entry.errors.append(f"section {index}: every field must be a string")
            continue
        title = _text(item, "title")
        if title is None:
            entry.errors.append(f"section {index}: 'title' is required")
            continue
        if len(title) > 200:
            entry.errors.append(f"section {index}: 'title' is longer than 200 characters")
        visibility = _visibility(entry, item.get("visibility"), "joueurs")
        if title.casefold() in SECRET_TITLES:
            visibility = "mj"
        sections.append((title, (item.get("text") or "").strip(), visibility))
    entry.sections = sections


def _common(entry: _Entry, allowed: frozenset[str], articles: _Articles) -> None:
    _basics(entry, allowed)
    data = entry.data
    visibility = _visibility(entry, data.get("visibility"), "mj")
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
            "position": entry.position,
        }
    )


def _prepare_place(entry: _Entry, articles: _Articles) -> None:
    _common(entry, PLACE_KEYS, articles)
    _sections(entry)
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
    npc = _text(entry.data, "npc")
    npc_role = _text(entry.data, "npc_role")
    name = _text(entry.data, "name") or ""
    title = _text(entry.data, "title") or ""
    if not (person or npc or name or title):
        entry.errors.append("give a 'person', an 'npc', a 'name' or a 'title'")
    if sum(bool(value) for value in (person, npc, npc_role)) > 1:
        entry.errors.append("give at most one of 'person', 'npc' or 'npc_role'")
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
            "is_staff": entry.data.get("staff") is True,
        }
    )
    if entry.values["is_staff"] and entry.values["is_head"]:
        entry.errors.append("a staff position cannot be the head of a hierarchy")
    for ref in ("place", "superior", "site", "npc", "npc_role"):
        value = _text(entry.data, ref)
        if value:
            entry.refs[ref] = value


def _prepare_person(entry: _Entry, allowed: frozenset[str], articles: _Articles) -> None:
    _basics(entry, allowed)
    _sections(entry)
    data = entry.data
    name = _text(data, "name")
    if name is None:
        entry.errors.append("'name' is required")
    entry.values.update(
        {
            "name": name or "",
            "description": _text(data, "description") or "",
            "gm_notes": _text(data, "gm_notes") or "",
            "organisation_id": articles.resolve(entry, "organisation"),
            "visibility": _visibility(entry, data.get("visibility"), "mj"),
            "position": entry.position,
        }
    )
    for ref in ("statblock", "place"):
        value = _text(data, ref)
        if value:
            entry.refs[ref] = value


def _prepare_npc(entry: _Entry, articles: _Articles) -> None:
    _prepare_person(entry, NPC_KEYS, articles)
    data = entry.data
    category = _text(data, "tag") or "lore"
    if category not in PERSON_CATEGORIES:
        entry.errors.append(f"tag must be one of {', '.join(PERSON_CATEGORIES)}")
    entry.values.update(
        {
            "title": _text(data, "title") or "",
            "gender": _text(data, "gender") or "",
            "race": _text(data, "race") or "",
            "age": _text(data, "age") or "",
            "standing": _text(data, "standing") or "",
            "category": category,
            "wa_article_id": articles.resolve(entry, "article"),
        }
    )
    raw = data.get("relations", [])
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        entry.errors.append("'relations' must be an array of tables")
        return
    for index, item in enumerate(raw, start=1):
        unknown = sorted(set(item) - RELATION_KEYS)
        if unknown:
            entry.errors.append(f"relation {index}: unknown fields {unknown}")
        if not all(isinstance(value, str) for value in item.values()):
            entry.errors.append(f"relation {index}: every field must be a string")
            continue
        target = _text(item, "npc")
        article = articles.resolve(entry, "article", data=item)
        has_article = any(name in item for name in ("article", "article_id"))
        if bool(target) == has_article:
            entry.errors.append(f"relation {index}: give exactly one of 'npc' or 'article'")
        if target == entry.key:
            entry.errors.append(f"relation {index}: an NPC cannot point to itself")
        entry.relations.append(
            {
                "npc": target,
                "article": article,
                "label": (_text(item, "label") or "")[:200],
                "reverse_label": (_text(item, "reverse_label") or "")[:200],
                "visibility": _visibility(entry, item.get("visibility"), "mj"),
            }
        )


def _prepare_role(entry: _Entry, articles: _Articles) -> None:
    _prepare_person(entry, ROLE_KEYS, articles)
    entry.values["headcount"] = _text(entry.data, "headcount") or ""


def _stat_entries(entry: _Entry, name: str, value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        entry.errors.append(f"'{name}' must be a list of {{n, t}} objects")
        return []
    result = []
    for index, item in enumerate(value, start=1):
        unknown = sorted(set(item) - STAT_ENTRY_KEYS)
        if unknown:
            entry.errors.append(f"{name} {index}: unknown fields {unknown}")
        title, text = item.get("n"), item.get("t")
        if not isinstance(title, str) or not title.strip() or not isinstance(text, str):
            entry.errors.append(f"{name} {index}: 'n' and 't' must be strings")
            continue
        spells = item.get("sp", [])
        if not isinstance(spells, list) or not all(
            isinstance(row, list) and len(row) == 2 and all(isinstance(part, str) for part in row)
            for row in spells
        ):
            entry.errors.append(f"{name} {index}: 'sp' must be a list of [label, spells] pairs")
            spells = []
        found: dict[str, Any] = {"name": title.strip(), "text": text.strip()}
        if spells:
            found["spells"] = [[label.strip(), names.strip()] for label, names in spells]
        result.append(found)
    return result


def _prepare_stat_block(entry: _Entry) -> None:
    data = entry.data
    unknown = sorted(set(data) - STAT_KEYS)
    if unknown:
        entry.errors.append(f"unknown fields {unknown}")
    if not entry.key:
        entry.errors.append("'key' is required")
    elif not KEY_RE.match(entry.key):
        entry.errors.append("'key' must be lowercase kebab-case")
    previous = data.get("previous_keys", [])
    if isinstance(previous, list) and all(isinstance(item, str) for item in previous):
        entry.previous = [item.strip() for item in previous if item.strip()]
    else:
        entry.errors.append("'previous_keys' must be a list of keys")
    for short, column in STAT_TEXT_FIELDS.items():
        value = data.get(short, "")
        if not isinstance(value, str):
            entry.errors.append(f"'{short}' must be a string")
            value = ""
        value = value.strip()
        if len(value) > STAT_LIMITS[column]:
            entry.errors.append(f"'{short}' is longer than {STAT_LIMITS[column]} characters")
        entry.values[column] = value
    if not entry.values.get("name"):
        entry.errors.append("'n' (name) is required")
    for short, column in STAT_NUMBER_FIELDS.items():
        value = data.get(short)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            entry.errors.append(f"'{short}' must be a positive whole number")
            value = None
        entry.values[column] = value
    abilities = data.get("ab", [])
    if not (
        isinstance(abilities, list)
        and len(abilities) in (0, 6)
        and all(isinstance(score, int) and not isinstance(score, bool) for score in abilities)
    ):
        entry.errors.append("'ab' must list the six scores STR DEX CON INT WIS CHA")
        abilities = []
    entry.values["abilities"] = abilities
    for short, column in STAT_LIST_FIELDS.items():
        entry.values[column] = _stat_entries(entry, short, data.get(short, []))
    entry.values["position"] = entry.position


def _snapshot(row: object, columns: tuple[str, ...]) -> tuple[Any, ...]:
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
    db: Session, entries: list[_Entry], model: type[Any], columns: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, tuple[Any, ...] | None]]:
    existing = {row.key: row for row in db.scalars(select(model))}
    before: dict[str, tuple[Any, ...] | None] = {}
    rows: dict[str, Any] = {}
    for entry in entries:
        row = existing.get(entry.key)
        if row is None:
            older = [key for key in entry.previous if key in existing]
            if len(older) > 1:
                entry.errors.append(f"several previous keys exist: {', '.join(older)}")
                continue
            if older:
                row = existing.pop(older[0])
                entry.renamed_from = row.key
                row.key = entry.key
                existing[entry.key] = row
        else:
            stale = [key for key in entry.previous if key in existing]
            if stale:
                entry.errors.append(f"both '{entry.key}' and '{stale[0]}' exist")
                continue
        before[entry.key] = _snapshot(row, columns) if row is not None else None
        if row is None:
            row = model(key=entry.key)
            db.add(row)
        for column in columns:
            if column in entry.values:
                setattr(row, column, entry.values[column])
        rows[entry.key] = row
    db.flush()
    return rows, before


def _status(entry: _Entry, before: tuple[Any, ...] | None, after: tuple[Any, ...]) -> str:
    if before is None:
        return "created"
    if entry.renamed_from:
        return "updated"
    return "unchanged" if before == after else "updated"


def _check_unique(entries: list[_Entry], kind: str) -> None:
    seen: dict[str, str] = {}
    for entry in entries:
        if entry.key and entry.key in seen:
            entry.errors.append(f"{kind} key already used in {seen[entry.key]}")
        seen.setdefault(entry.key, entry.file)
    for entry in entries:
        for key in entry.previous:
            if key in seen:
                entry.errors.append(f"previous key '{key}' is still used in {seen[key]}")


@dataclass
class _Batch:
    statblocks: list[_Entry] = field(default_factory=list)
    places: list[_Entry] = field(default_factory=list)
    npcs: list[_Entry] = field(default_factory=list)
    roles: list[_Entry] = field(default_factory=list)
    positions: list[_Entry] = field(default_factory=list)

    def groups(self) -> list[tuple[str, list[_Entry]]]:
        return [
            ("statblock", self.statblocks),
            ("place", self.places),
            ("npc", self.npcs),
            ("role", self.roles),
            ("position", self.positions),
        ]

    def all(self) -> list[_Entry]:
        return [entry for _, entries in self.groups() for entry in entries]

    @property
    def has_errors(self) -> bool:
        return any(entry.errors for entry in self.all())

    @property
    def has_npc_data(self) -> bool:
        return bool(self.statblocks or self.npcs or self.roles)


def import_structures(
    db: Session,
    documents: list[tuple[str, dict[str, Any]]],
    *,
    dry_run: bool = False,
    prune: bool = False,
) -> list[ImportLine]:
    articles = _Articles(db)
    batch = _Batch()
    counter = 0
    for name, data in documents:
        for section in DOCUMENT_SECTIONS:
            target = getattr(batch, section)
            for item in data.get(section, []):
                counter += 1
                target.append(_Entry(name, item, counter))
    for entry in batch.statblocks:
        _prepare_stat_block(entry)
    for entry in batch.places:
        _prepare_place(entry, articles)
    for entry in batch.npcs:
        _prepare_npc(entry, articles)
    for entry in batch.roles:
        _prepare_role(entry, articles)
    for entry in batch.positions:
        _prepare_position(entry, articles)
    for kind, entries in batch.groups():
        _check_unique(entries, kind)
    _check_unique_articles(batch.places, "wa_article_id", "place")
    _check_unique_articles(batch.npcs, "wa_article_id", "NPC")
    if prune:
        _check_kept_references(batch)
    statuses: dict[tuple[str, str], str] = {}
    removed: list[str] = []
    try:
        if not batch.has_errors:
            statuses, removed = _write(db, batch, prune)
    except Exception:
        db.rollback()
        raise
    lines = _lines(batch, statuses, removed, dry_run)
    if dry_run or any(line.failed for line in lines):
        db.rollback()
    else:
        db.commit()
    return lines


def _check_kept_references(batch: _Batch) -> None:
    kept = {kind: {entry.key for entry in entries} for kind, entries in batch.groups()}
    checks = [
        (batch.places, "parent", "place", True),
        (batch.positions, "place", "place", True),
        (batch.positions, "site", "place", True),
        (batch.positions, "superior", "position", True),
        (batch.positions, "npc", "npc", batch.has_npc_data),
        (batch.positions, "npc_role", "role", batch.has_npc_data),
        (batch.npcs, "place", "place", True),
        (batch.roles, "place", "place", True),
        (batch.npcs, "statblock", "statblock", True),
        (batch.roles, "statblock", "statblock", True),
    ]
    for entries, ref, kind, pruned in checks:
        if not pruned:
            continue
        for entry in entries:
            target = entry.refs.get(ref)
            if target and target not in kept[kind]:
                entry.errors.append(f"{ref}: '{target}' is not in the files and would be pruned")


def _write(db: Session, batch: _Batch, prune: bool) -> tuple[dict[tuple[str, str], str], list[str]]:
    befores: dict[str, dict[str, tuple[Any, ...] | None]] = {}
    rows: dict[str, dict[str, Any]] = {}
    rows["statblock"], befores["statblock"] = _apply(db, batch.statblocks, StatBlock, STAT_COLUMNS)
    rows["place"], befores["place"] = _apply(db, batch.places, Place, PLACE_COLUMNS)
    if batch.has_errors:
        return {}, []
    places_by_key = {row.key: row for row in db.scalars(select(Place))}
    _link(batch.places, rows["place"], places_by_key, "parent", "parent_id")
    db.flush()
    cycle = _find_cycle({row.id: row.parent_id for row in places_by_key.values()})
    if cycle is not None:
        key = next(row.key for row in places_by_key.values() if row.id == cycle)
        _error_for(batch.places, key, "places form a cycle through their parents")
    blocks_by_key = {row.key: row for row in db.scalars(select(StatBlock))}
    for entry in [*batch.npcs, *batch.roles]:
        _resolve(entry, "statblock", "stat_block_id", blocks_by_key, "stat block")
        _resolve(entry, "place", "place_id", places_by_key, "place")
    if batch.has_errors:
        return {}, []
    rows["npc"], befores["npc"] = _apply(db, batch.npcs, Npc, NPC_COLUMNS)
    rows["role"], befores["role"] = _apply(db, batch.roles, NpcRole, ROLE_COLUMNS)
    if batch.has_errors:
        return {}, []
    npcs_by_key = {row.key: row for row in db.scalars(select(Npc))}
    roles_by_key = {row.key: row for row in db.scalars(select(NpcRole))}
    for entry in batch.positions:
        _resolve(entry, "place", "place_id", places_by_key, "place")
        _resolve(entry, "site", "site_id", places_by_key, "place")
        _resolve(entry, "npc", "npc_id", npcs_by_key, "NPC")
        _resolve(entry, "npc_role", "npc_role_id", roles_by_key, "role")
    if batch.has_errors:
        return {}, []
    rows["position"], befores["position"] = _apply(db, batch.positions, Position, POSITION_COLUMNS)
    if batch.has_errors:
        return {}, []
    positions_by_key = {row.key: row for row in db.scalars(select(Position))}
    _link(batch.positions, rows["position"], positions_by_key, "superior", "superior_id")
    db.flush()
    _check_superiors(batch.positions, positions_by_key)
    extras = {
        "place": _replace_sections(db, batch.places, rows["place"], "place"),
        "npc": _replace_sections(db, batch.npcs, rows["npc"], "npc"),
        "role": _replace_sections(db, batch.roles, rows["role"], "role"),
    }
    relation_changes = _replace_relations(db, batch.npcs, rows["npc"], npcs_by_key)
    removed = _prune(db, batch) if prune else []
    db.flush()
    columns = {
        "statblock": STAT_COLUMNS,
        "place": PLACE_COLUMNS,
        "npc": NPC_COLUMNS,
        "role": ROLE_COLUMNS,
        "position": POSITION_COLUMNS,
    }
    statuses: dict[tuple[str, str], str] = {}
    for kind, entries in batch.groups():
        changed = extras.get(kind, set()) | (relation_changes if kind == "npc" else set())
        for entry in entries:
            row = rows[kind][entry.key]
            status = _status(entry, befores[kind][entry.key], _snapshot(row, columns[kind]))
            if status == "unchanged" and entry.key in changed:
                status = "updated"
            statuses[(kind, entry.key)] = status
    return statuses, removed


def _resolve(entry: _Entry, ref: str, column: str, by_key: dict[str, Any], label: str) -> None:
    target = entry.refs.get(ref)
    if target is not None and target not in by_key:
        entry.errors.append(f"{ref}: unknown {label} '{target}'")
    entry.values[column] = by_key[target].id if target in by_key else None


def _replace_sections(
    db: Session, entries: list[_Entry], rows: dict[str, Any], owner: str
) -> set[str]:
    column = SECTION_OWNER[owner]
    changed: set[str] = set()
    for entry in entries:
        row = rows[entry.key]
        current = list(
            db.scalars(
                select(FicheSection)
                .where(getattr(FicheSection, column) == row.id)
                .order_by(FicheSection.position, FicheSection.id)
            )
        )
        wanted = entry.sections or []
        if [(item.title, item.text, item.visibility) for item in current] == wanted:
            continue
        for item in current:
            db.delete(item)
        db.flush()
        for index, (title, text, visibility) in enumerate(wanted):
            db.add(
                FicheSection(
                    **{column: row.id},
                    title=title,
                    text=text,
                    visibility=visibility,
                    position=index,
                )
            )
        changed.add(entry.key)
    db.flush()
    return changed


def _replace_relations(
    db: Session, entries: list[_Entry], rows: dict[str, Npc], npcs_by_key: dict[str, Npc]
) -> set[str]:
    changed: set[str] = set()
    for entry in entries:
        wanted = []
        for relation in entry.relations:
            target = npcs_by_key.get(relation["npc"]) if relation["npc"] else None
            if relation["npc"] and target is None:
                entry.errors.append(f"relation: unknown NPC '{relation['npc']}'")
                continue
            wanted.append(
                (
                    target.id if target else None,
                    relation["article"],
                    relation["label"],
                    relation["reverse_label"],
                    relation["visibility"],
                )
            )
        row = rows[entry.key]
        current = list(
            db.scalars(
                select(NpcRelation)
                .where(NpcRelation.npc_id == row.id)
                .order_by(NpcRelation.position, NpcRelation.id)
            )
        )
        found = [
            (
                item.target_npc_id,
                item.target_article_id,
                item.label,
                item.reverse_label,
                item.visibility,
            )
            for item in current
        ]
        if found == wanted:
            continue
        for item in current:
            db.delete(item)
        db.flush()
        for index, (target_id, article_id, label, reverse, visibility) in enumerate(wanted):
            db.add(
                NpcRelation(
                    npc_id=row.id,
                    target_npc_id=target_id,
                    target_article_id=article_id,
                    label=label,
                    reverse_label=reverse,
                    visibility=visibility,
                    position=index,
                )
            )
        changed.add(entry.key)
    db.flush()
    return changed


def _check_unique_articles(entries: list[_Entry], column: str, label: str) -> None:
    seen: dict[str, str] = {}
    for entry in entries:
        wa_id = entry.values.get(column)
        if not wa_id:
            continue
        if wa_id in seen:
            entry.errors.append(f"article already used by {label} '{seen[wa_id]}'")
        seen.setdefault(wa_id, entry.key)


def _link(
    entries: list[_Entry],
    rows: dict[str, Any],
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


def _prune(db: Session, batch: _Batch) -> list[str]:
    targets: list[tuple[str, type[Any], list[_Entry]]] = [
        ("position", Position, batch.positions),
        ("place", Place, batch.places),
    ]
    if batch.has_npc_data:
        targets[1:1] = [("role", NpcRole, batch.roles), ("npc", Npc, batch.npcs)]
        targets.append(("statblock", StatBlock, batch.statblocks))
    removed: list[str] = []
    for kind, model, entries in targets:
        keep = {entry.key for entry in entries}
        for row in db.scalars(select(model).order_by(model.key)):
            if row.key not in keep:
                removed.append(f"{kind} {row.key}")
                db.delete(row)
        db.flush()
    return removed


def _lines(
    batch: _Batch,
    statuses: dict[tuple[str, str], str],
    removed: list[str],
    dry_run: bool,
) -> list[ImportLine]:
    lines: list[ImportLine] = []
    for kind, entries in batch.groups():
        for entry in entries:
            label = f"{kind} {entry.key or '?'}"
            if entry.errors:
                detail = f"({entry.file}) {'; '.join(entry.errors)}"
                lines.append(ImportLine("failed", label, detail))
                continue
            status = statuses.get((kind, entry.key), "checked")
            detail = f"({entry.file})"
            if entry.renamed_from:
                detail = f"{detail} renamed from '{entry.renamed_from}'"
            lines.append(ImportLine(status, label, detail))
            lines.extend(ImportLine("warning", label, warning) for warning in entry.warnings)
    lines.extend(ImportLine("removed", item, "(--prune)") for item in removed)
    if dry_run:
        lines = [
            ImportLine(line.status, line.label, f"{line.detail} [dry run]".strip())
            for line in lines
        ]
    return lines
