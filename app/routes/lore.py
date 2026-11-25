from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import LORE_ELEMENT_TYPES, Artefact, LoreEntry, User, WaArticle
from app.presentation import in_section
from app.services import lore as service
from app.services.access import (
    accessible_article_ids,
    dm_render_context,
    player_render_context,
    player_shared_articles,
)
from app.services.articles import all_available_articles
from app.services.chronology import EraView, era_views, event_options
from app.templating import flash, render
from app.worldanvil.bbcode import RenderContext, render_bbcode

dm_router = APIRouter(prefix="/mj")
player_router = APIRouter(prefix="/joueur")

LORE_SECTION = "lore"
ERROR_MESSAGES = {
    "title": "Le titre est obligatoire (300 caractères au plus).",
    "element_type": "Choisissez le type d'élément concerné.",
    "text": "Le texte est trop long.",
    "review_note": "La note « À vérifier » est trop longue.",
    "start_year": "L'année de début doit être un nombre entier (négatif avant l'An 0).",
    "end_year": "L'année de fin doit suivre l'année de début.",
    "era": "Ère inconnue.",
    "era_mismatch": "L'année indiquée n'appartient pas à l'ère choisie.",
    "event": "Cet événement de la Chronologie n'existe pas ou a été retiré.",
    "date_conflict": "Une entrée liée à un événement lit sa date sur l'événement : "
    "laissez l'année et l'ère vides.",
    "article": "Cette fiche n'existe pas ou a été supprimée.",
    "sort_order": "L'ordre doit être un nombre entier.",
}
ARTEFACT_ERROR = "Caractéristiques non enregistrées : un champ est trop long."


def _player_scope(db: DbSession, player: User) -> tuple[set[str], set[int]]:
    return accessible_article_ids(db, player), service.players_event_ids(db)


def _artefact_cards(
    db: DbSession, accessible: set[str] | None
) -> list[tuple[WaArticle, Artefact | None]]:
    rows = []
    for article in service.item_articles(db):
        if accessible is not None and article.wa_id not in accessible:
            continue
        rows.append((article, service.get_artefact(db, article.wa_id)))
    return rows


def lore_page_context(
    request: Request, db: DbSession, *, dm: bool, player: User | None = None
) -> dict[str, Any]:
    eras = era_views(db)
    accessible, visible_events = (None, None) if dm or player is None else _player_scope(db, player)
    entries = service.entries_for(db, dm=dm, accessible=accessible, visible_events=visible_events)
    views = service.build_views(
        entries, eras, dm=dm, accessible=accessible, visible_events=visible_events
    )
    filters = service.FriseFilters.parse(request.query_params, dm=dm)
    shown = [view for view in views if filters.matches(view)]
    context: dict[str, Any] = {
        "dm": dm,
        "base": "/mj" if dm else "/joueur",
        "filters": filters,
        "entry_total": len(views),
        "shown": shown,
        "groups": service.frise_groups(shown, eras),
        "undated": service.undated(shown),
        "element_counts": service.element_counts(views),
        "element_labels": service.ELEMENT_LABELS,
        "artefact_cards": _artefact_cards(db, accessible),
    }
    if dm:
        context["review_total"] = sum(1 for view in views if view.entry.review_note.strip())
        context["artefact_reviews"] = sum(
            1 for _, artefact in context["artefact_cards"] if artefact and artefact.review_note
        )
        context["entry_form"] = _form_options(db, eras)
    else:
        shared = in_section(player_shared_articles(db, player), LORE_SECTION) if player else []
        context["other_articles"] = [
            article for article in shared if article.template_type != service.ITEM_TEMPLATE
        ]
    return context


def _form_options(db: DbSession, eras: list[EraView]) -> dict[str, Any]:
    return {
        "element_types": [(key, service.ELEMENT_LABELS[key]) for key in LORE_ELEMENT_TYPES],
        "eras": eras,
        "events": event_options(db, eras),
        "articles": all_available_articles(db),
    }


