import re
import tomllib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chronology import format_year
from app.models import (
    Character,
    CharacterAction,
    CharacterEvent,
    CharacterEventOrganization,
    CharacterEventPerson,
    CharacterItem,
    CharacterJournalEntry,
    CharacterProfile,
    CharacterRelation,
    WaArticle,
    utcnow,
)
from app.services.media import find_article
from app.worldanvil.bbcode import RenderContext, render_bbcode, to_plain_text

RELATION_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("famille", "Famille"),
    ("fideles", "Fidèles"),
    ("amis", "Amis"),
    ("allies", "Alliés"),
    ("connaissances", "Connaissances"),
    ("rivaux", "Rivaux"),
    ("ennemis", "Ennemis"),
    ("autres", "Autres"),
)
CATEGORY_ALIASES = {
    "family": "famille",
    "fidele": "fideles",
    "followers": "fideles",
    "loyal": "fideles",
    "loyaux": "fideles",
    "friends": "amis",
    "ami": "amis",
    "amies": "amis",
    "allies": "allies",
    "allie": "allies",
    "acquaintances": "connaissances",
    "connaissance": "connaissances",
    "rivals": "rivaux",
    "rival": "rivaux",
    "enemies": "ennemis",
    "ennemi": "ennemis",
    "ennemie": "ennemis",
    "ennemies": "ennemis",
    "others": "autres",
    "autre": "autres",
}
CATEGORY_LABELS = dict(RELATION_CATEGORIES)
ACTION_KINDS: tuple[tuple[str, str], ...] = (
    ("action", "Action"),
    ("bonus", "Action bonus"),
    ("reaction", "Réaction"),
    ("free", "Caractéristique"),
)
ACTION_FAMILIES: dict[str, tuple[tuple[str, str], ...]] = {
    "action": (("attack", "Attaques"), ("spell", "Sorts"), ("other", "Divers")),
    "bonus": (("spell", "Sorts"), ("other", "Divers")),
}
SPELL_CATEGORY_RE = re.compile(r"^sorts?(?:-|$)")
ATTACK_CATEGORY_RE = re.compile(r"(?:^|-)attaques?(?:-|$)")
ACTION_ALIASES = {
    "action": "action",
    "bonus": "bonus",
    "action-bonus": "bonus",
    "bonus-action": "bonus",
    "reaction": "reaction",
    "free": "free",
    "gratuit": "free",
    "gratuite": "free",
    "passif": "free",
    "passive": "free",
    "sans-action": "free",
    "caracteristique": "free",
    "caracteristiques": "free",
}
COMBAT_FIELDS: tuple[tuple[str, str], ...] = (
    ("armor_class", "Classe d'armure"),
    ("hit_points", "Points de vie"),
    ("speed", "Vitesse"),
    ("initiative", "Initiative"),
    ("spell_save_dc", "DD des sorts"),
    ("spell_attack", "Attaque des sorts"),
)
JOURNAL_LISTS: tuple[tuple[str, str], ...] = (
    ("encounters", "Rencontres"),
    ("items", "Objets et argent"),
    ("quests", "Quêtes et engagements"),
    ("leads", "Pistes et questions"),
)
MAX_TEXT_LENGTH = 50_000
MAX_CATEGORY_LENGTH = 40
MAX_JOURNAL_LINE_LENGTH = 2_000
MAX_JOURNAL_LINES = 100
MAX_EXCERPT_LENGTH = 220
MAX_EVENT_ROWS = 50
MAX_YEAR = 100_000
MAX_AGE = 10_000
PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n|\[/p\]")
SINGLE_PARAGRAPH_RE = re.compile(r"^<p>((?:(?!</?p>).)*)</p>$", re.S)


class DossierError(Exception):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class EventLinkView:
    name: str
    href: str | None
    role: str | None = None


@dataclass(frozen=True)
class EventLinkForm:
    name: str
    article: str
    role: str = ""


@dataclass(frozen=True)
class EventForm:
    title: str
    session: str
    year: str
    age: str
    approximate: bool
    place: str
    place_article: str
    text: str
    consequences: str
    gm_notes: str
    people: list[EventLinkForm]
    organizations: list[EventLinkForm]


