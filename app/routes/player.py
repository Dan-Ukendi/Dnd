from collections.abc import Callable
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.deps import CurrentPlayer, DbSession, log_in
from app.models import Character, WaArticle
from app.routes.lore import LORE_SECTION, artefact_fiche, lore_page_context
from app.routes.personnage import PERSON_SECTION, player_page_context
from app.routes.structures import territory_response
from app.sections import ADVENTURE_KEY
from app.services.access import (
    accessible_article_ids,
    get_player_article,
    player_render_context,
    player_shared_articles,
)
from app.services.accounts import AccountError, change_password
from app.services.articles import articles_by_ids
from app.services.characters import characters_of
from app.services.dossiers import (
    build_dossier,
    find_journal_entry,
    journal_entry_view,
    journal_shelves,
)
from app.services.media import player_section_banner
from app.services.person_tags import player_badge_index
from app.services.structures import Viewer, build_structure
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
        context = replace(render_context, current=article.wa_id if article else None)
        content = None
        if article is not None and article.wa_id in accessible:
            content = render_bbcode(article.content, context)
        sheets.append(
            {
                "character": character,
                "content_html": content,
                "dossier": build_dossier(character, context, article_href),
            }
        )
    return render(
        request,
        "player/home.html",
        {
            "sheets": sheets,
            "shared_count": len(player_shared_articles(db, player)),
            "person_badges": player_badge_index(db, player),
        },
    )


@router.get("/articles")
def shared_articles(
    request: Request,
    db: DbSession,
    player: CurrentPlayer,
    section: Annotated[str, Query(max_length=30)] = "",
    tag: Annotated[str, Query(max_length=20)] = "",
    pj: Annotated[str, Query(max_length=12)] = "",
) -> Response:
    banner = player_section_banner(db, player, section)
    if section == PERSON_SECTION:
        return render(
            request,
            "personnage/page.html",
            {
                "banner": banner,
                "person_badges": player_badge_index(db, player),
                **player_page_context(db, player, tag, pj),
            },
        )
    if section == LORE_SECTION:
        return render(
            request,
            "lore/page.html",
            {"banner": banner, **lore_page_context(request, db, dm=False, player=player)},
        )
    journals = (
        journal_shelves(characters_of(db, player), player_render_context(db, player))
        if section == ADVENTURE_KEY
        else []
    )
    return render(
        request,
        "player/articles.html",
        {
            "articles": player_shared_articles(db, player),
            "banner": banner,
            "journals": journals,
            "person_badges": player_badge_index(db, player),
        },
    )


@router.get("/journal/{character_id}/{episode}")
def journal_entry(
    request: Request, character_id: int, episode: int, db: DbSession, player: CurrentPlayer
) -> Response:
    character = db.get(Character, character_id)
    if character is None or character.player_id != player.id:
        raise HTTPException(status_code=404)
    entry = find_journal_entry(character, episode)
    if entry is None:
        raise HTTPException(status_code=404)
    accessible = accessible_article_ids(db, player)
    main = character.main_article
    return render(
        request,
        "journal/entry.html",
        {
            "character": character,
            "entry": journal_entry_view(entry, player_render_context(db, player)),
            "episodes": [item.episode for item in character.journal],
            "base_href": f"/joueur/journal/{character.id}",
            "crumbs": [
                ("Mon personnage", "/joueur"),
                ("Aventure", f"/joueur/articles?section={ADVENTURE_KEY}"),
            ],
            "back_href": f"/joueur/articles?section={ADVENTURE_KEY}",
            "fiche_href": f"/joueur/articles/{main.wa_id}"
            if main is not None and main.wa_id in accessible
            else None,
        },
    )


@router.get("/articles/{wa_id}")
def shared_article(
    request: Request,
    wa_id: str,
    db: DbSession,
    player: CurrentPlayer,
    lieu: Annotated[str, Query(max_length=80)] = "",
) -> Response:
    article = get_player_article(db, player, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    accessible = accessible_article_ids(db, player)
    linked = [
        item for item in articles_by_ids(db, article.linked_ids or []) if item.wa_id in accessible
    ]
    render_context = player_render_context(db, player, current=wa_id)
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
            "artefact_fiche": artefact_fiche(db, article, render_context, dm=False, player=player),
            "person_badges": player_badge_index(db, player),
            "structure": build_structure(
                db,
                article,
                Viewer(dm=False, accessible=frozenset(accessible)),
                render_context,
                lieu,
            ),
        },
    )


@router.get("/articles/{wa_id}/territoire")
def shared_article_territory(
    request: Request,
    wa_id: str,
    db: DbSession,
    player: CurrentPlayer,
    lieu: Annotated[str, Query(max_length=80)] = "",
) -> Response:
    article = get_player_article(db, player, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    viewer = Viewer(dm=False, accessible=frozenset(accessible_article_ids(db, player)))
    return territory_response(
        request,
        db,
        article,
        viewer,
        player_render_context(db, player, current=wa_id),
        lieu,
        player_mode=True,
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
