from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from app.branding import APP_TAGLINE, APP_TITLE, THEME_COLOR
from app.config import get_settings
from app.presentation import (
    article_image,
    article_media,
    article_tagline,
    fiche_body,
    group_by_section,
    in_section,
    media_url,
    natural_order,
    section_types,
    world_anvil_image,
)
from app.sections import ADVENTURE_KEY, SECTIONS, SECTIONS_BY_KEY, section_of
from app.security import get_csrf_token

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
DISPLAY_TIMEZONE = ZoneInfo("Europe/Paris")
FLASH_SESSION_KEY = "flash"

TEMPLATE_LABELS = {
    "article": "Article",
    "condition": "Condition",
    "document": "Document",
    "ethnicity": "Ethnie",
    "formation": "Formation militaire",
    "item": "Objet",
    "landmark": "Lieu remarquable",
    "language": "Langue",
    "law": "Loi",
    "location": "Lieu",
    "material": "Matériau",
    "militaryconflict": "Conflit militaire",
    "myth": "Mythe",
    "organization": "Organisation",
    "person": "Personnage",
    "plot": "Intrigue",
    "profession": "Profession",
    "prose": "Prose",
    "rank": "Rang",
    "report": "Compte rendu",
    "ritual": "Rituel",
    "settlement": "Établissement",
    "species": "Espèce",
    "spell": "Sort",
    "technology": "Technologie",
    "vehicle": "Véhicule",
}


def template_label(value: str | None) -> str:
    if not value:
        return "Sans type"
    return TEMPLATE_LABELS.get(value.lower(), value.capitalize())


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(DISPLAY_TIMEZONE).strftime("%d/%m/%Y à %H:%M")


def format_filesize(size: int | None) -> str:
    if not size:
        return "—"
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} Ko"
    return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")


def flash(request: Request, message: str, kind: str = "success") -> None:
    request.session[FLASH_SESSION_KEY] = {"message": message, "kind": kind}


def _context(request: Request) -> dict[str, Any]:
    return {
        "csrf_token": get_csrf_token(request),
        "current_user": getattr(request.state, "user", None),
        "pop_flash": lambda: request.session.pop(FLASH_SESSION_KEY, None),
    }


templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[_context])
templates.env.filters.update(
    {
        "template_label": template_label,
        "datetime_fr": format_datetime,
        "tagline": article_tagline,
        "fiche_body": fiche_body,
        "article_image": article_image,
        "article_media": article_media,
        "media_url": media_url,
        "world_anvil_image": world_anvil_image,
        "filesize": format_filesize,
        "in_section": in_section,
        "group_by_section": group_by_section,
        "natural_order": natural_order,
        "section_of": section_of,
    }
)
templates.env.globals.update(
    {
        "APP_TITLE": APP_TITLE,
        "APP_TAGLINE": APP_TAGLINE,
        "THEME_COLOR": THEME_COLOR,
        "SECTIONS": SECTIONS,
        "SECTIONS_BY_KEY": SECTIONS_BY_KEY,
        "ADVENTURE_KEY": ADVENTURE_KEY,
        "section_types": section_types,
        "ASSET_VERSION": "7",
        "player_password_change_enabled": lambda: get_settings().player_password_change,
    }
)


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, context or {}, status_code=status_code, headers=headers
    )


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"
