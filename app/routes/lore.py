from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup

from app.deps import CurrentDm, CurrentPlayer, DbSession
from app.models import Artefact, LoreEntry, LoreGroup, LoreStory, LoreType, User, WaArticle
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
from app.services.lore_sections import TextStatus, article_sections, entry_text, intro_html
from app.templating import flash, render
from app.worldanvil.bbcode import RenderContext, render_bbcode

dm_router = APIRouter(prefix="/mj")
player_router = APIRouter(prefix="/joueur")

LORE_SECTION = "lore"
LORE_HOME = f"/articles?section={LORE_SECTION}"
VIEWS = ("legendes", "artefacts", "ouvrages", "frise", "fiches")
ERROR_MESSAGES = {
    "title": "Le titre est obligatoire (300 caractères au plus).",
    "text": "Le texte est trop long.",
    "review_note": "La note « À vérifier » est trop longue.",
    "start_year": "L'année de début doit être un nombre entier (négatif avant l'An 0).",
    "end_year": "L'année de fin doit suivre l'année de début.",
    "era": "Ère inconnue.",
    "era_mismatch": "L'année indiquée n'appartient pas à l'ère choisie.",
    "event": "Cet événement de la Chronologie n'existe pas ou a été retiré.",
    "date_conflict": "Une partie liée à un événement lit sa date sur l'événement : "
    "laissez l'année et l'ère vides.",
    "article": "Cette fiche n'existe pas ou a été supprimée.",
    "source": "L'article source n'existe pas ou a été supprimé.",
    "source_section": "La section source est obligatoire (300 caractères au plus).",
    "sort_order": "L'ordre doit être un nombre entier.",
    "placement": "Emplacement inconnu.",
    "fiche": "Une section de fiche doit être liée à une fiche d'artefact existante.",
    "story": "Ce récit n'existe pas.",
    "type": "Ce type de légende n'existe pas.",
    "group": "Ce regroupement n'existe pas (titre de 300 caractères au plus).",
    "group_taken": "Un autre regroupement porte déjà ce titre.",
    "artefact": "Cet artefact n'existe pas.",
    "legend": "Choisissez une légende existante.",
    "intro": "La liste des sections d'en-tête est trop longue.",
    "label": "Le nom du type est obligatoire (60 caractères au plus).",
    "label_taken": "Ce type existe déjà.",
    "type_used": "Ce type est encore utilisé par une légende : changez-la d'abord.",
    "part_source": "Choisissez un article source ou écrivez le texte de la partie.",
    "exists": "Cet article est déjà dans le Lore sous ce genre.",
    "genre": "Choisissez Légende, Artefact ou Ouvrage.",
    "kind": "Genre inconnu.",
}
ARTEFACT_ERROR = "Caractéristiques non enregistrées : un champ est trop long."


def _message(exc: service.LoreError) -> str:
    message = ERROR_MESSAGES.get(exc.code, "Modification impossible.")
    return f"{message[:1].lower()}{message[1:]}"


def _fail(request: Request, db: DbSession, exc: service.LoreError, prefix: str) -> None:
    db.rollback()
    flash(request, f"{prefix} : {_message(exc)}", "error")


def _scope(db: DbSession, player: User | None) -> service.Scope:
    if player is None:
        return service.DM_SCOPE
    return service.player_scope(db, accessible_article_ids(db, player))


def _home(scope: service.Scope, view: str = "") -> str:
    suffix = f"&vue={view}" if view else ""
    return f"{scope.base}{LORE_HOME}{suffix}"


