import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.credits import CATALOGUE_NAME, CATALOGUE_URL, CREDITS
from app.deps import CurrentDm, CurrentPlayer, DbSession, OptionalUser
from app.services.access import accessible_article_ids, dm_render_context, player_render_context
from app.services.fiches import FicheBuilder
from app.services.structures import Viewer
from app.templating import render

KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KINDS = {"lieux": "place", "pnj": "npc", "roles": "role"}
TEMPLATES = {"place": "fiches/place.html", "npc": "fiches/npc.html", "role": "fiches/role.html"}

router = APIRouter()
dm_router = APIRouter(prefix="/mj")
player_router = APIRouter(prefix="/joueur")


def _check_key(key: str) -> None:
    if len(key) > 80 or not KEY_RE.match(key):
        raise HTTPException(status_code=404)


def _page(request: Request, builder: FicheBuilder, kind: str, key: str, dm: bool) -> Response:
    _check_key(key)
    page = getattr(builder, kind)(key)
    if page is None:
        raise HTTPException(status_code=404)
    context: dict[str, Any] = {"page": page, "dm": dm, "home": "/mj" if dm else "/joueur"}
    return render(request, TEMPLATES[kind], context)


def _dm_builder(db: DbSession) -> FicheBuilder:
    return FicheBuilder(db, Viewer(dm=True), dm_render_context(db))


def _player_builder(db: DbSession, player: CurrentPlayer) -> FicheBuilder:
    viewer = Viewer(dm=False, accessible=frozenset(accessible_article_ids(db, player)))
    return FicheBuilder(db, viewer, player_render_context(db, player))


@dm_router.get("/lieux/{key}")
def dm_place(request: Request, key: str, db: DbSession, dm: CurrentDm) -> Response:
    return _page(request, _dm_builder(db), "place", key, True)


@dm_router.get("/pnj/{key}")
def dm_npc(request: Request, key: str, db: DbSession, dm: CurrentDm) -> Response:
    return _page(request, _dm_builder(db), "npc", key, True)


@dm_router.get("/roles/{key}")
def dm_role(request: Request, key: str, db: DbSession, dm: CurrentDm) -> Response:
    return _page(request, _dm_builder(db), "role", key, True)


@player_router.get("/lieux/{key}")
def player_place(request: Request, key: str, db: DbSession, player: CurrentPlayer) -> Response:
    return _page(request, _player_builder(db, player), "place", key, False)


@player_router.get("/pnj/{key}")
def player_npc(request: Request, key: str, db: DbSession, player: CurrentPlayer) -> Response:
    return _page(request, _player_builder(db, player), "npc", key, False)


@player_router.get("/roles/{key}")
def player_role(request: Request, key: str, db: DbSession, player: CurrentPlayer) -> Response:
    return _page(request, _player_builder(db, player), "role", key, False)


@router.get("/lieux/{key}")
def place_redirect(key: str, user: OptionalUser) -> Response:
    _check_key(key)
    if user is None:
        return RedirectResponse("/", status_code=303)
    prefix = "/mj" if user.is_dm else "/joueur"
    return RedirectResponse(f"{prefix}/lieux/{key}", status_code=303)


@router.get("/credits")
def credits_page(request: Request, user: OptionalUser) -> Response:
    if user is None:
        return RedirectResponse("/", status_code=303)
    return render(
        request,
        "fiches/credits.html",
        {
            "credits": CREDITS,
            "catalogue_name": CATALOGUE_NAME,
            "catalogue_url": CATALOGUE_URL,
            "home": "/mj" if user.is_dm else "/joueur",
        },
    )
