import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import nh3
from markupsafe import Markup

from app.worldanvil.linking import Matcher

MENTION_PATTERN = r"@\[([^\[\]\n]+)\]\(([A-Za-z]+):([0-9A-Fa-f-]{36})\)"
TAG_PATTERN = r"\[(/?)([A-Za-z][A-Za-z0-9]*)((?:[:|=][^\[\]\n]*)?)\]"
TOKEN_RE = re.compile(f"{MENTION_PATTERN}|{TAG_PATTERN}")
LIST_MARKER_RE = re.compile(r"^\s*(-+|[*•])\s+")
MAX_LIST_DEPTH = 3
IMAGE_ID_RE = re.compile(r"^\s*(\d+)")
_TAG_ONLY_RE = re.compile(r"<[^>]+>")
_BLANK_HTML_RE = re.compile(r"^(?:\s|<br>)*$")
PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")

HEADINGS = {"h": "h2", "h1": "h2", "h2": "h3", "h3": "h4", "h4": "h5", "h5": "h6", "h6": "h6"}
INLINE = {
    "b": "strong",
    "strong": "strong",
    "i": "em",
    "em": "em",
    "u": "u",
    "s": "s",
    "strike": "s",
    "sup": "sup",
    "sub": "sub",
    "small": "small",
}
BLOCKS = {"p": "p", "quote": "blockquote", "center": "div", "code": "pre", "aloud": "blockquote"}
LISTS = {"ul": "ul", "ol": "ol"}
CELLS = {"th", "td"}
TABLE_PARTS = {"table", "tr"} | CELLS
CONTAINERS = set(HEADINGS) | set(INLINE) | set(BLOCKS) | set(LISTS) | TABLE_PARTS | {"li", "url"}
ABILITY_HEADERS = (
    frozenset({"for", "dex", "con", "int", "sag", "cha"}),
    frozenset({"str", "dex", "con", "int", "wis", "cha"}),
)
VOIDS = {"br", "hr", "img"}
RESTRICTED_PREFIXES = (
    "secret",
    "subcontainer",
    "gm",
    "redacted",
    "private",
    "subscriber",
    "passcode",
)

ALLOWED_TAGS = {
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "u",
    "s",
    "sup",
    "sub",
    "small",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "a",
    "img",
    "div",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "class", "target", "aria-label"},
    "img": {"src", "alt", "loading", "class"},
    "div": {"class"},
    "span": {"class"},
    "table": {"class"},
    "th": {"scope"},
    "td": {"data-label"},
}
ALLOWED_ATTRIBUTE_VALUES = {"a": {"hx-boost": {"true"}}}
IMAGE_HOSTS = frozenset(
    {
        "worldanvil.com",
        "www.worldanvil.com",
        "wa-cdn.nyc3.cdn.digitaloceanspaces.com",
        "wa-cdn.nyc3.digitaloceanspaces.com",
    }
)


def is_restricted_tag(name: str) -> bool:
    return name.startswith(RESTRICTED_PREFIXES)


def is_world_anvil_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "worldanvil.com" or host.endswith(".worldanvil.com")


def normalize_wa_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host == "worldanvil.com":
        host = "www.worldanvil.com"
    return f"{host}{parts.path.rstrip('/')}"


@dataclass(frozen=True)
class ImageRef:
    url: str
    title: str = ""


@dataclass(frozen=True)
class RenderContext:
    resolve_article: Callable[[str], str | None]
    resolve_article_url: Callable[[str], str | None]
    resolve_image: Callable[[int], ImageRef | None]
    player_mode: bool
    matcher: Matcher | None = None
    current: str | None = None


@dataclass
class Text:
    value: str


@dataclass
class Mention:
    label: str
    kind: str
    wa_id: str


@dataclass
class Void:
    name: str
    arg: str
    raw: str


@dataclass
class Element:
    name: str
    arg: str = ""
    raw_open: str = ""
    children: list["Node"] = field(default_factory=list)


Node = Text | Mention | Void | Element


