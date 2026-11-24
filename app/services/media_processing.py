import base64
import contextlib
import gzip
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Literal
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as parse_xml
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models import MediaRole

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 60_000_000
PORTRAIT_EDGE = 1400
MAP_EDGE = 2400
THUMB_BOX = (480, 640)
WEBP_QUALITY = 82
RASTER_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
SVG_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "textPath",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "pattern",
        "symbol",
        "use",
        "marker",
        "style",
        "filter",
        "feBlend",
        "feColorMatrix",
        "feComponentTransfer",
        "feComposite",
        "feDisplacementMap",
        "feDropShadow",
        "feFlood",
        "feFuncA",
        "feFuncB",
        "feFuncG",
        "feFuncR",
        "feGaussianBlur",
        "feMerge",
        "feMergeNode",
        "feMorphology",
        "feOffset",
        "feTurbulence",
        "image",
    }
)
LINK_ATTRIBUTES = frozenset({"href", f"{{{XLINK_NS}}}href"})
MAP_THUMB_BOX = (800, 800)
EMBEDDED_PAYLOAD_RE = re.compile(
    rb'href="data:image/(?:png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)"'
)
EMBEDDED_RASTER_RE = re.compile(r"^data:image/(?:png|jpeg|jpg|webp);base64,[A-Za-z0-9+/=\s]+$")
KEPT_XML_ATTRIBUTES = frozenset({f"{{{XML_NS}}}space", f"{{{XML_NS}}}lang"})
URL_REF_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
FORBIDDEN_CSS_RE = re.compile(r"@import|expression\s*\(|javascript:|behavior\s*:", re.I)
LENGTH_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$")

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class MediaError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Variant:
    name: Literal["full", "thumb"]
    content_type: str
    data: bytes
    width: int | None
    height: int | None
    gzip_data: bytes | None = None

    @property
    def etag(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ProcessedMedia:
    variants: tuple[Variant, ...]

    @property
    def full(self) -> Variant:
        return self.variants[0]


def detect_kind(data: bytes) -> Literal["raster", "svg", "pdf"] | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")) or (
        data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    ):
        return "raster"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<svg", b"<?xml", b"<!doctype svg", b"<!--")) and b"<svg" in data[:65536]:
        return "svg"
    return None


def _encode_webp(image: Image.Image, icc_profile: bytes | None) -> bytes:
    buffer = io.BytesIO()
    options: dict[str, object] = {"quality": WEBP_QUALITY, "method": 6, "exif": b""}
    if icc_profile:
        options["icc_profile"] = icc_profile
    image.save(buffer, "WEBP", **options)
    return buffer.getvalue()


def process_raster(data: bytes, long_edge: int) -> ProcessedMedia:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in RASTER_FORMATS:
                raise MediaError("unsupported_type")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise MediaError("image_too_large")
            source.load()
            icc_profile = source.info.get("icc_profile")
            image = ImageOps.exif_transpose(source)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise MediaError("invalid_image") from exc
    has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_alpha else "RGB")
    variants = []
    for name, box in (("full", (long_edge, long_edge)), ("thumb", THUMB_BOX)):
        resized = image.copy()
        resized.thumbnail(box, Image.Resampling.LANCZOS)
        variants.append(
            Variant(
                name,  # type: ignore[arg-type]
                "image/webp",
                _encode_webp(resized, icc_profile),
                resized.width,
                resized.height,
            )
        )
    return ProcessedMedia(tuple(variants))


def _local_reference(value: str) -> bool:
    return value.strip().startswith("#")


def _safe_css(value: str) -> bool:
    if FORBIDDEN_CSS_RE.search(value):
        return False
    return all(_local_reference(match.group(2)) for match in URL_REF_RE.finditer(value))


def _clean_attributes(element: ET.Element) -> None:
    for key, value in list(element.attrib.items()):
        del element.attrib[key]
        if key in LINK_ATTRIBUTES:
            if element.tag == "image":
                if EMBEDDED_RASTER_RE.match(value.strip()):
                    element.set("href", value.strip())
            elif _local_reference(value):
                element.set("href", value.strip())
            continue
        if key.startswith("{"):
            if key in KEPT_XML_ATTRIBUTES:
                element.set(key, value)
            continue
        lowered = value.casefold()
        if key.casefold().startswith("on") or "javascript:" in lowered or not _safe_css(value):
            continue
        element.set(key, value)


