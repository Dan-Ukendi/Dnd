from collections.abc import Callable
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentPlayer, DbSession, is_master_key_session, log_in
from app.models import Character, WaArticle
from app.routes import monde
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
from app.services.accounts import AccountError, change_pin
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
from app.services.world import player_articles, world_fiche
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
    q: Annotated[str, Query(max_length=200)] = "",
    categorie: Annotated[str, Query(max_length=20)] = "",
) -> Response:
    banner = player_section_banner(db, player, section)
    if section == monde.WORLD_SECTION:
        articles = player_articles(db, accessible_article_ids(db, player))
        context = monde.page_context(articles, "/joueur", categorie, q.strip(), dm=False)
        return monde.render_page(request, {**context, "banner": banner})
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
            "world_fiche": world_fiche(
                db, article, Viewer(dm=False, accessible=frozenset(accessible))
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


def _require_own_session(request: Request) -> None:
    if is_master_key_session(request):
        raise HTTPException(status_code=404)


PIN_ERROR_MESSAGES = {
    "wrong_pin": "Le code PIN actuel est incorrect.",
    "pin_invalid": "Le code PIN doit contenir exactement 4 chiffres.",
    "pin_mismatch": "Les deux codes PIN ne correspondent pas.",
    "pin_unchanged": "Choisissez un code différent de l'actuel.",
}


@router.get("/mot-de-passe")
def pin_form(request: Request, player: CurrentPlayer) -> Response:
    _require_own_session(request)
    return render(request, "player/password.html", {"forced": player.must_change_password})


@router.post("/mot-de-passe")
def pin_submit(
    request: Request,
    db: DbSession,
    player: CurrentPlayer,
    current_pin: Annotated[str, Form(max_length=512)] = "",
    new_pin: Annotated[str, Form(max_length=512)] = "",
    new_pin_confirmation: Annotated[str, Form(max_length=512)] = "",
) -> Response:
    _require_own_session(request)
    forced = player.must_change_password
    try:
        change_pin(db, player, None if forced else current_pin, new_pin, new_pin_confirmation)
    except AccountError as exc:
        return render(
            request,
            "player/password.html",
            {
                "forced": forced,
                "error": PIN_ERROR_MESSAGES.get(exc.code, "Modification impossible."),
            },
            status_code=400,
        )
    log_in(request, player)
    flash(request, "Votre code PIN est enregistré.")
    return RedirectResponse("/joueur", status_code=303)