def lore_page_context(
    request: Request, db: DbSession, *, dm: bool, player: User | None = None
) -> dict[str, Any]:
    scope = _scope(db, None if dm else player)
    eras = era_views(db)
    view = request.query_params.get("vue", "")
    view = view if view in VIEWS else ""
    legends = service.story_views(db, service.LEGEND, scope, eras)
    books = service.story_views(db, service.BOOK, scope, eras)
    cards = service.artefact_cards(db, scope)
    groups = service.frise_groups(legends, eras)
    context: dict[str, Any] = {
        "dm": dm,
        "base": scope.base,
        "view": view,
        "home_href": _home(scope),
        "view_href": {key: _home(scope, key) for key in VIEWS},
        "legend_total": len(legends),
        "book_total": len(books),
        "artefact_total": len(cards),
        "frise_total": sum(len(group.views) for group in groups),
        "legend_preview": service.LegendFilters().ordered(legends)[:3],
        "book_preview": books[:3],
        "artefact_preview": cards[:4],
    }
    if dm:
        context["reviews"] = service.review_counts(db)
    if view == "legendes":
        context.update(_legend_context(request, db, legends, dm=dm, eras=eras))
    elif view == "artefacts":
        context.update(_artefact_context(request, db, cards, dm=dm))
    elif view == "ouvrages":
        context["books"] = books
        if dm:
            context["story_form"] = _story_options(db, eras)
    elif view == "frise":
        context["groups"] = groups
    elif view == "fiches" and not dm:
        shared = in_section(player_shared_articles(db, player), LORE_SECTION) if player else []
        context["other_articles"] = [
            article for article in shared if article.template_type != service.ITEM_TEMPLATE
        ]
    return context


def _legend_context(
    request: Request,
    db: DbSession,
    legends: list[service.StoryView],
    *,
    dm: bool,
    eras: list[EraView],
) -> dict[str, Any]:
    filters = service.LegendFilters.parse(request.query_params, dm=dm)
    shown = filters.ordered([view for view in legends if filters.matches(view)])
    context: dict[str, Any] = {
        "filters": filters,
        "lore_types": service.lore_types(db),
        "type_counts": service.type_counts(legends),
        "blocks": service.grouped(shown, service.lore_groups(db)),
        "shown_total": len(shown),
    }
    if dm:
        context["story_form"] = _story_options(db, eras)
        context["unplaced"] = service.unplaced_entries(db)
    return context


def _artefact_context(
    request: Request, db: DbSession, cards: list[service.ArtefactCard], *, dm: bool
) -> dict[str, Any]:
    filters = service.ArtefactFilters.parse(request.query_params, dm=dm)
    rarity_counts = {
        key: sum(1 for card in cards if key in card.rarities) for key, _ in service.RARITIES
    }
    types: dict[str, tuple[str, int]] = {}
    for card in cards:
        if card.type_key:
            label, count = types.get(card.type_key, (card.type_label, 0))
            types[card.type_key] = (label, count + 1)
    context: dict[str, Any] = {
        "filters": filters,
        "cards": [card for card in cards if filters.matches(card)],
        "rarities": [(key, label, rarity_counts[key]) for key, label in service.RARITIES],
        "object_types": sorted(types.items(), key=lambda item: item[1][0]),
    }
    if dm:
        curated = {card.article.wa_id for card in cards}
        context["uncurated_items"] = [
            article for article in service.item_articles(db) if article.wa_id not in curated
        ]
    return context


def _story_options(db: DbSession, eras: list[EraView]) -> dict[str, Any]:
    return {
        "lore_types": service.lore_types(db),
        "groups": service.lore_groups(db),
        "eras": eras,
        "articles": all_available_articles(db),
        "artefacts": _artefact_options(db),
        "legends": service.all_stories(db, service.LEGEND),
    }


def _artefact_options(db: DbSession) -> list[tuple[int, str]]:
    return [
        (card.artefact.id, card.article.title)
        for card in service.artefact_cards(db, service.DM_SCOPE)
    ]


def _entry_options(db: DbSession, eras: list[EraView]) -> dict[str, Any]:
    return {
        "eras": eras,
        "events": event_options(db, eras),
        "articles": all_available_articles(db),
        "stories": service.all_stories(db),
        "placements": list(service.PLACEMENT_LABELS.items()),
    }


def _linked_card(story: LoreStory, scope: service.Scope) -> dict[str, Any]:
    return {"story": story, "href": service.story_href(story, scope)}


def _artefact_link(artefact: Artefact | None, scope: service.Scope) -> dict[str, Any] | None:
    if artefact is None or artefact.article.removed_at is not None:
        return None
    if not service.artefact_visible(artefact, scope):
        return None
    return {
        "article": artefact.article,
        "artefact": artefact,
        "href": f"{scope.base}/articles/{artefact.wa_article_id}",
    }


def _part_view(
    entry: LoreEntry,
    index: int,
    story_href: str,
    scope: service.Scope,
    eras: dict[str, EraView],
) -> dict[str, Any]:
    date = service.scoped_entry_date(entry, scope)
    event = entry.event if scope.event_ok(entry.event) else None
    return {
        "entry": entry,
        "index": index,
        "href": f"{story_href}/parties/{index}",
        "date": date,
        "label": service.date_label(date, eras) if date.dated else "",
        "event": event,
        "event_href": f"{scope.base}/chronologie/evenements/{event.id}" if event else None,
    }


