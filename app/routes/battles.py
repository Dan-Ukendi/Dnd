from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import Battle, BattleMap, BattleToken, User
from app.services.battles import (
    ENEMY_COLORS,
    MAX_ENEMIES_PER_COLOR,
    BattleError,
    active_battle,
    create_battle,
    crop_image,
    default_battle_name,
    default_enemies,
    end_battle,
    enemy_counts,
    list_battles,
    list_maps,
    move_token,
    player_token,
    player_window,
    resume_battle,
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


def _battle(db: DbSession, battle_id: int) -> Battle:
    battle = db.get(Battle, battle_id)
    if battle is None:
        raise HTTPException(status_code=404)
    return battle


def _board(request: Request, db: DbSession, battle: Battle | None) -> Response:
    active = active_battle(db)
    context = {
        "battle": battle,
        "battles": list_battles(db),
        "active_id": active.id if active else None,
    }
    return render(request, "battle/dm.html", context)


@dm_router.get("")
def dm_battle(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return _board(request, db, active_battle(db))


@dm_router.get("/nouveau")
def dm_new_battle(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    params = request.query_params
    context: dict[str, object] = {
        "colors": ENEMY_COLORS,
        "max_per_color": MAX_ENEMIES_PER_COLOR,
    }
    battle_map = None
    if params.get("carte", "").isdigit():
        battle_map = db.get(BattleMap, int(params["carte"]))
    if battle_map is None:
        context.update(step=1, maps=list_maps(db))
    elif params.get("etape") != "noms":
        context.update(step=2, map=battle_map, counts=enemy_counts(dict(params)))
    else:
        counts = enemy_counts(dict(params))
        context.update(
            step=3,
            map=battle_map,
            counts=counts,
            enemies=default_enemies(counts),
            default_name=default_battle_name(db),
        )
    return render(request, "battle/new.html", context)


@dm_router.post("")
def dm_create_battle(
    db: DbSession,
    dm: CurrentDm,
    carte: Annotated[int, Form()],
    nom_combat: Annotated[str, Form(max_length=200)] = "",
    couleur: Annotated[list[str] | None, Form()] = None,
    nom: Annotated[list[str] | None, Form()] = None,
) -> Response:
    battle_map = db.get(BattleMap, carte)
    colors, labels = couleur or [], nom or []
    if battle_map is None or len(colors) != len(labels):
        raise HTTPException(status_code=422)
    try:
        battle = create_battle(db, battle_map, nom_combat, list(zip(colors, labels, strict=True)))
    except BattleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/mj/combat/{battle.id}", status_code=303)


@dm_router.get("/cartes/{map_id}/apercu")
def dm_map_thumbnail(request: Request, map_id: int, db: DbSession, dm: CurrentDm) -> Response:
    battle_map = db.get(BattleMap, map_id)
    if battle_map is None:
        raise HTTPException(status_code=404)
    if battle_map.thumbnail is None:
        return _image_response(
            request, battle_map.image, battle_map.image_type, f'"{battle_map.image_sha256}"'
        )
    return _image_response(
        request, battle_map.thumbnail, "image/webp", f'"{battle_map.image_sha256}-t"'
    )


@dm_router.get("/{battle_id}")
def dm_battle_page(request: Request, battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    return _board(request, db, _battle(db, battle_id))


@dm_router.get("/{battle_id}/carte")
def dm_battle_map(request: Request, battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    battle_map = _battle(db, battle_id).map
    if battle_map is None:
        raise HTTPException(status_code=404)
    return _image_response(
        request, battle_map.image, battle_map.image_type, f'"{battle_map.image_sha256}"'
    )


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
    battle = _battle(db, battle_id)
    for token in battle.tokens:
        token.col = None
        token.row = None
        token.facing = None
    db.commit()
    return RedirectResponse(f"/mj/combat/{battle_id}", status_code=303)


@dm_router.post("/{battle_id}/terminer")
def dm_end_battle(battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    end_battle(_battle(db, battle_id))
    db.commit()
    return RedirectResponse(f"/mj/combat/{battle_id}", status_code=303)


@dm_router.post("/{battle_id}/reprendre")
def dm_resume_battle(battle_id: int, db: DbSession, dm: CurrentDm) -> Response:
    resume_battle(_battle(db, battle_id))
    db.commit()
    return RedirectResponse(f"/mj/combat/{battle_id}", status_code=303)


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
    if token is None or window is None or window.key != fenetre or token.battle.map is None:
        raise HTTPException(status_code=404)
    etag = f'"{token.battle.map.image_sha256}-{window.key}"'
    if request.headers.get("if-none-match") == etag:
        return _image_response(request, b"", "image/webp", etag)
    return _image_response(request, crop_image(token.battle, window), "image/webp", etag)
