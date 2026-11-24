from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.deps import CurrentPlayer, DbSession, log_in
from app.models import WaArticle
from app.services.access import (
    accessible_article_ids,
    get_player_article,
    player_render_context,
    player_shared_articles,
)
from app.services.accounts import AccountError, change_password
from app.services.articles import articles_by_ids
from app.services.characters import characters_of
from app.services.dossiers import build_dossier
from app.services.media import player_section_banner
from app.templating import flash, render
from app.worldanvil.bbcode import render_bbcode

router = APIRouter(prefix="/joueur")


@router.get("")
def my_character(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    accessible = accessible_article_ids(db, player)
    render_context = player_render_context(db, player)
    article_href = _player_article_href(accessible)
    sheets = []
    for character in characters_of(db, player):
        article = character.main_article
        content = None
        if article is not None and article.wa_id in accessible:
            content = render_bbcode(article.content, render_context)
        sheets.append(
            {
                "character": character,
                "content_html": content,
                "dossier": build_dossier(character, render_context, article_href),
            }
        )
    return render(
        request,
        "player/home.html",
        {"sheets": sheets, "shared_count": len(player_shared_articles(db, player))},
    )


@router.get("/articles")
def shared_articles(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    banner = player_section_banner(db, player, request.query_params.get("section", ""))
    return render(
        request,
        "player/articles.html",
        {"articles": player_shared_articles(db, player), "banner": banner},
    )


@router.get("/articles/{wa_id}")
def shared_article(request: Request, wa_id: str, db: DbSession, player: CurrentPlayer) -> Response:
    article = get_player_article(db, player, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    accessible = accessible_article_ids(db, player)
    linked = [
        item for item in articles_by_ids(db, article.linked_ids or []) if item.wa_id in accessible
    ]
    render_context = player_render_context(db, player)
    own = next((item for item in characters_of(db, player) if item.main_article_id == wa_id), None)
    dossier = (
        build_dossier(own, render_context, _player_article_href(accessible))
        if own is not None
        else None
    )
    return render(
        request,
        "player/article.html",
        {
            "article": article,
            "content_html": render_bbcode(article.content, render_context),
            "linked": linked,
            "dossier": dossier,
        },
    )


def _player_article_href(accessible: set[str]) -> Callable[[WaArticle], str | None]:
    def href(article: WaArticle) -> str | None:
        return f"/joueur/articles/{article.wa_id}" if article.wa_id in accessible else None

    return href


def _require_password_change_enabled() -> None:
    if not get_settings().player_password_change:
        raise HTTPException(status_code=404)


PASSWORD_ERROR_MESSAGES = {
    "wrong_password": "Le mot de passe actuel est incorrect.",
    "password_too_short": "Le nouveau mot de passe doit contenir au moins 10 caractères.",
    "password_too_long": "Le nouveau mot de passe est trop long.",
    "password_mismatch": "Les deux nouveaux mots de passe ne correspondent pas.",
    "password_unchanged": "Choisissez un mot de passe différent de l'actuel.",
}


@router.get("/mot-de-passe")
def password_form(request: Request, player: CurrentPlayer) -> Response:
    _require_password_change_enabled()
    return render(request, "player/password.html", {"forced": player.must_change_password})


@router.post("/mot-de-passe")
def password_submit(
    request: Request,
    db: DbSession,
    player: CurrentPlayer,
    current_password: Annotated[str, Form(max_length=512)] = "",
    new_password: Annotated[str, Form(max_length=512)] = "",
    new_password_confirmation: Annotated[str, Form(max_length=512)] = "",
) -> Response:
    _require_password_change_enabled()
    forced = player.must_change_password
    try:
        change_password(db, player, current_password, new_password, new_password_confirmation)
    except AccountError as exc:
        return render(
            request,
            "player/password.html",
            {
                "forced": forced,
                "error": PASSWORD_ERROR_MESSAGES.get(exc.code, "Modification impossible."),
            },
            status_code=400,
        )
    log_in(request, player)
    flash(request, "Votre mot de passe est enregistré.")
    return RedirectResponse("/joueur", status_code=303)