def _close_table_parts(stack: list[Element], name: str) -> None:
    if name == "table":
        return
    closes = TABLE_PARTS - {"table"} if name == "tr" else CELLS
    boundary = {"table"} if name == "tr" else {"tr", "table"}
    found: int | None = None
    for index in range(len(stack) - 1, 0, -1):
        current = stack[index].name
        if current in boundary:
            break
        if current in closes:
            found = index
    if found is not None:
        del stack[found:]


def parse(source: str) -> Element:
    source = source.replace("[*]", "\n- ")
    root = Element("")
    stack: list[Element] = [root]
    position = 0
    for match in TOKEN_RE.finditer(source):
        if match.start() > position:
            stack[-1].children.append(Text(source[position : match.start()]))
        position = match.end()
        if match.group(1) is not None:
            stack[-1].children.append(
                Mention(match.group(1), match.group(2).lower(), match.group(3).lower())
            )
            continue
        raw = match.group(0)
        closing = match.group(4) == "/"
        name = match.group(5).lower()
        arg = match.group(6) or ""
        if closing:
            index = next(
                (i for i in range(len(stack) - 1, 0, -1) if stack[i].name == name),
                None,
            )
            if index is None:
                stack[-1].children.append(Text(raw))
            else:
                del stack[index:]
            continue
        restricted = is_restricted_tag(name)
        if name in VOIDS or (
            restricted
            and name.startswith("secret")
            and not re.search(rf"\[/{re.escape(name)}\]", source[position:], re.I)
        ):
            stack[-1].children.append(Void(name, arg, raw))
            continue
        if name in TABLE_PARTS:
            _close_table_parts(stack, name)
        if name in CONTAINERS or restricted:
            element = Element(name, arg, raw)
            stack[-1].children.append(element)
            stack.append(element)
            continue
        stack[-1].children.append(Text(raw))
    if position < len(source):
        stack[-1].children.append(Text(source[position:]))
    return root


def _is_block(node: Node) -> bool:
    if isinstance(node, Element):
        return (
            node.name in HEADINGS
            or node.name in BLOCKS
            or node.name in LISTS
            or node.name in {"li", "table"}
            or is_restricted_tag(node.name)
        )
    if isinstance(node, Void):
        return node.name == "hr"
    return False


def _arg_value(arg: str) -> str:
    return arg[1:].strip() if arg else ""


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _text_of(nodes: Iterable[Node]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, Text):
            parts.append(node.value)
        elif isinstance(node, Mention):
            parts.append(node.label)
        elif isinstance(node, Element):
            parts.append(_text_of(node.children))
    return "".join(parts)


def _with_breaks(fragment: str) -> str:
    return fragment.strip().replace("\n", "<br>")