@dataclass(frozen=True)
class EventView:
    id: int
    title: str
    session: str | None
    html: Markup
    year: int | None = None
    date_label: str | None = None
    age_label: str | None = None
    place: EventLinkView | None = None
    people: list[EventLinkView] = field(default_factory=list)
    organizations: list[EventLinkView] = field(default_factory=list)
    consequences_html: Markup = field(default_factory=Markup)
    gm_html: Markup | None = None
    form: EventForm | None = None

    @property
    def anchor(self) -> str:
        return f"evenement-{self.id}"

    @property
    def when(self) -> str | None:
        return " · ".join(label for label in (self.date_label, self.age_label) if label) or None


@dataclass(frozen=True)
class RelationForm:
    category: str
    article: str
    text: str
    gm_notes: str


@dataclass(frozen=True)
class RelationView:
    id: int
    name: str
    role: str | None
    status: str | None
    html: Markup
    article: WaArticle | None
    href: str | None
    gm_html: Markup | None = None
    form: RelationForm | None = None


@dataclass(frozen=True)
class RelationGroup:
    key: str
    label: str
    people: list[RelationView]


@dataclass(frozen=True)
class ItemView:
    name: str
    quantity: int
    category: str | None
    equipped: bool
    html: Markup
    href: str | None
    gm_html: Markup | None = None


@dataclass(frozen=True)
class JournalListView:
    key: str
    label: str
    lines: list[Markup]


@dataclass(frozen=True)
class JournalEntryView:
    character_id: int
    episode: int
    title: str
    days: str | None
    html: Markup
    lists: list[JournalListView]
    excerpt: str


@dataclass(frozen=True)
class JournalShelf:
    character: Character
    entries: list[JournalEntryView]


@dataclass(frozen=True)
class Dossier:
    character: Character
    backstory: Markup
    events: list[EventView] = field(default_factory=list)
    relation_groups: list[RelationGroup] = field(default_factory=list)
    items: list[ItemView] = field(default_factory=list)
    combat_stats: list["CombatStat"] = field(default_factory=list)
    resources: list["ResourceView"] = field(default_factory=list)
    action_groups: list["ActionGroup"] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    journal: list[JournalEntryView] = field(default_factory=list)
    source_file: str | None = None
    dm: bool = False
    relations_edited_at: datetime | None = None
    events_edited_at: datetime | None = None

    @property
    def dated_events(self) -> list[EventView]:
        return [event for event in self.events if event.year is not None]

    @property
    def undated_events(self) -> list[EventView]:
        return [event for event in self.events if event.year is None]

    @property
    def relation_count(self) -> int:
        return sum(len(group.people) for group in self.relation_groups)

    @property
    def action_count(self) -> int:
        return sum(len(group.actions) for group in self.action_groups)


@dataclass(frozen=True)
class CombatStat:
    label: str
    value: str


@dataclass(frozen=True)
class ResourceView:
    name: str
    uses: str | None
    recharge: str | None


@dataclass(frozen=True)
class ActionView:
    name: str
    category: str | None
    hit: str | None
    damage: str | None
    save: str | None
    range: str | None
    uses: str | None
    concentration: bool
    html: Markup
    family: str = "other"


@dataclass(frozen=True)
class ActionSubgroup:
    key: str
    label: str
    actions: list[ActionView]


@dataclass(frozen=True)
class ActionGroup:
    key: str
    label: str
    actions: list[ActionView]
    subgroups: list[ActionSubgroup] = field(default_factory=list)


def _slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-")


def category_key(value: str) -> str:
    slug = _slug(value)
    return CATEGORY_ALIASES.get(slug, slug)


def normalize_category(value: str) -> str:
    text = " ".join(value.split())
    key = category_key(text)
    if not key:
        raise DossierError("relation category is empty", "category")
    if key in CATEGORY_LABELS:
        return key
    if len(text) > MAX_CATEGORY_LENGTH:
        raise DossierError("relation category is too long", "category")
    return text


def category_label(value: str) -> str:
    key = category_key(value)
    if key in CATEGORY_LABELS:
        return CATEGORY_LABELS[key]
    text = " ".join(value.split())
    if text == key:
        text = text.replace("-", " ")
    return text[:1].upper() + text[1:]


def _category_rank(key: str) -> tuple[int, str]:
    order = [slug for slug, _ in RELATION_CATEGORIES]
    if key == "autres":
        return (len(order) + 1, key)
    return (order.index(key), key) if key in order else (len(order), key)


