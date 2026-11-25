from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import LinkAlias, LinkExclusion
from app.services import linking as service
from app.services.access import accessible_article_ids
from app.services.articles import all_available_articles
from app.templating import flash, render

dm_router = APIRouter(prefix="/mj/liens")
player_router = APIRouter(prefix="/joueur/liens")

PAGE = "/mj/liens"
ERROR_MESSAGES = {
    "term": "Le nom doit compter au moins 3 lettres (200 caractères au plus).",
    "article": "Aucune fiche disponible ne porte exactement ce titre.",
    "dossier_alias": "Ce nom est un alias de dossier de personnage : il ne crée jamais de lien.",
    "duplicate": "Ce nom figure déjà dans la liste.",
}
Short = Annotated[str, Form(max_length=600)]


def _preview_response(request: Request, body: bytes, etag: str) -> Response:
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Cookie"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@dm_router.get("")
def links_page(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    matcher = service.matcher(db)
    return render(
        request,
        "dm/links.html",
        {
            "aliases": service.list_aliases(db),
            "exclusions": service.list_exclusions(db),
            "conflicts": service.conflicts(db, matcher),
            "name_count": len(matcher.keys),
            "articles": all_available_articles(db),
        },
    )


def _fail(request: Request, db: DbSession, exc: service.LinkError, what: str) -> None:
    db.rollback()
    message = ERROR_MESSAGES.get(exc.code, "Modification impossible.")
    flash(request, f"{what} non enregistré : {message[:1].lower()}{message[1:]}", "error")


@dm_router.post("/alias")
def add_alias(
    request: Request, db: DbSession, dm: CurrentDm, alias: Short = "", article: Short = ""
) -> Response:
    try:
        row = service.add_alias(db, alias, article)
    except service.LinkError as exc:
        _fail(request, db, exc, "Alias")
    else:
        flash(request, f"Alias « {row.alias} » ajouté.")
    return RedirectResponse(f"{PAGE}#alias", status_code=303)


@dm_router.post("/alias/{alias_id}/supprimer")
def delete_alias(request: Request, alias_id: int, db: DbSession, dm: CurrentDm) -> Response:
    row = db.get(LinkAlias, alias_id)
    if row is None:
        raise HTTPException(status_code=404)
    name = row.alias
    service.delete_alias(db, row)
    flash(request, f"Alias « {name} » supprimé.")
    return RedirectResponse(f"{PAGE}#alias", status_code=303)


@dm_router.post("/exclusions")
def add_exclusion(request: Request, db: DbSession, dm: CurrentDm, term: Short = "") -> Response:
    try:
        row = service.add_exclusion(db, term)
    except service.LinkError as exc:
        _fail(request, db, exc, "Nom exclu")
    else:
        flash(request, f"« {row.term} » ne crée plus de lien automatique.")
    return RedirectResponse(f"{PAGE}#exclusions", status_code=303)


@dm_router.post("/exclusions/{exclusion_id}/supprimer")
def delete_exclusion(request: Request, exclusion_id: int, db: DbSession, dm: CurrentDm) -> Response:
    row = db.get(LinkExclusion, exclusion_id)
    if row is None:
        raise HTTPException(status_code=404)
    term = row.term
    service.delete_exclusion(db, row)
    flash(request, f"« {term} » crée de nouveau des liens automatiques.")
    return RedirectResponse(f"{PAGE}#exclusions", status_code=303)


@dm_router.get("/apercus")
def dm_previews(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return _preview_response(request, *service.preview_payload(db, None))


@player_router.get("/apercus")
def player_previews(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    accessible = accessible_article_ids(db, player)
    return _preview_response(request, *service.preview_payload(db, accessible))