class _Renderer:
    def __init__(self, context: RenderContext) -> None:
        self.context = context
        self.linked: set[str] = set()
        self.verbatim = 0
        self.heading = False

    def text(self, value: str) -> str:
        matcher = self.context.matcher
        if matcher is None or self.verbatim:
            return _escape(value)
        parts: list[str] = []
        position = 0
        for found in matcher.find(value):
            label = value[found.start : found.end]
            parts.append(_escape(value[position : found.start]))
            parts.append(self.autolink(label, found.wa_id))
            position = found.end
        parts.append(_escape(value[position:]))
        return "".join(parts)

    def autolink(self, label: str, wa_id: str | None) -> str:
        matcher = self.context.matcher
        target = matcher.targets.get(wa_id) if matcher and wa_id else None
        if target is None or wa_id == self.context.current or wa_id in self.linked:
            return _escape(label)
        href = self.context.resolve_article(target.wa_id)
        if href is None:
            return _escape(label)
        self.linked.add(target.wa_id)
        return (
            f'<a class="autolink autolink--{_escape(target.kind)}" href="{_escape(href)}" '
            f'aria-label="{_escape(f"{label}, fiche {target.label.lower()}")}" '
            f'hx-boost="true">{_escape(label)}</a>'
        )

    def verbatim_inline(self, children: list[Node]) -> str:
        self.verbatim += 1
        try:
            return self.inline(children)
        finally:
            self.verbatim -= 1

    def block(self, children: list[Node]) -> str:
        output: list[str] = []
        run: list[Node] = []

        def flush() -> None:
            fragment = self.inline(run).strip()
            run.clear()
            if not fragment:
                return
            for paragraph in re.split(r"\n[ \t]*\n", fragment):
                if paragraph.strip():
                    output.append(_paragraph(_with_breaks(paragraph)))

        for child in children:
            if _is_block(child):
                flush()
                rendered = self.node(child)
                if rendered:
                    output.append(rendered)
            else:
                run.append(child)
        flush()
        return "\n".join(output)

    def inline(self, children: list[Node]) -> str:
        return "".join(self.node(child) for child in children)

    def node(self, node: Node) -> str:
        if isinstance(node, Text):
            return self.text(node.value)
        if isinstance(node, Mention):
            return self.mention(node)
        if isinstance(node, Void):
            return self.void(node)
        return self.element(node)

    def mention(self, node: Mention) -> str:
        href = self.context.resolve_article(node.wa_id)
        label = _escape(node.label)
        if href is None:
            return label
        self.linked.add(node.wa_id)
        return f'<a class="internal" href="{_escape(href)}" hx-boost="true">{label}</a>'

    def void(self, node: Void) -> str:
        if node.name == "br":
            return "<br>"
        if node.name == "hr":
            return "<hr>"
        if node.name == "img":
            return self.image(node)
        if self.context.player_mode:
            return ""
        return f'<span class="gm-only">{_escape(node.raw)}</span>'

    def image(self, node: Void) -> str:
        match = IMAGE_ID_RE.match(_arg_value(node.arg))
        image = self.context.resolve_image(int(match.group(1))) if match else None
        if image is None or not _is_allowed_image_url(image.url):
            return "" if self.context.player_mode else '<span class="missing">[image]</span>'
        return (
            f'<img class="wa-image" loading="lazy" src="{_escape(image.url)}" '
            f'alt="{_escape(image.title)}">'
        )

    def element(self, node: Element) -> str:
        name = node.name
        if is_restricted_tag(name):
            if self.context.player_mode:
                return ""
            return f'<div class="gm-only">{self.block(node.children)}</div>'
        if name in HEADINGS:
            tag = HEADINGS[name]
            self.heading = True
            try:
                content = _with_breaks(self.verbatim_inline(node.children))
            finally:
                self.heading = False
            self.linked.clear()
            return f"<{tag}>{content}</{tag}>" if _TAG_ONLY_RE.sub("", content).strip() else ""
        if name in INLINE:
            tag = INLINE[name]
            if tag == "strong" and self.heading:
                return self.inline(node.children)
            return f"<{tag}>{self.inline(node.children)}</{tag}>"
        if name == "p":
            return _paragraph(_with_breaks(self.inline(node.children)))
        if name == "center":
            return f'<div class="center">{self.block(node.children)}</div>'
        if name == "code":
            return f"<pre>{_escape(_text_of(node.children))}</pre>"
        if name in BLOCKS:
            tag = BLOCKS[name]
            return f"<{tag}>{self.block(node.children)}</{tag}>"
        if name in LISTS:
            return _nested_list(LISTS[name], self.list_items(node.children))
        if name == "li":
            return f"<li>{_with_breaks(self.inline(node.children))}</li>"
        if name == "url":
            return self.link(node)
        if name == "table":
            return self.table(node)
        return self.inline(node.children)

    def table(self, node: Element) -> str:
        rows = [row for row in self.rows(node.children) if row]
        if not rows:
            return ""
        header: list[tuple[str, str]] | None = None
        if len(rows) > 1 and all(tag == "th" for tag, _ in rows[0]):
            header, rows = rows[0], rows[1:]
        kind = _ability_table_kind(header, rows)
        labels = [_cell_text(content) for _, content in header] if header else []
        parts = [f'<table class="{kind}">' if kind else "<table>"]
        if header:
            cells = "".join(f'<th scope="col">{content}</th>' for _, content in header)
            parts.append(f"<thead><tr>{cells}</tr></thead>")
        parts.append("<tbody>")
        for row in rows:
            cells = []
            for index, (tag, content) in enumerate(row):
                if tag == "th":
                    cells.append(f'<th scope="row">{content}</th>')
                elif kind == "stat-block" and index < len(labels):
                    cells.append(f'<td data-label="{_escape(labels[index])}">{content}</td>')
                else:
                    cells.append(f"<td>{content}</td>")
            parts.append(f"<tr>{''.join(cells)}</tr>")
        parts.append("</tbody></table>")
        return f'<div class="table-scroll">{"".join(parts)}</div>'

    def rows(self, children: list[Node]) -> list[list[tuple[str, str]]]:
        rows: list[list[tuple[str, str]]] = []
        stray: list[Node] = []
        for child in [*children, None]:
            if child is None or (isinstance(child, Element) and child.name == "tr"):
                if stray:
                    rows.append([("td", self.cell(stray))])
                    stray = []
                if child is not None:
                    rows.append(self.cells(child.children))
            elif not _is_blank(child):
                stray.append(child)
        return rows

    def cells(self, children: list[Node]) -> list[tuple[str, str]]:
        cells: list[tuple[str, str]] = []
        stray: list[Node] = []
        for child in [*children, None]:
            if child is None or (isinstance(child, Element) and child.name in CELLS):
                if stray:
                    cells.append(("td", self.cell(stray)))
                    stray = []
                if child is not None:
                    cells.append((child.name, self.cell(child.children)))
            elif not _is_blank(child):
                stray.append(child)
        return cells

    def cell(self, children: list[Node]) -> str:
        if any(_is_block(child) for child in children):
            return self.block(children)
        return _with_breaks(self.inline(children))

    def list_items(self, children: list[Node]) -> list[tuple[int, str]]:
        items: list[tuple[int, list[Node]]] = []
        current: list[Node] | None = None
        for child in children:
            if isinstance(child, Element) and child.name == "li":
                items.append((1, child.children))
                current = None
                continue
            if isinstance(child, Text):
                for index, part in enumerate(child.value.split("\n")):
                    if index > 0:
                        current = None
                    if current is None:
                        stripped = part.strip()
                        if not stripped:
                            continue
                        marker = LIST_MARKER_RE.match(part)
                        current = []
                        items.append((_list_depth(marker), current))
                        current.append(Text(part[marker.end() :] if marker else stripped))
                    elif part:
                        current.append(Text(part))
                continue
            if current is None:
                current = []
                items.append((1, current))
            current.append(child)
        rendered = [(depth, _with_breaks(self.inline(item))) for depth, item in items]
        return [(depth, item) for depth, item in rendered if not _BLANK_HTML_RE.match(item)]

    def link(self, node: Element) -> str:
        href = _arg_value(node.arg) or _text_of(node.children).strip()
        label = self.verbatim_inline(node.children).strip() or _escape(href)
        scheme = urlsplit(href).scheme.lower()
        if is_world_anvil_url(href):
            internal = self.context.resolve_article_url(normalize_wa_url(href))
            if internal is not None:
                return f'<a class="internal" href="{_escape(internal)}" hx-boost="true">{label}</a>'
            if self.context.player_mode:
                return label
        if scheme in {"http", "https", "mailto"}:
            return f'<a class="external" target="_blank" href="{_escape(href)}">{label}</a>'
        return label