def _story_context(
    db: DbSession,
    story: LoreStory,
    scope: service.Scope,
    render_context: RenderContext,
    part_number: int,
) -> dict[str, Any]:
    eras = era_views(db)
    view = service.story_view(db, story, scope, eras)
    by_slug = {era.slug: era for era in eras}
    parts = [
        _part_view(entry, index, view.href, scope, by_slug)
        for index, entry in enumerate(view.parts, start=1)
    ]
    if parts and not 1 <= part_number <= len(parts):
        raise HTTPException(status_code=404)
    current = parts[part_number - 1] if parts else None
    text = entry_text(current["entry"], render_context) if current else None
    legend = story.legend if story.kind == service.BOOK else None
    context: dict[str, Any] = {
        "dm": scope.dm,
        "base": scope.base,
        "view": view,
        "story": story,
        "parts": parts,
        "current": current,
        "text": text,
        "previous": parts[part_number - 2] if current and part_number > 1 else None,
        "next": parts[part_number] if current and part_number < len(parts) else None,
        "description_html": render_bbcode(story.description, render_context)
        if story.description.strip()
        else Markup(""),
        "intro_html": intro_html(story.article, story.intro_sections, render_context)
        if story.intro_sections.strip() and scope.article_ok(story.wa_article_id)
        else Markup(""),
        "artefact_link": _artefact_link(story.artefact, scope),
        "legend_link": _linked_card(legend, scope)
        if legend is not None and service.story_visible(legend, scope)
        else None,
        "book_links": [
            _linked_card(book, scope) for book in service.books_of_legend(db, story, scope)
        ]
        if story.kind == service.LEGEND
        else [],
        "lore_home": _home(scope),
        "list_href": _home(scope, "legendes" if story.kind == service.LEGEND else "ouvrages"),
        "article_href": f"{scope.base}/articles/{story.wa_article_id}"
        if story.wa_article_id
        and story.article
        and story.article.removed_at is None
        and scope.article_ok(story.wa_article_id)
        else None,
    }
    if scope.dm:
        context["story_form"] = _story_options(db, eras)
        context["source_sections"] = (
            article_sections(story.article) if story.article is not None else []
        )
        context["text_status"] = TextStatus
    return context


def _story_page(
    request: Request,
    db: DbSession,
    story_id: int,
    kind: str,
    part: int,
    player: User | None,
) -> Response:
    scope = _scope(db, player)
    story = service.get_story(db, story_id, kind, scope)
    if story is None:
        raise HTTPException(status_code=404)
    render_context = (
        dm_render_context(db, current=story.wa_article_id)
        if player is None
        else player_render_context(db, player, current=story.wa_article_id)
    )
    return render(
        request, "lore/story.html", _story_context(db, story, scope, render_context, part)
    )


@dm_router.get("/lore/recits/{story_id}")
def dm_legend(request: Request, story_id: int, db: DbSession, dm: CurrentDm) -> Response:
    return _story_page(request, db, story_id, service.LEGEND, 1, None)


@dm_router.get("/lore/recits/{story_id}/parties/{part}")
def dm_legend_part(
    request: Request, story_id: int, part: int, db: DbSession, dm: CurrentDm
) -> Response:
    return _story_page(request, db, story_id, service.LEGEND, part, None)


@dm_router.get("/lore/ouvrages/{story_id}")
def dm_book(request: Request, story_id: int, db: DbSession, dm: CurrentDm) -> Response:
    return _story_page(request, db, story_id, service.BOOK, 1, None)


@dm_router.get("/lore/ouvrages/{story_id}/parties/{part}")
def dm_book_part(
    request: Request, story_id: int, part: int, db: DbSession, dm: CurrentDm
) -> Response:
    return _story_page(request, db, story_id, service.BOOK, part, None)


@player_router.get("/lore/recits/{story_id}")
def player_legend(
    request: Request, story_id: int, db: DbSession, player: CurrentPlayer
) -> Response:
    return _story_page(request, db, story_id, service.LEGEND, 1, player)


