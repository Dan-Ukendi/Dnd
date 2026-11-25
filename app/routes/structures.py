from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import WaArticle
from app.services.structures import MONDE, Viewer, build_structure
from app.templating import is_htmx, render
from app.worldanvil.bbcode import RenderContext


def territory_response(
    request: Request,
    db: Session,
    article: WaArticle,
    viewer: Viewer,
    context: RenderContext,
    focus_key: str,
    *,
    player_mode: bool,
) -> Response:
    structure = build_structure(db, article, viewer, context, focus_key)
    if (
        structure is None
        or structure.mode != MONDE
        or not structure.show_tabs
        or not structure.territory.has_root
        or (focus_key and structure.territory.focus.key != focus_key)
    ):
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        query = f"?lieu={focus_key}" if focus_key else ""
        return RedirectResponse(f"{structure.page_url}{query}#territoire", status_code=303)
    return render(
        request,
        "components/territory.html",
        {"structure": structure, "player_mode": player_mode},
    )
