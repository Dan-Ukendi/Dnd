import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.presentation import is_title_heading, tagline_from
from app.worldanvil.bbcode import to_plain_text

HEADING_RE = re.compile(r"\[(?P<tag>h[1-6]?)(?:\|[^\]]*)?\](?P<text>.*?)\[/(?P=tag)\]", re.S)
EDGE_BLANK_RE = re.compile(r"^(?:\s+|\[p\]\s*\[/p\]|\[br\])+|(?:\s+|\[p\]\s*\[/p\]|\[br\])+$")
ITALIC_PARAGRAPH_RE = re.compile(r"\[p\]\s*\[i\](?P<text>.*?)\[/i\]\s*\[/p\]", re.S)
LIST_TAG_RE = re.compile(r"^\s*\[/?ul\]\s*$")
LIST_LINE_RE = re.compile(r"^\s*-\s*\[b\](?P<label>[^\[\]]+?)\s*:\s*\[/b\]\s*(?P<value>.*?)\s*$")
RARITY_RE = re.compile(
    r"^Objet merveilleux(?:\s*\([^)]*\))?\s*,\s*(?P<rarity>.+?)"
    r"(?:\s*\((?P<attunement>nécessite un lien[^)]*)\))?\s*\.?$",
    re.I | re.S,
)
CHARGES_RE = re.compile(r"possède (?P<count>\d+) charges?", re.I)
RECHARGE_RE = re.compile(r"en récupère (?P<amount>[^.]+?)\s*(?:\.|$)", re.I)
CURSE_HEADING_RE = re.compile(r"prix du pouvoir|malediction|maudit")

QUICK_SHEET = "fiche rapide"
STATISTICS_PREFIX = "statistiques"
QUICK_SHEET_ENTRY = "Fiche rapide (origine)"
INTRODUCTION_ENTRY = "Introduction"
SHEET_FIELDS = {
    "type": "object_type",
    "autre nom": "other_names",
    "autres noms": "other_names",
    "etat": "status",
}
HOLDER_LABELS = frozenset({"porteur actuel", "detenteur actuel"})
GM_ONLY_SECTIONS = frozenset({"secrets", "pistes pour la suite"})
SESSION_ONE_YEAR = 829

AUTOMATIC_NOTE = "Découpage automatique d'un nouvel article World Anvil : à vérifier."
MIXED_NOTE = (
    "Section mélangée (récit et règles) gardée telle quelle dans le Lore : à répartir si besoin."
)
DESCRIPTION_NOTE = (
    "Description physique de l'objet : la fiche n'en garde qu'une ligne (le sous-titre) ; "
    "à vérifier."
)
QUICK_SHEET_NOTE = (
    "Lignes de la Fiche rapide qui mêlent récit et caractéristiques, gardées telles quelles : "
    "à vérifier."
)
GM_ONLY_NOTE = (
    "Réservée au MJ : contient des informations que les personnages ignorent ; à vérifier."
)
UNDATED_NOTE = "Non datée : le texte ne donne aucune date."
EVENT_NOTE = (
    "Rapprochée de l'événement {code} de la Chronologie, dont elle lit la date : à vérifier."
)
MISSING_EVENT_NOTE = "L'événement {code} de la Chronologie est introuvable : entrée non datée."
HOLDER_NOTE = (
    "« Porteur actuel » sur World Anvil : {value}. Le détenteur affiché vient des inventaires "
    "des dossiers."
)
EMPTY_STATUS_NOTE = "Statut vide : le texte ne le précise pas."


@dataclass(frozen=True)
class ArticleRules:
    gm_only: frozenset[str] = frozenset()
    mixed: frozenset[str] = frozenset()
    description: frozenset[str] = frozenset()
    undated: frozenset[str] = frozenset()
    events: Mapping[str, str] = field(default_factory=dict)
    years: Mapping[str, int] = field(default_factory=dict)
    artefact_notes: tuple[str, ...] = ()


KNOWN_ARTICLES: dict[str, ArticleRules] = {
    "57249abe-74e2-4590-b489-7e388923051b": ArticleRules(
        gm_only=frozenset({"L'amulette de Khaetar", QUICK_SHEET_ENTRY}),
        mixed=frozenset({"Fonctionnement", "Les degrés de la charge"}),
        description=frozenset({"Description"}),
        years={"Événements de la session 1": SESSION_ONE_YEAR},
        artefact_notes=(EMPTY_STATUS_NOTE,),
    ),
    "dcdedb4d-686e-483d-a35d-137823b77eb1": ArticleRules(
        gm_only=frozenset({"Ceux qui la convoitent"}),
        mixed=frozenset({"Pouvoirs", "Le prix du pouvoir", "La restauration"}),
        description=frozenset({"Description"}),
        undated=frozenset({"La réapparition"}),
        events={
            "La forge": "A1",
            "Le règne du Dompteur": "A2",
            "La chute": "A3",
            "Le long sommeil": "A4",
        },
        years={"Événements de la session 1": SESSION_ONE_YEAR},
    ),
}