@player_router.get("/lore/recits/{story_id}/parties/{part}")
def player_legend_part(
    request: Request, story_id: int, part: int, db: DbSession, player: CurrentPlayer
) -> Response:
    return _story_page(request, db, story_id, service.LEGEND, part, player)


@player_router.get("/lore/ouvrages/{story_id}")
def player_book(request: Request, story_id: int, db: DbSession, player: CurrentPlayer) -> Response:
    return _story_page(request, db, story_id, service.BOOK, 1, player)


@player_router.get("/lore/ouvrages/{story_id}/parties/{part}")
def player_book_part(
    request: Request, story_id: int, part: int, db: DbSession, player: CurrentPlayer
) -> Response:
    return _story_page(request, db, story_id, service.BOOK, part, player)


@player_router.get("/lore/{entry_id:int}")
def player_entry(request: Request, entry_id: int, db: DbSession, player: CurrentPlayer) -> Response:
    scope = _scope(db, player)
    entry = db.get(LoreEntry, entry_id)
    if entry is None or entry.story is None or not service.part_visible(entry, scope):
        raise HTTPException(status_code=404)
    parts = service.story_parts(db, entry.story, scope)
    index = parts.index(entry) + 1
    href = service.story_href(entry.story, scope)
    return RedirectResponse(f"{href}/parties/{index}", status_code=303)


@dm_router.get("/lore/{entry_id:int}")
def dm_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = _get_dm_entry(db, entry_id)
    eras = era_views(db)
    date = service.scoped_entry_date(entry, service.DM_SCOPE)
    by_slug = {era.slug: era for era in eras}
    context = {
        "entry": entry,
        "date_label": service.date_label(date, by_slug),
        "text": entry_text(entry, dm_render_context(db, current=entry.source_article_id)),
        "text_status": TextStatus,
        "entry_form": _entry_options(db, eras),
        "placement_labels": service.PLACEMENT_LABELS,
        "back_href": service.story_href(entry.story, service.DM_SCOPE)
        if entry.story is not None
        else (
            f"/mj/articles/{entry.wa_article_id}"
            if entry.placement != "part" and entry.wa_article_id
            else _home(service.DM_SCOPE, "legendes")
        ),
    }
    return render(request, "lore/entry.html", context)


Short = Annotated[str, Form(max_length=600)]
Long = Annotated[str, Form(max_length=120_000)]
Flag = Annotated[str, Form(max_length=5)]


def _get_dm_entry(db: DbSession, entry_id: int) -> LoreEntry:
    entry = db.get(LoreEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404)
    return entry


def _get_dm_story(db: DbSession, story_id: int) -> LoreStory:
    story = db.get(LoreStory, story_id)
    if story is None:
        raise HTTPException(status_code=404)
    return story


def _entry_form(
    title: str,
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
    story_id: str,
    placement: str,
    position: str,
    source_article_id: str,
    source_section: str,
) -> service.EntryForm:
    return service.EntryForm(
        title=title,
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
        story_id=story_id,
        placement=placement or "part",
        position=position,
        source_article_id=source_article_id,
        source_section=source_section,
    )


@dm_router.post("/lore/{entry_id:int}")
def update_entry(
    request: Request,
    entry_id: int,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
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
    story_id: Short = "",
    placement: Short = "",
    position: Short = "",
    source_article_id: Short = "",
    source_section: Short = "",
) -> Response:
    entry = _get_dm_entry(db, entry_id)
    form = _entry_form(
        title,
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
        story_id,
        placement,
        position,
        source_article_id,
        source_section,
    )
    try:
        service.update_entry(db, entry, form)
    except service.LoreError as exc:
        _fail(request, db, exc, "Entrée non enregistrée")
    else:
        flash(request, f"Entrée « {entry.title} » enregistrée.")
    return RedirectResponse(f"/mj/lore/{entry_id}", status_code=303)


@dm_router.post("/lore/{entry_id:int}/verifie")
def verify_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = _get_dm_entry(db, entry_id)
    service.clear_entry_review(db, entry)
    flash(request, f"Entrée « {entry.title} » marquée vérifiée.")
    return RedirectResponse(f"/mj/lore/{entry_id}", status_code=303)


