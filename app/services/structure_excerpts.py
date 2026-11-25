import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.artefact_split import HEADING_RE, fold
from app.worldanvil.bbcode import to_plain_text

FORBIDDEN_SECTIONS = ("secrets", "pistes pour la suite", "evenements de la session 1")
BOLD_RE = re.compile(r"\[b\](?P<label>.*?)\[/b\]", re.S)
UNIT_PREFIX_RE = re.compile(r"(?:\[p\]|\[br\])\s*$")
LABEL_PARAGRAPH_PREFIX_RE = re.compile(r"\[p\]\s*$")
LABEL_PARAGRAPH_SUFFIX_RE = re.compile(r"\s*\[/p\]")
LIST_PREFIX_RE = re.compile(r"^\s*(?P<dashes>-+)\s*$")
LIST_LINE_RE = re.compile(r"^\s*(?P<dashes>-+)\s*")
SEGMENT_END_RE = re.compile(r"\[br\]|\[/p\]")
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'"})


class AnchorStatus(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class Excerpt:
    status: AnchorStatus
    source: str = ""


@dataclass(frozen=True)
class _Heading:
    level: int
    title: str
    start: int
    body_start: int


def anchor_key(value: str) -> str:
    return fold(value.translate(APOSTROPHES)).rstrip(" :").strip()


def _level(tag: str) -> int:
    return int(tag[1:]) if len(tag) > 1 else 0


def _headings(content: str) -> list[_Heading]:
    return [
        _Heading(
            _level(match.group("tag")),
            anchor_key(to_plain_text(match.group("text"))),
            match.start(),
            match.end(),
        )
        for match in HEADING_RE.finditer(content)
    ]


def _is_forbidden(title: str, forbidden: tuple[str, ...]) -> bool:
    return any(title.startswith(name) for name in forbidden)


def _ancestors(headings: list[_Heading], position: int) -> list[_Heading]:
    chain: list[_Heading] = []
    for heading in headings:
        if heading.start >= position:
            break
        while chain and chain[-1].level >= heading.level:
            chain.pop()
        chain.append(heading)
    return chain


def _forbidden_at(headings: list[_Heading], position: int, forbidden: tuple[str, ...]) -> bool:
    return any(
        _is_forbidden(heading.title, forbidden) for heading in _ancestors(headings, position)
    )


def _heading_excerpt(content: str, headings: list[_Heading], index: int) -> str:
    heading = headings[index]
    end = headings[index + 1].start if index + 1 < len(headings) else len(content)
    if index + 1 < len(headings) and headings[index + 1].level > heading.level:
        nested_end = next(
            (item.start for item in headings[index + 1 :] if item.level <= heading.level),
            len(content),
        )
        if not to_plain_text(content[heading.body_start : end]).strip():
            end = nested_end
    return content[heading.body_start : end].strip()


def _list_excerpt(content: str, line_start: int, depth: int) -> str:
    lines = content[line_start:].split("\n")
    kept = [lines[0]]
    for line in lines[1:]:
        match = LIST_LINE_RE.match(line)
        if match is None or len(match.group("dashes")) <= depth:
            break
        kept.append(line)
    items = [LIST_LINE_RE.sub("- ", line, count=1).rstrip() for line in kept]
    return "[ul]\n" + "\n".join(items) + "\n[/ul]"


def _paragraph_excerpt(content: str, start: int, after: int) -> str:
    match = SEGMENT_END_RE.search(content, after)
    end = match.start() if match else len(content)
    return f"[p]{content[start:end].strip()}[/p]"


def _labelled_units(content: str) -> list[tuple[str, int, str]]:
    units = []
    for match in BOLD_RE.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        prefix = content[line_start : match.start()]
        label = anchor_key(to_plain_text(match.group("label")))
        if not label:
            continue
        list_prefix = LIST_PREFIX_RE.match(prefix)
        if list_prefix is not None:
            depth = len(list_prefix.group("dashes"))
            units.append((label, match.start(), _list_excerpt(content, line_start, depth)))
            continue
        unit_prefix = UNIT_PREFIX_RE.search(prefix)
        if unit_prefix is not None:
            units.append(
                (label, match.start(), _paragraph_excerpt(content, match.start(), match.end()))
            )
    return units


def _label_paragraphs(content: str) -> list[tuple[str, int, int]]:
    paragraphs = []
    for match in BOLD_RE.finditer(content):
        prefix = LABEL_PARAGRAPH_PREFIX_RE.search(content, 0, match.start())
        suffix = LABEL_PARAGRAPH_SUFFIX_RE.match(content, match.end())
        if prefix is None or prefix.end() != match.start() or suffix is None:
            continue
        label = anchor_key(to_plain_text(match.group("label")))
        if label:
            paragraphs.append((label, prefix.start(), suffix.end()))
    return paragraphs


def _label_block(content: str, headings: list[_Heading], wanted: str) -> tuple[int, str] | None:
    paragraphs = _label_paragraphs(content)
    for index, (label, start, end) in enumerate(paragraphs):
        if label != wanted:
            continue
        stops = [item[1] for item in paragraphs[index + 1 :]]
        stops += [heading.start for heading in headings if heading.start >= end]
        stop = min(stops, default=len(content))
        return start, content[end:stop].strip()
    return None


def find_excerpt(
    content: str | None,
    anchor: str,
    *,
    forbidden: tuple[str, ...] = FORBIDDEN_SECTIONS,
    blocks: bool = False,
) -> Excerpt:
    source = content or ""
    wanted = anchor_key(anchor)
    if not wanted:
        return Excerpt(AnchorStatus.MISSING)
    headings = _headings(source)
    for index, heading in enumerate(headings):
        if heading.title == wanted:
            if _forbidden_at(headings, heading.start + 1, forbidden):
                return Excerpt(AnchorStatus.FORBIDDEN)
            return Excerpt(AnchorStatus.FOUND, _heading_excerpt(source, headings, index))
    block = _label_block(source, headings, wanted) if blocks else None
    if block is not None:
        position, excerpt = block
        if _forbidden_at(headings, position, forbidden):
            return Excerpt(AnchorStatus.FORBIDDEN)
        return Excerpt(AnchorStatus.FOUND, excerpt)
    for label, position, excerpt in _labelled_units(source):
        if label == wanted:
            if _forbidden_at(headings, position, forbidden):
                return Excerpt(AnchorStatus.FORBIDDEN)
            return Excerpt(AnchorStatus.FOUND, excerpt)
    return Excerpt(AnchorStatus.MISSING)
