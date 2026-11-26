from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, DbSession
from app.models import WaArticle
from app.services import world as service
from app.templating import flash, is_htmx, render

dm_router = APIRouter(prefix="/mj")

WORLD_SECTION = service.WORLD_KEY
RESULTS_TARGET = "monde-results"
CATEGORY_ERRORS = {
    "article": "Seules les fiches des sections Monde et Organisation ont une catégorie Monde.",
    "category": "Choisissez Pays, Ville, Bâtiment ou aucune catégorie.",
    "building": "Seules les fiches de la catégorie Bâtiments ont un type de bâtiment.",
    "building_type": "Choisissez Branche d'une organisation, Habitation ou le type automatique.",
}


def page_context(
    articles: list[WaArticle], base: str, slug: str, query: str, *, dm: bool
) -> dict[str, Any]:
    if slug and slug not in service.CATEGORY_BY_SLUG:
        raise HTTPException(status_code=404)
    category = service.CATEGORY_BY_SLUG.get(slug)
    tiles = service.tiles(articles, base, player_mode=not dm)
    context: dict[str, Any] = {
        "dm": dm,
        "base": base,
        "tiles": tiles,
        "current": next((tile for tile in tiles if tile.key == category), None),
        "q": query,
        "section_href": f"{base}/articles?section={WORLD_SECTION}",
    }
    if category is not None:
        members = service.categorised(articles, category)
        context["entries"] = [
            article for article in members if service.matches_name(article, query)
        ]
        context["category_total"] = len(members)
        context["slug"] = service.SLUGS[category]
    return context


def render_page(request: Request, context: dict[str, Any]) -> Response:
    if is_htmx(request) and request.headers.get("hx-target") == RESULTS_TARGET:
        return render(request, "monde/_results.html", context)
    return render(request, "monde/page.html", context)


@dm_router.post("/articles/{wa_id}/categorie-monde")
def update_category(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    category: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    try:
        service.set_category(db, article, category)
    except service.WorldCategoryError as exc:
        db.rollback()
        flash(request, CATEGORY_ERRORS.get(str(exc), "Catégorie non enregistrée."), "error")
    else:
        label = service.category_label(category) or "aucune catégorie"
        flash(request, f"Catégorie Monde enregistrée : {label}.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@dm_router.post("/articles/{wa_id}/type-batiment")
def update_building_type(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    building_type: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    try:
        service.set_building_type(db, article, building_type)
    except service.WorldCategoryError as exc:
        db.rollback()
        flash(request, CATEGORY_ERRORS.get(str(exc), "Type de bâtiment non enregistré."), "error")
    else:
        label = service.building_type_label(building_type) or "automatique"
        flash(request, f"Type de bâtiment enregistré : {label}.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)
