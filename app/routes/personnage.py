from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, DbSession
from app.models import PERSON_CATEGORIES, User, WaArticle
from app.services import person_tags as service
from app.templating import flash

dm_router = APIRouter(prefix="/mj")

PERSON_SECTION = service.PERSON_SECTION
TAG_ERRORS = {
    "article": "Seules les fiches de la section Personnage portent un tag.",
    "category": "Choisissez un tag : Lore, Character ou Rencontre campagne.",
}


def _pj_id(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 9 else None


def _view(tag: str, pj: str) -> str:
    if tag == service.LINKS_TAG:
        return "lien" if pj else "liens"
    return "list" if tag else "tiles"


def dm_page_context(db: DbSession, tag: str, pj: str) -> dict[str, Any]:
    if tag and tag not in service.TAG_ORDER:
        raise HTTPException(status_code=404)
    tiles = service.dm_tiles(db)
    context: dict[str, Any] = {
        "dm": True,
        "base": "/mj",
        "tag": tag,
        "view": _view(tag, pj),
        "tiles": tiles,
    }
    if tag == service.LINKS_TAG:
        groups = service.dm_link_groups(db)
        context["groups"] = groups
        if pj:
            context["group"] = _find_group(groups, pj)
    return context


def player_page_context(db: DbSession, player: User, tag: str, pj: str) -> dict[str, Any]:
    if tag and tag not in service.PLAYER_TAGS:
        raise HTTPException(status_code=404)
    view = service.player_view(db, player)
    context: dict[str, Any] = {
        "dm": False,
        "base": "/joueur",
        "tag": tag,
        "view": _view(tag, pj),
        "tiles": view.tiles(),
        "entries": view.characters,
        "groups": view.groups,
    }
    if tag == service.LINKS_TAG and pj:
        context["group"] = _find_group(view.groups, pj)
    return context


def _find_group(groups: list[service.LinkGroup], pj: str) -> service.LinkGroup:
    character_id = _pj_id(pj)
    group = next((item for item in groups if item.character.id == character_id), None)
    if group is None:
        raise HTTPException(status_code=404)
    return group


@dm_router.post("/articles/{wa_id}/tags")
def update_tags(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    category: Annotated[str, Form(max_length=20)] = "",
    pj: Annotated[list[str] | None, Form()] = None,
) -> Response:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    wanted = {value for value in (_pj_id(item) for item in pj or []) if value is not None}
    try:
        service.update_fiche(db, article, category, wanted)
    except service.PersonTagError as exc:
        db.rollback()
        flash(request, TAG_ERRORS.get(exc.code, "Tags non enregistrés."), "error")
    else:
        label = service.CATEGORY_LABELS[category] if category in PERSON_CATEGORIES else category
        flash(request, f"Tags enregistrés : {label}.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)