@dm_router.post("/lore/{entry_id:int}/deplacer")
def move_entry(
    request: Request,
    entry_id: int,
    db: DbSession,
    dm: CurrentDm,
    sens: Short = "",
) -> Response:
    entry = _get_dm_entry(db, entry_id)
    service.move_entry(db, entry, -1 if sens == "haut" else 1)
    if entry.story is not None:
        target = f"{service.story_href(entry.story, service.DM_SCOPE)}#parties"
    else:
        target = f"/mj/articles/{entry.wa_article_id}#sections"
    return RedirectResponse(target, status_code=303)


@dm_router.post("/lore/{entry_id:int}/supprimer")
def delete_entry(request: Request, entry_id: int, db: DbSession, dm: CurrentDm) -> Response:
    entry = _get_dm_entry(db, entry_id)
    title = entry.title
    if entry.story is not None:
        target = service.story_href(entry.story, service.DM_SCOPE)
    elif entry.placement != "part" and entry.wa_article_id:
        target = f"/mj/articles/{entry.wa_article_id}"
    else:
        target = _home(service.DM_SCOPE, "legendes")
    service.delete_entry(db, entry)
    flash(request, f"Entrée « {title} » supprimée.")
    return RedirectResponse(target, status_code=303)


def _story_form(
    title: str,
    description: str,
    intro_sections: str,
    type_id: str,
    group_id: str,
    new_group: str,
    position: str,
    start_year: str,
    end_year: str,
    era: str,
    approximate: str,
    article_id: str,
    artefact_id: str,
    legend_id: str,
    gm_only: str,
    review_note: str,
) -> service.StoryForm:
    return service.StoryForm(
        title=title,
        description=description,
        intro_sections=intro_sections,
        type_id=type_id,
        group_id=group_id,
        new_group=new_group,
        position=position,
        start_year=start_year,
        end_year=end_year,
        era=era,
        approximate=approximate == "1",
        article_id=article_id,
        artefact_id=artefact_id,
        legend_id=legend_id,
        gm_only=gm_only == "1",
        review_note=review_note,
    )


@dm_router.post("/lore/recits")
def create_story(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    kind: Short = "",
    title: Short = "",
    description: Long = "",
    intro_sections: Long = "",
    type_id: Short = "",
    group_id: Short = "",
    new_group: Short = "",
    position: Short = "",
    start_year: Short = "",
    end_year: Short = "",
    era: Short = "",
    approximate: Flag = "",
    article_id: Short = "",
    artefact_id: Short = "",
    legend_id: Short = "",
    gm_only: Flag = "",
    review_note: Long = "",
) -> Response:
    form = _story_form(
        title,
        description,
        intro_sections,
        type_id,
        group_id,
        new_group,
        position,
        start_year,
        end_year,
        era,
        approximate,
        article_id,
        artefact_id,
        legend_id,
        gm_only,
        review_note,
    )
    view = "ouvrages" if kind == service.BOOK else "legendes"
    try:
        story = service.create_story(db, kind, form)
    except service.LoreError as exc:
        _fail(request, db, exc, "Récit non créé")
        return RedirectResponse(_home(service.DM_SCOPE, view), status_code=303)
    flash(request, f"« {story.title} » ajouté au Lore.")
    return RedirectResponse(service.story_href(story, service.DM_SCOPE), status_code=303)


@dm_router.post("/lore/recits/{story_id}")
def update_story(
    request: Request,
    story_id: int,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    description: Long = "",
    intro_sections: Long = "",
    type_id: Short = "",
    group_id: Short = "",
    new_group: Short = "",
    position: Short = "",
    start_year: Short = "",
    end_year: Short = "",
    era: Short = "",
    approximate: Flag = "",
    article_id: Short = "",
    artefact_id: Short = "",
    legend_id: Short = "",
    gm_only: Flag = "",
    review_note: Long = "",
) -> Response:
    story = _get_dm_story(db, story_id)
    form = _story_form(
        title,
        description,
        intro_sections,
        type_id,
        group_id,
        new_group,
        position,
        start_year,
        end_year,
        era,
        approximate,
        article_id,
        artefact_id,
        legend_id,
        gm_only,
        review_note,
    )
    try:
        service.update_story(db, story, form)
    except service.LoreError as exc:
        _fail(request, db, exc, "Récit non enregistré")
    else:
        flash(request, f"« {story.title} » enregistré.")
    return RedirectResponse(service.story_href(story, service.DM_SCOPE), status_code=303)