def _text(entry: dict[str, Any], key: str, *, required: bool = False, limit: int = 200) -> str:
    value = entry.get(key)
    if value is None:
        if required:
            raise DossierError(f"missing '{key}'", key)
        return ""
    if not isinstance(value, str):
        raise DossierError(f"'{key}' must be text", key)
    value = value.replace("\r\n", "\n").strip()
    if required and not value:
        raise DossierError(f"'{key}' is empty", key)
    if len(value) > limit:
        raise DossierError(f"'{key}' is too long", key)
    return value


def _entries(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DossierError(f"'{key}' must be a list of tables")
    return value


def _linked_article(db: Session, entry: dict[str, Any]) -> str | None:
    wa_id = _text(entry, "article_id", limit=36) or None
    title = _text(entry, "article", limit=500) or None
    if not wa_id and not title:
        return None
    try:
        return find_article(db, wa_id=wa_id, title=title).wa_id
    except LookupError as exc:
        raise DossierError(str(exc), "article") from exc


def _event_article(db: Session, entry: dict[str, Any], field: str) -> WaArticle | None:
    try:
        wa_id = _text(entry, "article_id", limit=36) or None
        title = _text(entry, "article", limit=500) or None
    except DossierError as exc:
        raise DossierError(str(exc), field) from exc
    if not wa_id and not title:
        return None
    try:
        return find_article(db, wa_id=wa_id, title=title)
    except LookupError as exc:
        raise DossierError(str(exc), field) from exc


def _whole(entry: dict[str, Any], key: str, minimum: int, maximum: int) -> int | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DossierError(f"'{key}' must be a whole number", key)
    if not minimum <= value <= maximum:
        raise DossierError(f"'{key}' is out of range", key)
    return value


def _event_rows(
    db: Session, event: dict[str, Any], key: str, *, with_role: bool
) -> list[dict[str, Any]]:
    try:
        entries = _entries(event, key)
    except DossierError as exc:
        raise DossierError(str(exc), key) from exc
    if len(entries) > MAX_EVENT_ROWS:
        raise DossierError(f"too many '{key}' rows", key)
    rows = []
    for entry in entries:
        article = _event_article(db, entry, key)
        try:
            name = _text(entry, "name") or (article.title[:200] if article else "")
            role = _text(entry, "role") if with_role else ""
        except DossierError as exc:
            raise DossierError(str(exc), key) from exc
        if not name:
            raise DossierError(f"a '{key}' row has no name", key)
        row: dict[str, Any] = {"name": name, "wa_article_id": article.wa_id if article else None}
        if with_role:
            row["role"] = role or None
        rows.append(row)
    return rows


def event_values(db: Session, entry: dict[str, Any]) -> dict[str, Any]:
    approximate = entry.get("approximate", False)
    if not isinstance(approximate, bool):
        raise DossierError("'approximate' must be true or false", "approximate")
    place_link = {
        "article": entry.get("place_article"),
        "article_id": entry.get("place_article_id"),
    }
    place_article = _event_article(db, place_link, "place_article")
    place = _text(entry, "place") or (place_article.title[:200] if place_article else "")
    return {
        "title": _text(entry, "title", required=True),
        "session": _text(entry, "session", limit=100) or None,
        "text": _text(entry, "text", limit=MAX_TEXT_LENGTH),
        "world_year": _whole(entry, "year", -MAX_YEAR, MAX_YEAR),
        "age": _whole(entry, "age", 0, MAX_AGE),
        "date_approximate": approximate,
        "place_name": place or None,
        "place_article_id": place_article.wa_id if place_article else None,
        "consequences": _text(entry, "consequences", limit=MAX_TEXT_LENGTH),
        "gm_notes": _text(entry, "gm_notes", limit=MAX_TEXT_LENGTH),
        "people": _event_rows(db, entry, "people", with_role=True),
        "organizations": _event_rows(db, entry, "organizations", with_role=False),
    }


def _apply_event(event: CharacterEvent, values: dict[str, Any]) -> CharacterEvent:
    values = dict(values)
    people = values.pop("people")
    organizations = values.pop("organizations")
    for key, value in values.items():
        setattr(event, key, value)
    event.people = [CharacterEventPerson(position=index, **row) for index, row in enumerate(people)]
    event.organizations = [
        CharacterEventOrganization(position=index, **row) for index, row in enumerate(organizations)
    ]
    return event


def action_family(category: str | None, hit: str | None) -> str:
    slug = _slug(category or "")
    if SPELL_CATEGORY_RE.search(slug):
        return "spell"
    if hit or ATTACK_CATEGORY_RE.search(slug):
        return "attack"
    return "other"


def _action_subgroups(kind: str, actions: list[ActionView]) -> list[ActionSubgroup]:
    families = ACTION_FAMILIES.get(kind)
    if families is None:
        return []
    keys = {key for key, _ in families}
    grouped: dict[str, list[ActionView]] = {}
    for action in actions:
        family = action.family if action.family in keys else "other"
        grouped.setdefault(family, []).append(action)
    return [ActionSubgroup(key, label, grouped[key]) for key, label in families if key in grouped]


def normalize_action_kind(value: str) -> str:
    kind = ACTION_ALIASES.get(_slug(value))
    if kind is None:
        raise DossierError(f"unknown action type '{value}' (action, bonus, reaction or free)")
    return kind


def _short(entry: dict[str, Any], key: str, limit: int) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DossierError(f"'{key}' must be text or a number")
    value = str(value).strip()
    if len(value) > limit:
        raise DossierError(f"'{key}' is too long")
    return value or None


def _combat(data: dict[str, Any]) -> dict[str, Any]:
    combat = data.get("combat", {})
    if not isinstance(combat, dict):
        raise DossierError("'combat' must be a table")
    stats = {key: _short(combat, key, 40) for key, _ in COMBAT_FIELDS}
    resources = [
        {
            "name": _text(entry, "name", required=True),
            "uses": _short(entry, "uses", 40),
            "recharge": _short(entry, "recharge", 60),
        }
        for entry in _entries(combat, "resources")
    ]
    return {"stats": {key: value for key, value in stats.items() if value}, "resources": resources}


def _actions(data: dict[str, Any]) -> list[CharacterAction]:
    actions = []
    for index, entry in enumerate(_entries(data, "actions")):
        concentration = entry.get("concentration", False)
        if not isinstance(concentration, bool):
            raise DossierError("'concentration' must be true or false")
        actions.append(
            CharacterAction(
                position=index,
                kind=normalize_action_kind(_text(entry, "type", required=True, limit=40)),
                name=_text(entry, "name", required=True),
                category=_short(entry, "category", 100),
                hit=_short(entry, "hit", 60),
                damage=_short(entry, "damage", 200),
                save=_short(entry, "save", 60),
                range=_short(entry, "range", 100),
                uses=_short(entry, "uses", 100),
                concentration=concentration,
                text=_text(entry, "text", limit=MAX_TEXT_LENGTH),
            )
        )
    return actions


def _aliases(data: dict[str, Any]) -> list[dict[str, str | None]]:
    value = data.get("aliases", [])
    if not isinstance(value, list):
        raise DossierError("'aliases' must be a list", "aliases")
    aliases = []
    for entry in value:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            raise DossierError("each alias must be text or a table", "aliases")
        aliases.append(
            {"name": _text(entry, "name", required=True), "note": _text(entry, "note") or None}
        )
    return aliases


def _journal_lines(entry: dict[str, Any], key: str) -> list[str]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
        raise DossierError(f"journal '{key}' must be a list of text lines", key)
    if len(value) > MAX_JOURNAL_LINES:
        raise DossierError(f"journal '{key}' has too many lines", key)
    lines = [" ".join(line.split()) for line in value]
    if any(not line for line in lines):
        raise DossierError(f"journal '{key}' has an empty line", key)
    if any(len(line) > MAX_JOURNAL_LINE_LENGTH for line in lines):
        raise DossierError(f"a journal '{key}' line is too long", key)
    return lines


def _journal(data: dict[str, Any]) -> list[CharacterJournalEntry]:
    entries = []
    seen: set[int] = set()
    for entry in _entries(data, "journal"):
        episode = entry.get("episode")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
            raise DossierError("journal 'episode' must be a positive whole number", "episode")
        if episode in seen:
            raise DossierError(f"journal episode {episode} is listed twice", "episode")
        seen.add(episode)
        lists = {key: _journal_lines(entry, key) for key, _ in JOURNAL_LISTS}
        entries.append(
            CharacterJournalEntry(
                episode=episode,
                title=_text(entry, "title", required=True),
                days=_text(entry, "days", limit=100) or None,
                text=_text(entry, "text", limit=MAX_TEXT_LENGTH),
                lists={key: lines for key, lines in lists.items() if lines},
            )
        )
    return sorted(entries, key=lambda item: item.episode)


def format_alias(alias: dict[str, Any]) -> str:
    name = str(alias.get("name") or "")
    note = alias.get("note")
    return f"{name} ({note})" if note else name


def relation_values(db: Session, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": normalize_category(_text(entry, "category", required=True, limit=60)),
        "name": _text(entry, "name", required=True),
        "role": _text(entry, "role") or None,
        "status": _text(entry, "status", limit=100) or None,
        "wa_article_id": _linked_article(db, entry),
        "text": _text(entry, "text", limit=MAX_TEXT_LENGTH),
        "gm_notes": _text(entry, "gm_notes", limit=MAX_TEXT_LENGTH),
    }


def find_character(db: Session, name: str) -> Character:
    character = db.scalar(
        select(Character).where(func.lower(Character.name) == name.strip().casefold())
    )
    if character is None:
        article = db.scalar(
            select(WaArticle).where(func.lower(WaArticle.title) == name.strip().casefold())
        )
        if article is not None:
            character = db.scalar(
                select(Character).where(Character.main_article_id == article.wa_id)
            )
    if character is None:
        raise DossierError(f"character '{name}' not found")
    return character


def relations_locked(character: Character) -> bool:
    return character.profile is not None and character.profile.relations_edited_at is not None


def events_locked(character: Character) -> bool:
    return character.profile is not None and character.profile.events_edited_at is not None


def import_dossier(
    db: Session,
    data: dict[str, Any],
    source_file: str | None = None,
    *,
    replace_relations: bool = False,
    replace_events: bool = False,
) -> Character:
    name = _text(data, "character", required=True)
    character = find_character(db, name)
    history = data.get("history", {})
    if not isinstance(history, dict):
        raise DossierError("'history' must be a table")
    backstory = _text(history, "backstory", limit=MAX_TEXT_LENGTH)

    events = [
        _apply_event(CharacterEvent(position=index), event_values(db, entry))
        for index, entry in enumerate(_entries(history, "events"))
    ]
    relations = [
        CharacterRelation(position=index, **relation_values(db, entry))
        for index, entry in enumerate(_entries(data, "relations"))
    ]
    items = []
    for index, entry in enumerate(_entries(data, "inventory")):
        quantity = entry.get("quantity", 1)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise DossierError("'quantity' must be a positive whole number")
        equipped = entry.get("equipped", False)
        if not isinstance(equipped, bool):
            raise DossierError("'equipped' must be true or false")
        items.append(
            CharacterItem(
                position=index,
                name=_text(entry, "name", required=True),
                quantity=quantity,
                category=_text(entry, "category", limit=60) or None,
                equipped=equipped,
                wa_article_id=_linked_article(db, entry),
                text=_text(entry, "text", limit=MAX_TEXT_LENGTH),
                gm_notes=_text(entry, "gm_notes", limit=MAX_TEXT_LENGTH),
            )
        )

    combat = _combat(data)
    actions = _actions(data)
    aliases = _aliases(data)
    journal = _journal(data)

    keep_relations = relations_locked(character) and not replace_relations
    keep_events = events_locked(character) and not replace_events
    if character.profile is None:
        character.profile = CharacterProfile(character_id=character.id)
    character.profile.backstory = backstory
    character.profile.combat = combat
    character.profile.aliases = aliases
    character.profile.source_file = source_file
    character.profile.updated_at = utcnow()
    if not keep_events:
        character.events = events
        character.profile.events_edited_at = None
    if not keep_relations:
        character.relations = relations
        character.profile.relations_edited_at = None
    character.items = items
    character.actions = actions
    character.journal = []
    db.flush()
    character.journal = journal
    db.commit()
    return character


def mark_relations_edited(character: Character) -> None:
    if character.profile is None:
        character.profile = CharacterProfile(character_id=character.id)
    character.profile.relations_edited_at = utcnow()


def add_relation(db: Session, character: Character, entry: dict[str, Any]) -> CharacterRelation:
    values = relation_values(db, entry)
    position = max((relation.position for relation in character.relations), default=-1) + 1
    relation = CharacterRelation(position=position, **values)
    character.relations.append(relation)
    mark_relations_edited(character)
    db.commit()
    return relation


def update_relation(
    db: Session, character: Character, relation: CharacterRelation, entry: dict[str, Any]
) -> None:
    for key, value in relation_values(db, entry).items():
        setattr(relation, key, value)
    mark_relations_edited(character)
    db.commit()


def delete_relation(db: Session, character: Character, relation: CharacterRelation) -> None:
    character.relations.remove(relation)
    mark_relations_edited(character)
    db.commit()


def mark_events_edited(character: Character) -> None:
    if character.profile is None:
        character.profile = CharacterProfile(character_id=character.id)
    character.profile.events_edited_at = utcnow()


def add_event(db: Session, character: Character, entry: dict[str, Any]) -> CharacterEvent:
    values = event_values(db, entry)
    position = max((event.position for event in character.events), default=-1) + 1
    event = _apply_event(CharacterEvent(position=position), values)
    character.events.append(event)
    mark_events_edited(character)
    db.commit()
    return event


def update_event(
    db: Session, character: Character, event: CharacterEvent, entry: dict[str, Any]
) -> None:
    _apply_event(event, event_values(db, entry))
    mark_events_edited(character)
    db.commit()


def delete_event(db: Session, character: Character, event: CharacterEvent) -> None:
    character.events.remove(event)
    mark_events_edited(character)
    db.commit()


def load_dossier_file(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DossierError(f"cannot read {path.name}: {exc}") from exc


def _inline_html(text: str, context: RenderContext) -> Markup:
    rendered = str(render_bbcode(text, context)).strip()
    match = SINGLE_PARAGRAPH_RE.match(rendered)
    return Markup(match.group(1) if match else rendered)  # noqa: S704


def journal_entry_view(entry: CharacterJournalEntry, context: RenderContext) -> JournalEntryView:
    stored = entry.lists or {}
    lists = [
        JournalListView(key, label, [_inline_html(line, context) for line in stored.get(key, [])])
        for key, label in JOURNAL_LISTS
        if stored.get(key)
    ]
    return JournalEntryView(
        entry.character_id,
        entry.episode,
        entry.title,
        entry.days,
        render_bbcode(entry.text, context),
        lists,
        _excerpt(entry.text),
    )


def _excerpt(text: str) -> str:
    first = next((part for part in PARAGRAPH_BREAK_RE.split(text) if part.strip()), "")
    plain = " ".join(to_plain_text(first).split())
    if len(plain) <= MAX_EXCERPT_LENGTH:
        return plain
    return plain[:MAX_EXCERPT_LENGTH].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def journal_shelves(characters: list[Character], context: RenderContext) -> list[JournalShelf]:
    return [
        JournalShelf(
            character,
            [journal_entry_view(entry, context) for entry in reversed(character.journal)],
        )
        for character in characters
        if character.journal
    ]


def find_journal_entry(character: Character, episode: int) -> CharacterJournalEntry | None:
    return next((entry for entry in character.journal if entry.episode == episode), None)


def _gm_html(notes: str, context: RenderContext, dm: bool) -> Markup | None:
    return render_bbcode(notes, context) if dm and notes.strip() else None


def event_date_label(year: int | None, approximate: bool) -> str | None:
    if year is None:
        return None
    if not approximate:
        return format_year(year)
    return f"Vers l'an {year}" if year >= 0 else f"Vers {format_year(year)}"


def event_age_label(age: int | None, approximate: bool, *, alone: bool) -> str | None:
    if age is None:
        return None
    label = f"{age} an" if age <= 1 else f"{age} ans"
    if approximate:
        label = f"vers {label}"
    return label[:1].upper() + label[1:] if alone else label


def _event_form(event: CharacterEvent) -> EventForm:
    def title(article: WaArticle | None) -> str:
        return article.title if article is not None else ""

    return EventForm(
        title=event.title,
        session=event.session or "",
        year="" if event.world_year is None else str(event.world_year),
        age="" if event.age is None else str(event.age),
        approximate=event.date_approximate,
        place=event.place_name or "",
        place_article=title(event.place_article),
        text=event.text,
        consequences=event.consequences,
        gm_notes=event.gm_notes,
        people=[
            EventLinkForm(person.name, title(person.article), person.role or "")
            for person in event.people
        ],
        organizations=[
            EventLinkForm(organization.name, title(organization.article))
            for organization in event.organizations
        ],
    )


def event_view(
    event: CharacterEvent,
    context: RenderContext,
    article_href: Callable[[WaArticle], str | None],
    *,
    dm: bool = False,
) -> EventView:
    def href(article: WaArticle | None) -> str | None:
        return article_href(article) if article is not None else None

    place = EventLinkView(event.place_name, href(event.place_article)) if event.place_name else None
    return EventView(
        id=event.id,
        title=event.title,
        session=event.session,
        html=render_bbcode(event.text, context),
        year=event.world_year,
        date_label=event_date_label(event.world_year, event.date_approximate),
        age_label=event_age_label(
            event.age, event.date_approximate, alone=event.world_year is None
        ),
        place=place,
        people=[
            EventLinkView(person.name, href(person.article), person.role) for person in event.people
        ],
        organizations=[
            EventLinkView(organization.name, href(organization.article))
            for organization in event.organizations
        ],
        consequences_html=render_bbcode(event.consequences, context),
        gm_html=_gm_html(event.gm_notes, context, dm),
        form=_event_form(event) if dm else None,
    )


def build_dossier(
    character: Character,
    context: RenderContext,
    article_href: Callable[[WaArticle], str | None],
    *,
    dm: bool = False,
) -> Dossier:
    profile = character.profile
    ordered = sorted(
        character.events,
        key=lambda event: (event.world_year is None, event.world_year or 0, event.position),
    )
    events = [event_view(event, context, article_href, dm=dm) for event in ordered]
    grouped: dict[str, list[RelationView]] = {}
    labels: dict[str, str] = {}
    for relation in character.relations:
        href = article_href(relation.article) if relation.article is not None else None
        key = category_key(relation.category)
        labels.setdefault(key, category_label(relation.category))
        form = (
            RelationForm(
                labels[key],
                relation.article.title if relation.article is not None else "",
                relation.text,
                relation.gm_notes,
            )
            if dm
            else None
        )
        grouped.setdefault(key, []).append(
            RelationView(
                relation.id,
                relation.name,
                relation.role,
                relation.status,
                render_bbcode(relation.text, context),
                relation.article if href else None,
                href,
                _gm_html(relation.gm_notes, context, dm),
                form,
            )
        )
    groups = [
        RelationGroup(key, labels[key], grouped[key]) for key in sorted(grouped, key=_category_rank)
    ]
    items = [
        ItemView(
            item.name,
            item.quantity,
            item.category,
            item.equipped,
            render_bbcode(item.text, context),
            article_href(item.article) if item.article is not None else None,
            _gm_html(item.gm_notes, context, dm),
        )
        for item in character.items
    ]
    combat = (profile.combat if profile else None) or {}
    stats = combat.get("stats", {})
    combat_stats = [
        CombatStat(label, str(stats[key])) for key, label in COMBAT_FIELDS if stats.get(key)
    ]
    resources = [
        ResourceView(entry["name"], entry.get("uses"), entry.get("recharge"))
        for entry in combat.get("resources", [])
        if isinstance(entry, dict) and entry.get("name")
    ]
    by_kind: dict[str, list[ActionView]] = {}
    for action in character.actions:
        by_kind.setdefault(action.kind, []).append(
            ActionView(
                action.name,
                action.category,
                action.hit,
                action.damage,
                action.save,
                action.range,
                action.uses,
                action.concentration,
                render_bbcode(action.text, context),
                action_family(action.category, action.hit),
            )
        )
    action_groups = [
        ActionGroup(key, label, by_kind[key], _action_subgroups(key, by_kind[key]))
        for key, label in ACTION_KINDS
        if key in by_kind
    ]
    return Dossier(
        character=character,
        combat_stats=combat_stats,
        resources=resources,
        action_groups=action_groups,
        backstory=render_bbcode(profile.backstory if profile else "", context),
        events=events,
        relation_groups=groups,
        items=items,
        aliases=[format_alias(alias) for alias in (profile.aliases if profile else None) or []],
        journal=[journal_entry_view(entry, context) for entry in character.journal],
        source_file=profile.source_file if profile else None,
        dm=dm,
        relations_edited_at=profile.relations_edited_at if profile else None,
        events_edited_at=profile.events_edited_at if profile else None,
    )