def _paragraph(content: str) -> str:
    return "" if _BLANK_HTML_RE.match(content) else f"<p>{content}</p>"


def _list_depth(marker: re.Match[str] | None) -> int:
    if marker is None or not marker.group(1).startswith("-"):
        return 1
    return min(len(marker.group(1)), MAX_LIST_DEPTH)


def _nested_list(tag: str, items: list[tuple[int, str]]) -> str:
    if not items:
        return ""
    parts = [f"<{tag}>"]
    level = 1
    item_open = False
    for depth, content in items:
        depth = min(depth, level + 1 if item_open else level)
        if depth > level:
            parts.append(f"<{tag}>")
            level = depth
        else:
            while level > depth:
                parts.append(f"</li></{tag}>")
                level -= 1
            if item_open:
                parts.append("</li>")
        parts.append(f"<li>{content}")
        item_open = True
    parts.append(f"</li></{tag}>" * level)
    return "".join(parts)


def _is_blank(node: Node) -> bool:
    return isinstance(node, Text) and not node.value.strip()


def _cell_text(content: str) -> str:
    return html.unescape(_TAG_ONLY_RE.sub("", content)).strip()


def _ability_labels(values: list[str]) -> frozenset[str]:
    return frozenset(value.casefold()[:3] for value in values)