@dm_router.post("/lore/recits/{story_id}/verifie")
def verify_story(request: Request, story_id: int, db: DbSession, dm: CurrentDm) -> Response:
    story = _get_dm_story(db, story_id)
    service.clear_story_review(db, story)
    flash(request, f"« {story.title} » marqué vérifié.")
    return RedirectResponse(service.story_href(story, service.DM_SCOPE), status_code=303)


@dm_router.post("/lore/recits/{story_id}/supprimer")
def delete_story(request: Request, story_id: int, db: DbSession, dm: CurrentDm) -> Response:
    story = _get_dm_story(db, story_id)
    title, kind = story.title, story.kind
    service.delete_story(db, story)
    flash(request, f"« {title} » retiré du Lore.")
    view = "ouvrages" if kind == service.BOOK else "legendes"
    return RedirectResponse(_home(service.DM_SCOPE, view), status_code=303)


@dm_router.post("/lore/recits/{story_id}/parties")
def add_part(
    request: Request,
    story_id: int,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    source_article_id: Short = "",
    source_section: Short = "",
    text: Long = "",
) -> Response:
    story = _get_dm_story(db, story_id)
    try:
        entry = service.add_story_part(db, story, title, source_section, source_article_id, text)
    except service.LoreError as exc:
        _fail(request, db, exc, "Partie non ajoutée")
    else:
        flash(request, f"Partie « {entry.title} » ajoutée (réservée au MJ).")
    return RedirectResponse(
        f"{service.story_href(story, service.DM_SCOPE)}#parties", status_code=303
    )