def _local_name(tag: object) -> str | None:
    if not isinstance(tag, str) or not tag.startswith(f"{{{SVG_NS}}}"):
        return None
    return tag[len(SVG_NS) + 2 :]


def _clean_tree(element: ET.Element) -> None:
    for child in list(element):
        name = _local_name(child.tag)
        if name == "a":
            name = "g"
        allowed = name in SVG_ELEMENTS and not (name == "style" and not _safe_css(child.text or ""))
        if not allowed:
            index = list(element).index(child)
            if child.tail and child.tail.strip():
                if index > 0:
                    previous = element[index - 1]
                    previous.tail = (previous.tail or "") + child.tail
                else:
                    element.text = (element.text or "") + child.tail
            element.remove(child)
            continue
        child.tag = name
        _clean_attributes(child)
        if name == "image" and "href" not in child.attrib:
            element.remove(child)
            continue
        _clean_tree(child)


def _dimension(value: str | None) -> float | None:
    match = LENGTH_RE.match(value or "")
    return float(match.group(1)) if match else None


def _svg_size(root: ET.Element) -> tuple[int | None, int | None]:
    width, height = _dimension(root.get("width")), _dimension(root.get("height"))
    view_box = (root.get("viewBox") or "").replace(",", " ").split()
    if (width is None or height is None) and len(view_box) == 4:
        with contextlib.suppress(ValueError):
            width, height = float(view_box[2]), float(view_box[3])
    if not width or not height:
        return None, None
    return round(width), round(height)


def sanitize_svg(data: bytes) -> tuple[bytes, int | None, int | None]:
    try:
        root = parse_xml(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException, ValueError) as exc:
        raise MediaError("invalid_svg") from exc
    if _local_name(root.tag) != "svg":
        raise MediaError("invalid_svg")
    root.tag = "svg"
    _clean_attributes(root)
    _clean_tree(root)
    root.set("xmlns", SVG_NS)
    width, height = _svg_size(root)
    markup = ET.tostring(root, encoding="unicode")
    return markup.encode("utf-8"), width, height


def _embedded_raster_thumb(markup: bytes) -> Variant | None:
    payloads = EMBEDDED_PAYLOAD_RE.findall(markup)
    if not payloads:
        return None
    try:
        raster = base64.b64decode(max(payloads, key=len), validate=False)
        with Image.open(io.BytesIO(raster)) as source:
            if source.format not in RASTER_FORMATS:
                return None
            if source.width * source.height > MAX_IMAGE_PIXELS:
                return None
            image = source.convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return None
    image.thumbnail(MAP_THUMB_BOX, Image.Resampling.LANCZOS)
    return Variant("thumb", "image/webp", _encode_webp(image, None), image.width, image.height)


def process_svg(data: bytes) -> ProcessedMedia:
    cleaned, width, height = sanitize_svg(data)
    compressed = gzip.compress(cleaned, compresslevel=9, mtime=0)
    variants = [Variant("full", "image/svg+xml", cleaned, width, height, compressed)]
    thumb = _embedded_raster_thumb(cleaned)
    if thumb is not None:
        variants.append(thumb)
    return ProcessedMedia(tuple(variants))


def process_pdf(data: bytes) -> ProcessedMedia:
    if not data.startswith(b"%PDF-"):
        raise MediaError("invalid_pdf")
    return ProcessedMedia((Variant("full", "application/pdf", data, None, None),))


def process_upload(role: MediaRole, data: bytes) -> ProcessedMedia:
    if not data:
        raise MediaError("empty_file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError("file_too_large")
    kind = detect_kind(data)
    if role == MediaRole.SHEET:
        if kind != "pdf":
            raise MediaError("invalid_pdf")
        return process_pdf(data)
    if role == MediaRole.MAP and kind == "svg":
        return process_svg(data)
    if kind != "raster":
        raise MediaError("unsupported_type")
    return process_raster(data, MAP_EDGE if role == MediaRole.MAP else PORTRAIT_EDGE)