def _entry_context(
    db: DbSession,
    entry: LoreEntry,
    render_context: RenderContext,
    *,
    dm: bool,
    accessible: set[str] | None = None,
    visible_events: set[int] | None = None,
) -> dict[str, Any]:
    eras = era_views(db)
    view = service.build_views(
        [entry], eras, dm=dm, accessible=accessible, visible_events=visible_events
    )[0]
    context: dict[str, Any] = {
        "dm": dm,
        "base": "/mj" if dm else "/joueur",
        "view": view,
        "text_html": render_bbcode(entry.text, render_context),
    }
    if dm:
        context["entry_form"] = _form_options(db, eras)
    return context


@dm_router.get("/lore/{entry_id}")
def dm_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = service.get_entry(db, entry_id, dm=True)
    if entry is None:
        raise HTTPException(status_code=404)
    return render(
        request, "lore/entry.html", _entry_context(db, entry, dm_render_context(db), dm=True)
    )


@player_router.get("/lore/{entry_id}")
def player_entry(request: Request, entry_id: int, db: DbSession, player: CurrentPlayer) -> Response:
    accessible, visible_events = _player_scope(db, player)
    entry = service.get_entry(
        db, entry_id, dm=False, accessible=accessible, visible_events=visible_events
    )
    if entry is None:
        raise HTTPException(status_code=404)
    context = _entry_context(
        db,
        entry,
        player_render_context(db, player),
        dm=False,
        accessible=accessible,
        visible_events=visible_events,
    )
    return render(request, "lore/entry.html", context)


Short = Annotated[str, Form(max_length=600)]
Long = Annotated[str, Form(max_length=120_000)]
Flag = Annotated[str, Form(max_length=5)]


def _form(
    title: str,
    element_type: str,
    text: str,
    start_year: str,
    end_year: str,
    era: str,
    approximate: str,
    sort_order: str,
    article_id: str,
    event_id: str,
    gm_only: str,
    review_note: str,
) -> service.EntryForm:
    return service.EntryForm(
        title=title,
        element_type=element_type,
        text=text,
        start_year=start_year,
        end_year=end_year,
        era=era,
        approximate=approximate == "1",
        sort_order=sort_order,
        article_id=article_id,
        event_id=event_id,
        gm_only=gm_only == "1",
        review_note=review_note,
    )


def _fail(request: Request, db: DbSession, exc: service.LoreError) -> None:
    db.rollback()
    message = ERROR_MESSAGES.get(exc.code, "Modification impossible.")
    flash(request, f"Entrée non enregistrée : {message[:1].lower()}{message[1:]}", "error")


def _get_dm_entry(db: DbSession, entry_id: int) -> LoreEntry:
    entry = db.get(LoreEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404)
    return entry


@dm_router.post("/lore")
def create_entry(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    element_type: Short = "",
    text: Long = "",
    start_year: Short = "",
    end_year: Short = "",
    era: Short = "",
    approximate: Flag = "",
    sort_order: Short = "",
    article_id: Short = "",
    event_id: Short = "",
    gm_only: Flag = "",
    review_note: Long = "",
) -> Response:
    form = _form(
        title,
        element_type,
        text,
        start_year,
        end_year,
        era,
        approximate,
        sort_order,
        article_id,
        event_id,
        gm_only,
        review_note,
    )
    try:
        entry = service.create_entry(db, form)
    except service.LoreError as exc:
        _fail(request, db, exc)
        return RedirectResponse(f"/mj/articles?section={LORE_SECTION}#frise", status_code=303)
    flash(request, f"Entrée « {entry.title} » ajoutée au Lore.")
    return RedirectResponse(f"/mj/lore/{entry.id}", status_code=303)


@dm_router.post("/lore/{entry_id}")
def update_entry(
    request: Request,
    entry_id: int,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    element_type: Short = "",
    text: Long = "",
    start_year: Short = "",
    end_year: Short = "",
    era: Short = "",
    approximate: Flag = "",
    sort_order: Short = "",
    article_id: Short = "",
    event_id: Short = "",
    gm_only: Flag = "",
    review_note: Long = "",
) -> Response:
    entry = _get_dm_entry(db, entry_id)
    form = _form(
        title,
        element_type,
        text,
        start_year,
        end_year,
        era,
        approximate,
        sort_order,
        article_id,
        event_id,
        gm_only,
        review_note,
    )
    try:
        service.update_entry(db, entry, form)
    except service.LoreError as exc:
        _fail(request, db, exc)
    else:
        flash(request, f"Entrée « {entry.title} » enregistrée.")
    return RedirectResponse(f"/mj/lore/{entry_id}", status_code=303)