@dm_router.post("/lore/ajouter/{wa_id}")
def add_to_lore(
    request: Request, wa_id: str, db: DbSession, dm: CurrentDm, genre: Short = ""
) -> Response:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    try:
        result = service.add_to_lore(db, article, genre)
    except service.LoreError as exc:
        _fail(request, db, exc, "Article non ajouté au Lore")
        return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)
    label = service.GENRE_LABELS[result.genre]
    flash(request, f"« {article.title} » ajouté au Lore comme {label.lower()} (réservé au MJ).")
    if result.story is not None:
        return RedirectResponse(service.story_href(result.story, service.DM_SCOPE), status_code=303)
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@dm_router.get("/lore/reglages")
def settings_page(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    stories = service.all_stories(db, service.LEGEND)
    usage: dict[int, int] = {}
    group_usage: dict[int, int] = {}
    for story in stories:
        if story.type_id is not None:
            usage[story.type_id] = usage.get(story.type_id, 0) + 1
        if story.group_id is not None:
            group_usage[story.group_id] = group_usage.get(story.group_id, 0) + 1
    return render(
        request,
        "lore/settings.html",
        {
            "lore_types": service.lore_types(db),
            "groups": service.lore_groups(db),
            "usage": usage,
            "group_usage": group_usage,
            "lore_home": _home(service.DM_SCOPE),
        },
    )


SETTINGS = "/mj/lore/reglages"


@dm_router.post("/lore/types")
def add_type(request: Request, db: DbSession, dm: CurrentDm, label: Short = "") -> Response:
    try:
        lore_type = service.add_type(db, label)
    except service.LoreError as exc:
        _fail(request, db, exc, "Type non ajouté")
    else:
        flash(request, f"Type « {lore_type.label} » ajouté.")
    return RedirectResponse(SETTINGS, status_code=303)


def _get_type(db: DbSession, type_id: int) -> LoreType:
    lore_type = db.get(LoreType, type_id)
    if lore_type is None:
        raise HTTPException(status_code=404)
    return lore_type


@dm_router.post("/lore/types/{type_id}")
def update_type(
    request: Request,
    type_id: int,
    db: DbSession,
    dm: CurrentDm,
    label: Short = "",
    position: Short = "",
) -> Response:
    lore_type = _get_type(db, type_id)
    try:
        service.update_type(db, lore_type, label, position)
    except service.LoreError as exc:
        _fail(request, db, exc, "Type non enregistré")
    else:
        flash(request, f"Type « {lore_type.label} » enregistré.")
    return RedirectResponse(SETTINGS, status_code=303)


@dm_router.post("/lore/types/{type_id}/supprimer")
def delete_type(request: Request, type_id: int, db: DbSession, dm: CurrentDm) -> Response:
    lore_type = _get_type(db, type_id)
    label = lore_type.label
    try:
        service.delete_type(db, lore_type)
    except service.LoreError as exc:
        _fail(request, db, exc, "Type non supprimé")
    else:
        flash(request, f"Type « {label} » supprimé.")
    return RedirectResponse(SETTINGS, status_code=303)


def _get_group(db: DbSession, group_id: int) -> LoreGroup:
    group = db.get(LoreGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404)
    return group


@dm_router.post("/lore/regroupements/{group_id}")
def update_group(
    request: Request,
    group_id: int,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    description: Long = "",
    position: Short = "",
) -> Response:
    group = _get_group(db, group_id)
    try:
        service.update_group(db, group, title, description, position)
    except service.LoreError as exc:
        _fail(request, db, exc, "Regroupement non enregistré")
    else:
        flash(request, f"Regroupement « {group.title} » enregistré.")
    return RedirectResponse(SETTINGS, status_code=303)


@dm_router.post("/lore/regroupements/{group_id}/supprimer")
def delete_group(request: Request, group_id: int, db: DbSession, dm: CurrentDm) -> Response:
    group = _get_group(db, group_id)
    title = group.title
    service.delete_group(db, group)
    flash(request, f"Regroupement « {title} » supprimé ; ses récits sont conservés.")
    return RedirectResponse(SETTINGS, status_code=303)


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
    gm_only: Flag = "",
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
        service.update_artefact(db, artefact, values, gm_only == "1")
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


@dm_router.post("/artefacts/{wa_id}/sections")
def add_fiche_section(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    title: Short = "",
    source_section: Short = "",
    placement: Short = "",
) -> Response:
    artefact = _get_artefact(db, wa_id)
    try:
        entry = service.add_fiche_section(db, artefact, title, source_section, placement)
    except service.LoreError as exc:
        _fail(request, db, exc, "Section non ajoutée")
    else:
        flash(request, f"Section « {entry.title} » ajoutée à la fiche (réservée au MJ).")
    return RedirectResponse(f"/mj/articles/{wa_id}#sections", status_code=303)


def _section_views(entries: list[LoreEntry], render_context: RenderContext) -> list[dict[str, Any]]:
    return [{"entry": entry, "text": entry_text(entry, render_context)} for entry in entries]


def artefact_fiche(
    db: DbSession,
    article: WaArticle,
    render_context: RenderContext,
    *,
    dm: bool,
    player: User | None = None,
) -> dict[str, Any] | None:
    scope = _scope(db, None if dm else player)
    artefact = service.get_artefact(db, article.wa_id)
    visible = artefact is not None and service.artefact_visible(artefact, scope)
    if not visible and article.template_type != service.ITEM_TEMPLATE:
        return None
    if artefact is None or not visible:
        return {"artefact": None, "lore_home": _home(scope, "artefacts")}
    sections = service.fiche_sections(db, artefact, scope)
    context: dict[str, Any] = {
        "artefact": artefact,
        "properties_html": render_bbcode(artefact.properties, render_context),
        "bonuses_html": render_bbcode(artefact.bonuses, render_context),
        "holders": service.holders(db, article.wa_id, None if dm else player),
        "physical": _section_views(
            [entry for entry in sections if entry.placement == "physical"], render_context
        ),
        "characteristics": _section_views(
            [entry for entry in sections if entry.placement == "characteristic"], render_context
        ),
        "dm_notes": _section_views(
            [entry for entry in sections if entry.placement == "dm_note"], render_context
        )
        if dm
        else [],
        "legends": [
            _linked_card(story, scope)
            for story in service.linked_stories(db, artefact, service.LEGEND, scope)
        ],
        "books": [
            _linked_card(story, scope)
            for story in service.linked_stories(db, artefact, service.BOOK, scope)
        ],
        "lore_home": _home(scope, "artefacts"),
    }
    if dm:
        context["source_sections"] = article_sections(article)
        context["text_status"] = TextStatus
        context["placement_labels"] = service.PLACEMENT_LABELS
    return context


def lore_add_panel(db: DbSession, article: WaArticle) -> dict[str, Any]:
    links = service.article_lore(db, article.wa_id)
    taken = {story.kind for story in links.stories}
    if links.artefact is not None:
        taken.add(service.ARTEFACT)
    return {
        "stories": [_linked_card(story, service.DM_SCOPE) for story in links.stories],
        "artefact": links.artefact,
        "genres": [
            (genre, service.GENRE_LABELS[genre], genre in taken) for genre in service.GENRES
        ],
        "available": article.removed_at is None,
    }