@dataclass
class EntryDraft:
    title: str
    text: str
    position: int
    gm_only: bool = False
    start_year: int | None = None
    event_code: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ArtefactDraft:
    tagline: str = ""
    object_type: str = ""
    other_names: str = ""
    rarity: str = ""
    attunement: str = ""
    properties: str = ""
    charges: str = ""
    recharge: str = ""
    curse: str = ""
    status: str = ""
    notes: list[str] = field(default_factory=list)
    entries: list[EntryDraft] = field(default_factory=list)


@dataclass(frozen=True)
class _Section:
    level: int
    title: str
    start: int
    body_start: int
    end: int


def fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped.casefold()).strip()


def _trim(source: str) -> str:
    previous = None
    while previous != source:
        previous = source
        source = EDGE_BLANK_RE.sub("", source)
    return source


def _level(tag: str) -> int:
    return int(tag[1:]) if len(tag) > 1 else 0


def _sections(content: str) -> list[_Section]:
    matches = list(HEADING_RE.finditer(content))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        title = to_plain_text(match.group("text")).strip()
        sections.append(
            _Section(_level(match.group("tag")), title, match.start(), match.end(), end)
        )
    return sections


def _block_end(sections: list[_Section], index: int, content: str) -> int:
    level = sections[index].level
    for section in sections[index + 1 :]:
        if section.level <= level:
            return section.start
    return len(content)


def _without_tagline(source: str, tagline: str) -> str:
    match = ITALIC_PARAGRAPH_RE.match(_trim(source))
    if tagline and match is not None and to_plain_text(match.group("text")).strip() == tagline:
        return _trim(_trim(source)[match.end() :])
    return _trim(source)


def _parse_quick_sheet(body: str, draft: ArtefactDraft) -> list[str]:
    remaining = []
    for line in body.splitlines():
        if not line.strip() or LIST_TAG_RE.match(line):
            continue
        match = LIST_LINE_RE.match(line)
        label = fold(match.group("label")) if match else ""
        if match and label in SHEET_FIELDS and not getattr(draft, SHEET_FIELDS[label]):
            setattr(draft, SHEET_FIELDS[label], to_plain_text(match.group("value")).strip())
        elif match and label in HOLDER_LABELS:
            draft.notes.append(
                HOLDER_NOTE.format(value=to_plain_text(match.group("value")).strip())
            )
        else:
            remaining.append(line.rstrip())
    return remaining


def _quick_sheet_text(lines: list[str]) -> str:
    if all(line.lstrip().startswith("-") for line in lines):
        return "[ul]\n" + "\n".join(lines) + "\n[/ul]"
    return "\n".join(lines)


def _context_label(block: str, position: int) -> str:
    label = ""
    for match in HEADING_RE.finditer(block):
        if match.start() > position:
            break
        if _level(match.group("tag")) == _child_level(block):
            label = to_plain_text(match.group("text")).strip()
    return label


def _child_level(block: str) -> int:
    levels = [_level(match.group("tag")) for match in HEADING_RE.finditer(block)]
    return levels[1] if len(levels) > 1 else -1


def _with_context(value: str, context: str, multi_state: bool) -> str:
    return f"{value} ({context})" if multi_state and context else value


def _read_statistics(block: str, draft: ArtefactDraft) -> None:
    rarities: list[tuple[str, int]] = []
    attunements: list[str] = []
    for match in ITALIC_PARAGRAPH_RE.finditer(block):
        line = RARITY_RE.match(to_plain_text(match.group("text")).strip())
        if line is None:
            continue
        rarities.append((line.group("rarity").strip(), match.start()))
        attunement = (line.group("attunement") or "").strip()
        if attunement and attunement not in attunements:
            attunements.append(attunement)
    multi_state = len(rarities) > 1
    draft.rarity = " ; ".join(
        _with_context(rarity, _context_label(block, position), multi_state)
        for rarity, position in rarities
    )
    draft.attunement = " ; ".join(attunements)
    plain_positions = _plain_segments(block)
    for position, text in plain_positions:
        charges = CHARGES_RE.search(text)
        if charges and not draft.charges:
            context = _context_label(block, position)
            draft.charges = _with_context(charges.group("count"), context, multi_state)
            recharge = RECHARGE_RE.search(text, charges.end())
            if recharge:
                draft.recharge = _with_context(recharge.group("amount"), context, multi_state)
    curses = []
    for match in HEADING_RE.finditer(block):
        title = to_plain_text(match.group("text")).strip()
        if CURSE_HEADING_RE.search(fold(title)):
            curses.append(_with_context(title, _context_label(block, match.start()), multi_state))
    draft.curse = " ; ".join(curses)


def _plain_segments(block: str) -> list[tuple[int, str]]:
    segments = []
    matches = list(HEADING_RE.finditer(block))
    starts = [0, *[match.end() for match in matches]]
    ends = [*[match.start() for match in matches], len(block)]
    for start, end in zip(starts, ends, strict=True):
        segments.append((start, to_plain_text(block[start:end])))
    return segments