@dm_router.post("/lore/{entry_id}/verifie")
def verify_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = _get_dm_entry(db, entry_id)
    service.clear_entry_review(db, entry)
    flash(request, f"Entrée « {entry.title} » marquée vérifiée.")
    return RedirectResponse(f"/mj/lore/{entry_id}", status_code=303)


@dm_router.post("/lore/{entry_id}/supprimer")
def delete_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = _get_dm_entry(db, entry_id)
    title = entry.title
    service.delete_entry(db, entry)
    flash(request, f"Entrée « {title} » supprimée du Lore.")
    return RedirectResponse(f"/mj/articles?section={LORE_SECTION}#frise", status_code=303)


def _get_artefact(db: DbSession, wa_id: str) -> Artefact:
    artefact = service.get_artefact(db, wa_id)
    if artefact is None:
        raise HTTPException(status_code=404)
    return artefact


@dm_router.post("/artefacts/{wa_id}")
def update_artefact(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    tagline: Short = "",
    object_type: Short = "",
    other_names: Short = "",
    rarity: Short = "",
    attunement: Short = "",
    properties: Long = "",
    bonuses: Long = "",
    charges: Short = "",
    recharge: Short = "",
    curse: Long = "",
    holder: Short = "",
    location: Short = "",
    status: Short = "",
    review_note: Long = "",
) -> Response:
    artefact = _get_artefact(db, wa_id)
    values = {
        "tagline": tagline,
        "object_type": object_type,
        "other_names": other_names,
        "rarity": rarity,
        "attunement": attunement,
        "properties": properties,
        "bonuses": bonuses,
        "charges": charges,
        "recharge": recharge,
        "curse": curse,
        "holder": holder,
        "location": location,
        "status": status,
        "review_note": review_note,
    }
    try:
        service.update_artefact(db, artefact, values)
    except service.LoreError:
        db.rollback()
        flash(request, ARTEFACT_ERROR, "error")
    else:
        flash(request, "Caractéristiques enregistrées.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@dm_router.post("/artefacts/{wa_id}/verifie")
def verify_artefact(request: Request, wa_id: str, db: DbSession, dm: CurrentDm) -> Response:
    service.clear_artefact_review(db, _get_artefact(db, wa_id))
    flash(request, "Fiche marquée vérifiée.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@dm_router.post("/artefacts/{wa_id}/creer")
def create_artefact(request: Request, wa_id: str, db: DbSession, dm: CurrentDm) -> Response:
    article = db.get(WaArticle, wa_id)
    if article is None or article.template_type != service.ITEM_TEMPLATE:
        raise HTTPException(status_code=404)
    if service.create_from_article(db, article) is not None:
        db.commit()
        flash(request, "Fiche d'artefact et entrées de Lore créées depuis l'article.")
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


def artefact_fiche(
    db: DbSession,
    article: WaArticle,
    render_context: RenderContext,
    *,
    dm: bool,
    player: User | None = None,
) -> dict[str, Any] | None:
    artefact = service.get_artefact(db, article.wa_id)
    if artefact is None and article.template_type != service.ITEM_TEMPLATE:
        return None
    accessible, visible_events = (None, None) if dm or player is None else _player_scope(db, player)
    entries = service.entries_for(
        db, dm=dm, accessible=accessible, visible_events=visible_events, article_id=article.wa_id
    )
    return {
        "artefact": artefact,
        "properties_html": render_bbcode(artefact.properties, render_context) if artefact else "",
        "bonuses_html": render_bbcode(artefact.bonuses, render_context) if artefact else "",
        "holders": service.holders(db, article.wa_id, None if dm else player),
        "lore": service.build_views(
            entries, era_views(db), dm=dm, accessible=accessible, visible_events=visible_events
        ),
    }
