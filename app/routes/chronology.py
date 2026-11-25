from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup

from app.chronology import EventVisibility
from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import TimelineEvent, User
from app.services import chronology as service
from app.services.access import accessible_article_ids, dm_render_context, player_render_context
from app.services.articles import all_available_articles
from app.templating import flash, is_htmx, render
from app.worldanvil.bbcode import render_plain_text

dm_router = APIRouter(prefix="/mj/chronologie")
player_router = APIRouter(prefix="/joueur/chronologie")

DM_BASE = "/mj/chronologie"
PLAYER_BASE = "/joueur/chronologie"
PANEL_TARGET = "chrono-panel"

ERROR_MESSAGES = {
    "invalid_visibility": "Visibilité inconnue.",
    "invalid_day": "Le jour doit être J1, J2 ou rester vide.",
    "invalid_session": "La séance doit être un nombre entier positif.",
    "invalid_order": "L'ordre doit être un nombre entier.",
    "invalid_link": "Choisissez un autre événement de la chronologie.",
    "duplicate_link": "Ces deux événements sont déjà liés.",
    "unknown_link": "Ce lien n'existe pas ou provient de World Anvil.",
    "invalid_fiche_article": "Cette fiche n'existe pas ou a été supprimée.",
    "invalid_fiche_title": "Choisissez une fiche ou saisissez un titre (300 caractères au plus).",
    "duplicate_fiche": "Cette fiche est déjà liée à l'événement.",
    "unknown_fiche": "Cette fiche n'est pas liée à l'événement.",
    "invalid_era_name": "Le nom de l'ère est obligatoire (100 caractères au plus).",
    "invalid_era_subtitle": "Le sous-titre est trop long (300 caractères au plus).",
    "invalid_era_color": "La couleur doit être au format #RRGGBB.",
}


def _int_param(request: Request, key: str) -> int | None:
    value = request.query_params.get(key, "")
    if value.lstrip("-").isdigit() and len(value) <= 7:
        return int(value)
    return None


def _autolink(db: DbSession, user: User, *, dm: bool) -> Callable[..., Markup]:
    context = dm_render_context(db) if dm else player_render_context(db, user)

    def autolink(text: str | None, *, paragraphs: bool = False) -> Markup:
        return render_plain_text(text, context, paragraphs=paragraphs)

    return autolink


def _detail_context(
    db: DbSession, event: TimelineEvent, eras: list[service.EraView], user: User, *, dm: bool
) -> dict[str, Any]:
    view = service.to_views([event], eras)[0]
    accessible = None if dm else accessible_article_ids(db, user)
    context: dict[str, Any] = {
        "detail": view,
        "linked": service.linked_events(db, event, eras, dm=dm),
        "fiches": service.event_fiches(db, event, dm=dm, accessible=accessible),
        "edit_url": service.history_edit_url(event) if dm else None,
    }
    if dm:
        context["link_options"] = [
            option for option in service.event_options(db, eras) if option.id != event.id
        ]
        context["fiche_options"] = all_available_articles(db)
    return context


def _page(request: Request, db: DbSession, user: User, *, dm: bool) -> Response:
    base = DM_BASE if dm else PLAYER_BASE
    eras = service.era_views(db)
    filters = service.Filters.parse(request.query_params, dm=dm)
    views = service.to_views(service.visible_events(db, dm=dm), eras)
    shown = [view for view in views if filters.matches(view.event, dm=dm)]
    context: dict[str, Any] = {
        "dm": dm,
        "base": base,
        "eras": eras,
        "filters": filters,
        "tab": "chronique" if request.query_params.get("vue") == "chronique" else "histoire",
        "total": len(views),
        "shown": shown,
        "groups": service.group_by_era(shown, eras),
        "frise": service.build_frise(views, shown, eras),
        "sessions": service.chronicle_sessions(views),
        "current_url": str(request.url.path)
        + (f"?{request.url.query}" if request.url.query else ""),
        "autolink": _autolink(db, user, dm=dm),
    }
    event_id = _int_param(request, "evenement")
    year = _int_param(request, "annee")
    if event_id is not None:
        event = service.get_visible_event(db, event_id, dm=dm)
        if event is not None:
            context.update(_detail_context(db, event, eras, user, dm=dm))
    elif year is not None:
        context["year"] = year
        context["year_events"] = [view for view in shown if view.event.start_year == year]
    if is_htmx(request) and request.headers.get("hx-target") == PANEL_TARGET:
        return render(request, "chronology/_panel.html", context)
    return render(request, "chronology/page.html", context)


def _event_page(
    request: Request, db: DbSession, user: User, event_id: int, *, dm: bool
) -> Response:
    event = service.get_visible_event(db, event_id, dm=dm)
    if event is None:
        raise HTTPException(status_code=404)
    eras = service.era_views(db)
    context = {
        "dm": dm,
        "base": DM_BASE if dm else PLAYER_BASE,
        "current_url": request.url.path,
        "autolink": _autolink(db, user, dm=dm),
        **_detail_context(db, event, eras, user, dm=dm),
    }
    return render(request, "chronology/event.html", context)