def split_item_article(wa_id: str, title: str, content: str | None) -> ArtefactDraft:
    source = content or ""
    rules = KNOWN_ARTICLES.get(wa_id)
    draft = ArtefactDraft(tagline=tagline_from(title, source))
    sections = _sections(source)
    narrative: list[tuple[str, str]] = []
    preamble = source[: sections[0].start] if sections else source
    first = 0
    if sections and is_title_heading(sections[0].title, title):
        preamble += source[sections[0].body_start : sections[0].end]
        first = 1
    intro = _without_tagline(preamble, draft.tagline)
    if intro:
        narrative.append((INTRODUCTION_ENTRY, intro))
    properties: list[str] = []
    index = first
    while index < len(sections):
        section = sections[index]
        folded = fold(section.title)
        if folded.startswith(STATISTICS_PREFIX):
            end = _block_end(sections, index, source)
            block = _trim(source[section.start : end])
            properties.append(block)
            _read_statistics(block, draft)
            while index < len(sections) and sections[index].start < end:
                index += 1
            continue
        body = _trim(source[section.body_start : section.end])
        if folded == QUICK_SHEET:
            remaining = _parse_quick_sheet(body, draft)
            if remaining:
                narrative.append((QUICK_SHEET_ENTRY, _quick_sheet_text(remaining)))
        elif body:
            narrative.append((section.title, body))
        index += 1
    draft.properties = "\n".join(properties)
    for position, (entry_title, text) in enumerate(narrative):
        draft.entries.append(_entry(entry_title, text, position, rules))
    if rules is None:
        draft.notes.insert(0, AUTOMATIC_NOTE)
    else:
        draft.notes.extend(rules.artefact_notes)
    return draft


def _entry(title: str, text: str, position: int, rules: ArticleRules | None) -> EntryDraft:
    entry = EntryDraft(title=title, text=text, position=position)
    entry.gm_only = fold(title) in GM_ONLY_SECTIONS
    if rules is None:
        entry.notes.append(AUTOMATIC_NOTE)
        return entry
    if title in rules.gm_only:
        entry.gm_only = True
        entry.notes.append(GM_ONLY_NOTE)
    if title == QUICK_SHEET_ENTRY:
        entry.notes.append(QUICK_SHEET_NOTE)
    if title in rules.description:
        entry.notes.append(DESCRIPTION_NOTE)
    if title in rules.mixed:
        entry.notes.append(MIXED_NOTE)
    if title in rules.undated:
        entry.notes.append(UNDATED_NOTE)
    if title in rules.events:
        entry.event_code = rules.events[title]
        entry.notes.append(EVENT_NOTE.format(code=entry.event_code))
    entry.start_year = rules.years.get(title)
    return entry


def artefact_row(wa_id: str, draft: ArtefactDraft) -> dict[str, Any]:
    return {
        "wa_article_id": wa_id,
        "tagline": draft.tagline[:300],
        "object_type": draft.object_type[:300],
        "other_names": draft.other_names[:500],
        "rarity": draft.rarity[:300],
        "attunement": draft.attunement[:300],
        "properties": draft.properties,
        "bonuses": "",
        "charges": draft.charges[:100],
        "recharge": draft.recharge[:200],
        "curse": draft.curse,
        "holder": "",
        "location": "",
        "status": draft.status[:200],
        "review_note": "\n".join(draft.notes),
    }


def entry_rows(
    wa_id: str, draft: ArtefactDraft, event_ids: Mapping[str, int]
) -> list[dict[str, Any]]:
    rows = []
    for entry in draft.entries:
        notes = list(entry.notes)
        event_id = event_ids.get(entry.event_code) if entry.event_code else None
        if entry.event_code and event_id is None:
            notes = [note for note in notes if not note.startswith("Rapprochée")]
            notes.append(MISSING_EVENT_NOTE.format(code=entry.event_code))
        rows.append(
            {
                "title": entry.title[:300],
                "element_type": "artefact",
                "text": entry.text,
                "start_year": entry.start_year,
                "end_year": None,
                "era": None,
                "approximate": False,
                "sort_order": 0,
                "position": entry.position,
                "wa_article_id": wa_id,
                "timeline_event_id": event_id,
                "source_article_id": wa_id,
                "source_section": entry.title[:300],
                "gm_only": entry.gm_only,
                "review_note": "\n".join(notes),
            }
        )
    return rows


def review_lines(
    title: str, artefact: Mapping[str, Any], entries: list[dict[str, Any]]
) -> list[str]:
    lines = [f"{title} (fiche) : {note}" for note in artefact["review_note"].splitlines() if note]
    for entry in entries:
        lines.extend(
            f"{title} / {entry['title']} : {note}"
            for note in entry["review_note"].splitlines()
            if note
        )
    return lines
