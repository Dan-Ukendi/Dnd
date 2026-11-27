from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import Battle, BattleToken, User
from app.services.battles import (
    BattleError,
    crop_image,
    latest_battle,
    move_token,
    player_token,
    player_window,
    visible_tokens,
)
from app.templating import render

dm_router = APIRouter(prefix="/mj/combat")
player_router = APIRouter(prefix="/joueur/combat")

IMAGE_CACHE = "private, max-age=3600"


def _image_response(request: Request, data: bytes, media_type: str, etag: str) -> Response:
    headers = {"ETag": etag, "Cache-Control": IMAGE_CACHE, "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(data, media_type=media_type, headers=headers)


@dm_router.get("")
def dm_battle(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return render(request, "battle/dm.html", {"battle": latest_battle(db)})


@dm_router.get("/{battle_id}/carte")
def dm_battle_map(request: Request, battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    battle = db.get(Battle, battle_id)
    if battle is None or battle.image is None or battle.image_type is None:
        raise HTTPException(status_code=404)
    return _image_response(request, battle.image, battle.image_type, f'"{battle.image_sha256}"')


@dm_router.post("/{battle_id}/pions/{token_id}", status_code=204)
def dm_move_token(
    battle_id: int,
    token_id: int,
    col: Annotated[int, Form()],
    row: Annotated[int, Form()],
    db: DbSession,
    dm: CurrentDm,
) -> Response:
    token = db.get(BattleToken, token_id)
    if token is None or token.battle_id != battle_id:
        raise HTTPException(status_code=404)
    try:
        move_token(token, col, row)
    except BattleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return Response(status_code=204)


@dm_router.post("/{battle_id}/pions/{token_id}/orientation", status_code=204)
def dm_turn_token(
    battle_id: int,
    token_id: int,
    facing: Annotated[int, Form()],
    db: DbSession,
    dm: CurrentDm,
) -> Response:
    token = db.get(BattleToken, token_id)
    if token is None or token.battle_id != battle_id:
        raise HTTPException(status_code=404)
    if not 0 <= facing < 8:
        raise HTTPException(status_code=422)
    token.facing = facing
    db.commit()
    return Response(status_code=204)


@dm_router.post("/{battle_id}/reinitialiser")
def dm_reset_battle(battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    battle = db.get(Battle, battle_id)
    if battle is None:
        raise HTTPException(status_code=404)
    for token in battle.tokens:
        token.col = None
        token.row = None
        token.facing = None
    db.commit()
    return RedirectResponse("/mj/combat", status_code=303)


def _player_view(db: DbSession, player: User) -> dict[str, object]:
    token = player_token(db, player)
    if token is None:
        return {"token": None}
    battle = token.battle
    window = player_window(battle, token)
    tokens = visible_tokens(battle, window) if window is not None else []
    return {"token": token, "battle": battle, "window": window, "tokens": tokens}


@player_router.get("")
def player_battle(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    return render(request, "battle/player.html", _player_view(db, player))


@player_router.get("/vue")
def player_battle_view(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    return render(request, "battle/_player_view.html", _player_view(db, player))


@player_router.get("/carte")
def player_battle_map(
    request: Request, fenetre: str, db: DbSession, player: CurrentPlayer
) -> Response:
    token = player_token(db, player)
    window = player_window(token.battle, token) if token is not None else None
    if (
        token is None
        or window is None
        or window.key != fenetre
        or token.battle.image_sha256 is None
    ):
        raise HTTPException(status_code=404)
    etag = f'"{token.battle.image_sha256}-{window.key}"'
    if request.headers.get("if-none-match") == etag:
        return _image_response(request, b"", "image/webp", etag)
    return _image_response(request, crop_image(token.battle, window), "image/webp", etag)