def _ability_table_kind(
    header: list[tuple[str, str]] | None, rows: list[list[tuple[str, str]]]
) -> str:
    if header is None:
        return ""
    if len(header) == 6 and _ability_labels([_cell_text(c) for _, c in header]) in ABILITY_HEADERS:
        return "stat-row"
    if (
        len(rows) == 6
        and len(header) >= 2
        and all(len(row) == len(header) for row in rows)
        and _ability_labels([_cell_text(row[0][1]) for row in rows]) in ABILITY_HEADERS
    ):
        return "stat-block"
    return ""


def _is_allowed_image_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in IMAGE_HOSTS


def sanitize_html(fragment: str) -> str:
    return nh3.clean(
        fragment,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        tag_attribute_values=ALLOWED_ATTRIBUTE_VALUES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


def render_bbcode(source: str | None, context: RenderContext) -> Markup:
    if not source:
        return Markup("")
    fragment = _Renderer(context).block(parse(source).children)
    return Markup(sanitize_html(fragment))


def render_plain_text(
    source: str | None, context: RenderContext, *, paragraphs: bool = False
) -> Markup:
    if not source or not source.strip():
        return Markup("")
    renderer = _Renderer(context)
    if not paragraphs:
        return Markup(sanitize_html(renderer.text(source)))
    parts = [part for part in PARAGRAPH_RE.split(source) if part.strip()]
    return Markup(sanitize_html("".join(f"<p>{renderer.text(part)}</p>" for part in parts)))


def to_plain_text(source: str | None) -> str:
    if not source:
        return ""
    parts: list[str] = []

    def walk(nodes: list[Node]) -> None:
        for node in nodes:
            if isinstance(node, Text):
                parts.append(node.value)
            elif isinstance(node, Mention):
                parts.append(node.label)
            elif isinstance(node, Void):
                if node.name in {"br", "hr"}:
                    parts.append("\n")
            else:
                walk(node.children)
                if _is_block(node) or node.name == "tr":
                    parts.append("\n")
                elif node.name in CELLS:
                    parts.append(" ")

    walk(parse(source).children)
    text = "".join(parts)
    text = re.sub(r"^\s*-+\s+", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass(frozen=True)
class ExtractedLinks:
    article_ids: frozenset[str]
    world_anvil_urls: frozenset[str]


def extract_links(source: str | None) -> ExtractedLinks:
    ids: set[str] = set()
    urls: set[str] = set()
    if source:

        def walk(nodes: list[Node]) -> None:
            for node in nodes:
                if isinstance(node, Mention):
                    ids.add(node.wa_id)
                elif isinstance(node, Element):
                    if node.name == "url":
                        href = _arg_value(node.arg) or _text_of(node.children).strip()
                        if is_world_anvil_url(href):
                            urls.add(normalize_wa_url(href))
                    walk(node.children)

        walk(parse(source).children)
    return ExtractedLinks(frozenset(ids), frozenset(urls))


def extract_image_ids(source: str | None) -> set[int]:
    ids: set[int] = set()
    if not source:
        return ids
    for match in TOKEN_RE.finditer(source):
        if match.group(5) and match.group(5).lower() == "img" and match.group(4) != "/":
            found = IMAGE_ID_RE.match(_arg_value(match.group(6) or ""))
            if found:
                ids.add(int(found.group(1)))
    return ids


def restricted_tags_in(source: str | None) -> set[str]:
    if not source:
        return set()
    return {
        match.group(5).lower()
        for match in TOKEN_RE.finditer(source)
        if match.group(5) and is_restricted_tag(match.group(5).lower())
    }
