import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import nh3
from markupsafe import Markup

MENTION_PATTERN = r"@\[([^\[\]\n]+)\]\(([A-Za-z]+):([0-9A-Fa-f-]{36})\)"
TAG_PATTERN = r"\[(/?)([A-Za-z][A-Za-z0-9]*)((?:[:|=][^\[\]\n]*)?)\]"
TOKEN_RE = re.compile(f"{MENTION_PATTERN}|{TAG_PATTERN}")
LIST_MARKER_RE = re.compile(r"^\s*[-*•]\s+")
IMAGE_ID_RE = re.compile(r"^\s*(\d+)")

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
CONTAINERS = set(HEADINGS) | set(INLINE) | set(BLOCKS) | set(LISTS) | {"li", "url"}
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
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "class", "target"},
    "img": {"src", "alt", "loading", "class"},
    "div": {"class"},
    "span": {"class"},
}
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
            or node.name == "li"
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
                    output.append(f"<p>{_with_breaks(paragraph)}</p>")

        for child in children:
            if _is_block(child):
                flush()
                output.append(self.node(child))
            else:
                run.append(child)
        flush()
        return "\n".join(output)

    def inline(self, children: list[Node]) -> str:
        return "".join(self.node(child) for child in children)

    def node(self, node: Node) -> str:
        if isinstance(node, Text):
            return _escape(node.value)
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
        return f'<a class="internal" href="{_escape(href)}">{label}</a>'

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
            return f"<{tag}>{_with_breaks(self.inline(node.children))}</{tag}>"
        if name in INLINE:
            tag = INLINE[name]
            return f"<{tag}>{self.inline(node.children)}</{tag}>"
        if name == "p":
            content = _with_breaks(self.inline(node.children))
            return f"<p>{content}</p>" if content else ""
        if name == "center":
            return f'<div class="center">{self.block(node.children)}</div>'
        if name == "code":
            return f"<pre>{_escape(_text_of(node.children))}</pre>"
        if name in BLOCKS:
            tag = BLOCKS[name]
            return f"<{tag}>{self.block(node.children)}</{tag}>"
        if name in LISTS:
            tag = LISTS[name]
            items = "".join(f"<li>{item}</li>" for item in self.list_items(node.children))
            return f"<{tag}>{items}</{tag}>"
        if name == "li":
            return f"<li>{_with_breaks(self.inline(node.children))}</li>"
        if name == "url":
            return self.link(node)
        return self.inline(node.children)

    def list_items(self, children: list[Node]) -> list[str]:
        items: list[list[Node]] = []
        current: list[Node] | None = None
        for child in children:
            if isinstance(child, Element) and child.name == "li":
                items.append(child.children)
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
                        items.append(current)
                        current.append(Text(part[marker.end() :] if marker else stripped))
                    elif part:
                        current.append(Text(part))
                continue
            if current is None:
                current = []
                items.append(current)
            current.append(child)
        rendered = [_with_breaks(self.inline(item)) for item in items]
        return [item for item in rendered if item]

    def link(self, node: Element) -> str:
        href = _arg_value(node.arg) or _text_of(node.children).strip()
        label = self.inline(node.children).strip() or _escape(href)
        scheme = urlsplit(href).scheme.lower()
        if is_world_anvil_url(href):
            internal = self.context.resolve_article_url(normalize_wa_url(href))
            if internal is not None:
                return f'<a class="internal" href="{_escape(internal)}">{label}</a>'
            if self.context.player_mode:
                return label
        if scheme in {"http", "https", "mailto"}:
            return f'<a class="external" target="_blank" href="{_escape(href)}">{label}</a>'
        return label


def _is_allowed_image_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in IMAGE_HOSTS


def sanitize_html(fragment: str) -> str:
    return nh3.clean(
        fragment,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )


def render_bbcode(source: str | None, context: RenderContext) -> Markup:
    if not source:
        return Markup("")
    fragment = _Renderer(context).block(parse(source).children)
    return Markup(sanitize_html(fragment))


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
                if _is_block(node):
                    parts.append("\n")

    walk(parse(source).children)
    text = "".join(parts)
    text = re.sub(r"^\s*-\s+", "", text, flags=re.M)
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
