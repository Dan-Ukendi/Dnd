import html
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit

from markupsafe import Markup

from app.models import MediaAsset, MediaRole, WaArticle
from app.sections import SECTIONS_BY_KEY, TEMPLATE_SECTIONS, section_key
from app.worldanvil.bbcode import IMAGE_HOSTS, to_plain_text

EMPTY_LEAD_RE = re.compile(r"^(?:\s+|\[p\]\s*\[/p\]|\[(h\d?)(?:\|[^\]]*)?\]\s*\[/\1\])", re.S)
HEADING_BBCODE_RE = re.compile(r"^\[(h\d?)(?:\|[^\]]*)?\](?P<heading>.*?)\[/\1\]", re.S)
ITALIC_BBCODE_RE = re.compile(r"^\[p\]\s*\[i\](?P<tagline>.+?)\[/i\]\s*\[/p\]", re.S)
TITLE_SEPARATOR_RE = re.compile(r"\s*[:—–-]\s*")
LEADING_HEADING_RE = re.compile(r"^\s*<h2>(?P<inner>.*?)</h2>\s*", re.S)
LEADING_TAGLINE_RE = re.compile(r"^\s*<p><em>(?P<inner>[^<]*?)</em></p>\s*", re.S)
TAG_RE = re.compile(r"<[^>]+>")
LEADING_ARTICLE_RE = re.compile(r"^(?:le|la|les|l|un|une) ")
MAX_TAGLINE_LENGTH = 160


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()
    return LEADING_ARTICLE_RE.sub("", words)


def _heading_matches_title(heading: str, title: str) -> bool:
    normalized = _normalize(heading)
    return not normalized or normalized in _normalize(title)


def _title_remainder(heading: str, title: str) -> str:
    parts = TITLE_SEPARATOR_RE.split(heading, maxsplit=1)
    if len(parts) == 2 and _normalize(parts[0]) == _normalize(title):
        return parts[1].strip()
    return ""


def _is_title_heading(heading: str, title: str) -> bool:
    return _heading_matches_title(heading, title) or bool(_title_remainder(heading, title))


def _skip_empty_lead(source: str) -> str:
    while True:
        match = EMPTY_LEAD_RE.match(source)
        if match is None or not match.group(0):
            return source
        source = source[match.end() :]


def article_tagline(article: WaArticle) -> str:
    source = _skip_empty_lead(article.content or "")
    remainder = ""
    heading_match = HEADING_BBCODE_RE.match(source)
    if heading_match is not None:
        heading = to_plain_text(heading_match.group("heading")).strip()
        if not _is_title_heading(heading, article.title):
            return ""
        remainder = _title_remainder(heading, article.title)
        source = _skip_empty_lead(source[heading_match.end() :])
    italic = ITALIC_BBCODE_RE.match(source)
    tagline = to_plain_text(italic.group("tagline")).strip() if italic else remainder
    return tagline if len(tagline) <= MAX_TAGLINE_LENGTH else ""


def fiche_body(rendered: Markup | str, title: str) -> Markup:
    body = str(rendered)
    for _ in range(3):
        heading = LEADING_HEADING_RE.match(body)
        if heading is None:
            break
        text = html.unescape(TAG_RE.sub("", heading.group("inner")))
        if not _is_title_heading(text, title):
            break
        body = body[heading.end() :]
    tagline = LEADING_TAGLINE_RE.match(body)
    if tagline is not None:
        body = body[tagline.end() :]
    return Markup(body)  # noqa: S704


def _allowed_image(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in IMAGE_HOSTS


def article_media(article: WaArticle, role: str) -> MediaAsset | None:
    return next((asset for asset in article.media if asset.role == role), None)


def media_url(asset: MediaAsset, variant: str = "full") -> str:
    return f"/media/{asset.id}/{variant}?v={asset.version}"


def article_image(article: WaArticle, player_mode: bool = False) -> str | None:
    for role in (MediaRole.PORTRAIT, MediaRole.MAP):
        asset = article_media(article, role)
        if asset is not None:
            return media_url(asset, "thumb")
    return world_anvil_image(article, player_mode)


def world_anvil_image(article: WaArticle, player_mode: bool = False) -> str | None:
    raw = article.raw or {}
    for key in ("portrait", "cover"):
        image = raw.get(key)
        if not isinstance(image, dict) or image.get("id") in (None, -1, "-1"):
            continue
        if player_mode and (image.get("state") != "public" or image.get("subscribergroups")):
            continue
        if _allowed_image(image.get("url")):
            return image["url"]
    return None


def in_section(articles: Iterable[WaArticle], key: str | None) -> list[WaArticle]:
    if not key or key not in SECTIONS_BY_KEY:
        return list(articles)
    return [
        article for article in articles if section_key(article.wa_id, article.template_type) == key
    ]


def section_types(
    types: Iterable[tuple[str, int]], key: str | None, articles: Iterable[WaArticle]
) -> list[str]:
    available = [value for value, _ in types]
    if not key or key not in SECTIONS_BY_KEY:
        return available
    present = {article.template_type for article in in_section(articles, key)}
    return [
        value
        for value in available
        if TEMPLATE_SECTIONS.get(value, "lore") == key or value in present
    ]


def _natural_key(article: WaArticle) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", _normalize(article.title))
        if part
    ]


def natural_order(articles: Iterable[WaArticle]) -> list[WaArticle]:
    return sorted(articles, key=_natural_key)


def group_by_section(articles: Iterable[WaArticle]) -> list[tuple[str, list[WaArticle]]]:
    groups: dict[str, list[WaArticle]] = {key: [] for key in SECTIONS_BY_KEY}
    for article in articles:
        groups[section_key(article.wa_id, article.template_type)].append(article)
    return [(key, items) for key, items in groups.items() if items]
