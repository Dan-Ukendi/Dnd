from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.deps import CurrentPlayer, DbSession
from app.services.access import (
    accessible_article_ids,
    get_player_article,
    player_render_context,
    player_shared_articles,
)
from app.services.articles import articles_by_ids
from app.services.characters import characters_of
from app.templating import render
from app.worldanvil.bbcode import render_bbcode

router = APIRouter(prefix="/joueur")


@router.get("")
def my_character(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    accessible = accessible_article_ids(db, player)
    render_context = player_render_context(db, player)
    sheets = []
    for character in characters_of(db, player):
        article = character.main_article
        content = None
        if article is not None and article.wa_id in accessible:
            content = render_bbcode(article.content, render_context)
        sheets.append({"character": character, "content_html": content})
    return render(
        request,
        "player/home.html",
        {"sheets": sheets, "shared_count": len(player_shared_articles(db, player))},
    )


@router.get("/articles")
def shared_articles(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    return render(request, "player/articles.html", {"articles": player_shared_articles(db, player)})


@router.get("/articles/{wa_id}")
def shared_article(request: Request, wa_id: str, db: DbSession, player: CurrentPlayer) -> Response:
    article = get_player_article(db, player, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    accessible = accessible_article_ids(db, player)
    linked = [
        item for item in articles_by_ids(db, article.linked_ids or []) if item.wa_id in accessible
    ]
    return render(
        request,
        "player/article.html",
        {
            "article": article,
            "content_html": render_bbcode(article.content, player_render_context(db, player)),
            "linked": linked,
        },
    )