@dm_router.get("")
def dm_chronology(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return _page(request, db, dm, dm=True)


@dm_router.get("/evenements/{event_id}")
def dm_event(request: Request, event_id: int, db: DbSession, dm: CurrentDm) -> Response:
    return _event_page(request, db, dm, event_id, dm=True)


@player_router.get("")
def player_chronology(request: Request, db: DbSession, player: CurrentPlayer) -> Response:
    return _page(request, db, player, dm=False)


@player_router.get("/evenements/{event_id}")
def player_event(request: Request, event_id: int, db: DbSession, player: CurrentPlayer) -> Response:
    return _event_page(request, db, player, event_id, dm=False)


def _dm_event(db: DbSession, event_id: int) -> TimelineEvent:
    event = service.get_visible_event(db, event_id, dm=True)
    if event is None:
        raise HTTPException(status_code=404)
    return event


def _back(target: str, event: TimelineEvent | None = None) -> RedirectResponse:
    safe = (
        target.startswith(DM_BASE)
        and "//" not in target
        and "\\" not in target
        and len(target) <= 1000
    )
    if not safe:
        target = f"{DM_BASE}/evenements/{event.id}" if event is not None else DM_BASE
    return RedirectResponse(target, status_code=303)


def _fail(request: Request, db: DbSession, exc: service.ChronologyError) -> None:
    db.rollback()
    flash(request, ERROR_MESSAGES.get(exc.code, "Modification impossible."), "error")


Back = Annotated[str, Form(max_length=1000)]


@dm_router.post("/evenements/{event_id}/visibilite")
def update_visibility(
    request: Request,
    event_id: int,
    db: DbSession,
    dm: CurrentDm,
    visibility: Annotated[str, Form(max_length=20)] = "",
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        service.set_visibility(db, event, visibility)
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(
            request,
            f"« {event.title} » est révélé aux joueurs."
            if visibility == EventVisibility.PLAYERS
            else f"« {event.title} » est de nouveau réservé au MJ.",
        )
    return _back(retour, event)


@dm_router.post("/evenements/{event_id}/chronique")
def update_chronicle(
    request: Request,
    event_id: int,
    db: DbSession,
    dm: CurrentDm,
    chronicle: Annotated[str, Form(max_length=5)] = "",
    session: Annotated[str, Form(max_length=10)] = "",
    day: Annotated[str, Form(max_length=5)] = "",
    order: Annotated[str, Form(max_length=10)] = "",
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        service.set_chronicle(
            db, event, chronicle=chronicle == "1", session=session, day=day, order=order
        )
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(request, f"Chronique de « {event.title} » enregistrée.")
    return _back(retour, event)


@dm_router.post("/evenements/{event_id}/liens")
def add_link(
    request: Request,
    event_id: int,
    db: DbSession,
    dm: CurrentDm,
    target_id: Annotated[int, Form()] = 0,
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        target = service.add_link(db, event, target_id)
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(request, f"Lien vers « {target.title} » ajouté.")
    return _back(retour, event)


@dm_router.post("/evenements/{event_id}/liens/{target_id}/supprimer")
def remove_link(
    request: Request,
    event_id: int,
    target_id: int,
    db: DbSession,
    dm: CurrentDm,
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        service.remove_link(db, event, target_id)
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(request, "Lien retiré.")
    return _back(retour, event)


@dm_router.post("/evenements/{event_id}/fiches")
def add_fiche(
    request: Request,
    event_id: int,
    db: DbSession,
    dm: CurrentDm,
    article_id: Annotated[str, Form(max_length=36)] = "",
    title: Annotated[str, Form(max_length=600)] = "",
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        fiche = service.add_fiche(db, event, article_id=article_id, title=title)
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(request, f"Fiche « {fiche.title} » liée.")
    return _back(retour, event)


@dm_router.post("/evenements/{event_id}/fiches/{fiche_id}/supprimer")
def remove_fiche(
    request: Request,
    event_id: int,
    fiche_id: int,
    db: DbSession,
    dm: CurrentDm,
    retour: Back = "",
) -> Response:
    event = _dm_event(db, event_id)
    try:
        service.remove_fiche(db, event, fiche_id)
    except service.ChronologyError as exc:
        _fail(request, db, exc)
    else:
        flash(request, "Fiche retirée de l'événement.")
    return _back(retour, event)


@dm_router.get("/eres")
def era_settings(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return render(request, "chronology/eras.html", {"eras": service.era_views(db)})


@dm_router.post("/eres/{slug}")
def update_era(
    request: Request,
    slug: str,
    db: DbSession,
    dm: CurrentDm,
    name: Annotated[str, Form(max_length=300)] = "",
    subtitle: Annotated[str, Form(max_length=600)] = "",
    color: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    try:
        service.update_era(db, slug, name=name, subtitle=subtitle, color=color)
    except service.ChronologyError as exc:
        if exc.code == "unknown_era":
            raise HTTPException(status_code=404) from exc
        _fail(request, db, exc)
    else:
        flash(request, f"Ère « {name.strip()} » enregistrée.")
    return RedirectResponse(f"{DM_BASE}/eres", status_code=303)


@dm_router.post("/eres/{slug}/reinitialiser")
def reset_era(request: Request, slug: str, db: DbSession, dm: CurrentDm) -> Response:
    try:
        service.reset_era(db, slug)
    except service.ChronologyError as exc:
        raise HTTPException(status_code=404) from exc
    flash(request, "Ère rétablie selon la configuration par défaut.")
    return RedirectResponse(f"{DM_BASE}/eres", status_code=303)
