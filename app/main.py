import logging
import mimetypes

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.branding import APP_TAGLINE, APP_TITLE, THEME_COLOR
from app.config import get_settings
from app.db import get_engine, ping
from app.deps import ACCOUNT_SETUP_PATH, NotAuthenticatedError, PasswordChangeRequiredError
from app.routes import (
    auth,
    battles,
    chronology,
    dm,
    fiches,
    links,
    lore,
    media,
    monde,
    personnage,
    play,
    player,
)
from app.security import LoginRateLimiter, csrf_protect
from app.services.media_processing import MAX_UPLOAD_BYTES
from app.templating import STATIC_DIR, is_htmx, render

logger = logging.getLogger(__name__)
mimetypes.add_type("font/woff2", ".woff2")

MANIFEST = {
    "name": APP_TITLE,
    "short_name": APP_TITLE,
    "description": APP_TAGLINE,
    "lang": "fr",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": THEME_COLOR,
    "theme_color": THEME_COLOR,
    "icons": [
        {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/static/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' https: data:",
        "style-src 'self'",
        "script-src 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)

ERROR_MESSAGES = {
    400: ("Requête invalide", "La requête envoyée n'est pas valide."),
    403: (
        "Action refusée",
        "Votre session a peut-être expiré. Rechargez la page puis réessayez.",
    ),
    404: (
        "Page introuvable",
        "Cette page s'est perdue dans les archives, ou elle ne vous est pas ouverte.",
    ),
    405: ("Action impossible", "Cette action n'est pas disponible ici."),
    413: (
        "Fichier trop volumineux",
        "L'envoi dépasse la taille autorisée (15 Mo par fichier).",
    ),
    429: ("Trop de tentatives", "Patientez quelques minutes avant de réessayer."),
    503: (
        "Service momentanément indisponible",
        "La base de données se réveille. Réessayez dans quelques secondes.",
    ),
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_static = scope["path"].startswith("/static/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                extra = {
                    b"content-security-policy": CONTENT_SECURITY_POLICY.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"same-origin",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"cross-origin-opener-policy": b"same-origin",
                }
                if self.hsts:
                    extra[b"strict-transport-security"] = b"max-age=31536000"
                if not is_static:
                    extra[b"cache-control"] = b"no-store"
                existing = {name.lower() for name, _ in headers}
                headers.extend(
                    (name, value) for name, value in extra.items() if name not in existing
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers", [])).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_page(Request(scope, receive), 413, {"Connection": "close"})
        await response(scope, receive, send)


def _error_page(
    request: Request, status_code: int, headers: dict[str, str] | None = None
) -> Response:
    title, message = ERROR_MESSAGES.get(status_code, ("Erreur", "Une erreur est survenue."))
    return render(
        request,
        "error.html",
        {"title": title, "message": message, "status_code": status_code},
        status_code=status_code,
        headers=headers,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = FastAPI(
        title="Campaign Companion",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(csrf_protect)],
    )
    app.state.login_limiter = LoginRateLimiter()

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES + 1024 * 1024)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="cc_session",
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.is_production,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(auth.router)
    app.include_router(chronology.dm_router)
    app.include_router(lore.dm_router)
    app.include_router(links.dm_router)
    app.include_router(personnage.dm_router)
    app.include_router(fiches.dm_router)
    app.include_router(fiches.player_router)
    app.include_router(fiches.router)
    app.include_router(monde.dm_router)
    app.include_router(battles.dm_router)
    app.include_router(dm.router)
    app.include_router(chronology.player_router)
    app.include_router(lore.player_router)
    app.include_router(links.player_router)
    app.include_router(battles.player_router)
    app.include_router(player.router)
    app.include_router(play.router)
    app.include_router(media.router)

    @app.get("/health", include_in_schema=False)
    def health() -> JSONResponse:
        try:
            ping(get_engine())
        except OperationalError:
            return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> JSONResponse:
        return JSONResponse(MANIFEST, media_type="application/manifest+json")

    @app.exception_handler(NotAuthenticatedError)
    async def not_authenticated(request: Request, exc: NotAuthenticatedError) -> Response:
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": "/connexion"})
        return RedirectResponse("/connexion", status_code=303)

    @app.exception_handler(PasswordChangeRequiredError)
    async def password_change_required(
        request: Request, exc: PasswordChangeRequiredError
    ) -> Response:
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": ACCOUNT_SETUP_PATH})
        return RedirectResponse(ACCOUNT_SETUP_PATH, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        return _error_page(request, exc.status_code, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        return _error_page(request, 404 if request.method == "GET" else 400)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, exc: OperationalError) -> Response:
        logger.warning("Database unavailable while serving %s", request.url.path)
        return _error_page(request, 503, {"Retry-After": "5"})

    return app


app = create_app()
