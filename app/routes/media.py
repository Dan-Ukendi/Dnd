from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.deps import DbSession, require_user
from app.models import MediaRole, User
from app.services.media import asset_for_user, load_variant

router = APIRouter(prefix="/media")

MEDIA_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
CACHE_CONTROL = "private, max-age=86400"
FILE_EXTENSIONS = {
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}
UNSAFE_FILENAME_CHARS = str.maketrans({char: "_" for char in '\\/:*?"<>|'})


def _etag_matches(request: Request, etag: str) -> bool:
    header = request.headers.get("if-none-match", "")
    candidates = {item.strip().removeprefix("W/") for item in header.split(",")}
    return etag in candidates or "*" in candidates


def _download_name(title: str, content_type: str) -> str:
    name = title.translate(UNSAFE_FILENAME_CHARS).strip(" .") or "fichier"
    return f"{name}{FILE_EXTENSIONS.get(content_type, '')}"


def _content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/{asset_id}/{variant_name}")
def media_file(
    request: Request,
    asset_id: int,
    variant_name: Literal["full", "thumb"],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Response:
    asset = asset_for_user(db, user, asset_id)
    variant = load_variant(db, asset, variant_name) if asset is not None else None
    if asset is None or variant is None:
        raise HTTPException(status_code=404)
    use_gzip = variant.gzip_data is not None and "gzip" in request.headers.get(
        "accept-encoding", ""
    )
    etag = f'"{variant.etag}{"-gz" if use_gzip else ""}"'
    headers = {
        "ETag": etag,
        "Cache-Control": CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": MEDIA_CSP,
    }
    if variant.gzip_data is not None:
        headers["Vary"] = "Accept-Encoding"
    if asset.role == MediaRole.SHEET:
        headers["Content-Disposition"] = _content_disposition(
            _download_name(asset.title, variant.content_type)
        )
    if _etag_matches(request, etag):
        return Response(status_code=304, headers=headers)
    if use_gzip:
        headers["Content-Encoding"] = "gzip"
    body = variant.gzip_data if use_gzip and variant.gzip_data is not None else variant.data
    return Response(body, media_type=variant.content_type, headers=headers)
