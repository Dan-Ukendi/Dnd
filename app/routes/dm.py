from itertools import zip_longest
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentDm, DbSession
from app.models import (
    Character,
    CharacterEvent,
    CharacterRelation,
    MediaAsset,
    MediaRole,
    Role,
    SyncStatus,
    User,
    WaArticle,
)
from app.routes.lore import LORE_SECTION, artefact_fiche, lore_page_context
from app.routes.personnage import PERSON_SECTION, dm_page_context
from app.routes.structures import territory_response
from app.sections import ADVENTURE_KEY, section_counts
from app.services import articles as article_service
from app.services import characters as character_service
from app.services import dossiers as dossier_service
from app.services import media as media_service
from app.services import person_tags as person_service
from app.services.access import dm_render_context
from app.services.accounts import (
    AccountError,
    create_invitation,
    create_user,
    set_active,
    set_temporary_password,
)
from app.services.dossiers import build_dossier
from app.services.media_processing import MAX_UPLOAD_BYTES, MediaError
from app.services.structures import Viewer, build_structure
from app.services.sync import SyncAlreadyRunningError, latest_run, run_sync_job, start_run
from app.templating import flash, is_htmx, render
from app.worldanvil.bbcode import render_bbcode

router = APIRouter(prefix="/mj")

PLAYER_ERROR_MESSAGES = {
    "invalid_display_name": "Le nom affiché est obligatoire (100 caractères maximum).",
    "invalid_login": (
        "L'identifiant doit faire 3 à 50 caractères : lettres minuscules, chiffres, "
        "point, tiret ou tiret bas."
    ),
    "login_taken": "Cet identifiant est déjà utilisé.",
}
MEDIA_ERROR_MESSAGES = {
    "invalid_role": "Type de fichier inconnu.",
    "missing_file": "Choisissez un fichier à envoyer.",
    "empty_file": "Le fichier envoyé est vide.",
    "file_too_large": "Le fichier dépasse la taille maximale de 15 Mo.",
    "image_too_large": "L'image est trop grande (60 mégapixels au maximum).",
    "unsupported_type": "Format non pris en charge : envoyez une image JPEG, PNG ou WebP.",
    "invalid_image": "L'image est illisible ou endommagée.",
    "invalid_svg": "Le fichier SVG est invalide ou mal formé.",
    "invalid_pdf": "Ce fichier n'est pas un PDF valide.",
}
MEDIA_SAVED_MESSAGES = {
    MediaRole.PORTRAIT: "Portrait enregistré.",
    MediaRole.MAP: "Carte enregistrée.",
    MediaRole.SHEET: "Fiche de personnage (PDF) enregistrée.",
}
MEDIA_REMOVED_MESSAGES = {
    MediaRole.PORTRAIT: "Portrait retiré.",
    MediaRole.MAP: "Carte retirée.",
    MediaRole.SHEET: "Fiche de personnage (PDF) retirée.",
}
RELATION_ERROR_MESSAGES = {
    "name": "Le nom est obligatoire (200 caractères au plus).",
    "category": "La catégorie est obligatoire (40 caractères au plus).",
    "role": "Le lien est trop long (200 caractères au plus).",
    "status": "Le statut est trop long (100 caractères au plus).",
    "article": "Aucune fiche World Anvil disponible ne porte exactement ce titre.",
    "text": "La description est trop longue.",
    "gm_notes": "Les notes du MJ sont trop longues.",
}
EVENT_ERROR_MESSAGES = {
    "title": "Le titre est obligatoire (200 caractères au plus).",
    "session": "La séance est trop longue (100 caractères au plus).",
    "year": "L'année doit être un nombre entier, négatif avant l'An 0.",
    "age": "L'âge doit être un nombre entier positif.",
    "place": "Le lieu est trop long (200 caractères au plus).",
    "place_article": "Aucune fiche World Anvil disponible ne porte exactement le titre du lieu.",
    "people": (
        "Chaque personne doit avoir un nom ou une fiche existante "
        "(titre exact, 200 caractères au plus)."
    ),
    "organizations": (
        "Chaque organisation doit avoir un nom ou une fiche existante "
        "(titre exact, 200 caractères au plus)."
    ),
    "text": "Le récit de l'événement est trop long.",
    "consequences": "Les conséquences sont trop longues.",
    "gm_notes": "Les notes du MJ sont trop longues.",
}
CHARACTER_ERROR_MESSAGES = {
    "invalid_name": "Le nom du personnage est obligatoire.",
    "invalid_player": "Choisissez un joueur valide.",
    "invalid_article": "L'article choisi n'existe pas ou a été supprimé.",
}


def _sync_context(request: Request, db: DbSession) -> dict[str, object]:
    run = latest_run(db)
    return {
        "run": run,
        "running": run is not None and run.status == SyncStatus.RUNNING,
        "world_anvil_configured": get_settings().world_anvil_configured,
    }


@router.get("")
def dashboard(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    stats = {
        "articles": db.scalar(
            select(func.count()).select_from(WaArticle).where(WaArticle.removed_at.is_(None))
        ),
        "visible": db.scalar(
            select(func.count())
            .select_from(WaArticle)
            .where(WaArticle.removed_at.is_(None), WaArticle.player_visible.is_(True))
        ),
        "players": db.scalar(
            select(func.count()).select_from(User).where(User.role == Role.PLAYER)
        ),
        "characters": db.scalar(select(func.count()).select_from(Character)),
    }
    return render(
        request,
        "dm/dashboard.html",
        {"stats": stats, "section_counts": section_counts(db), **_sync_context(request, db)},
    )


@router.post("/synchroniser")
def trigger_sync(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    background: BackgroundTasks,
    mode: Annotated[str, Form()] = "incremental",
) -> Response:
    error: str | None = None
    if not get_settings().world_anvil_configured:
        error = "Les clés World Anvil ne sont pas configurées sur le serveur."
    else:
        try:
            run = start_run(db, full=mode == "full", trigger="ui")
            background.add_task(run_sync_job, run.id)
        except SyncAlreadyRunningError:
            error = "Une synchronisation est déjà en cours."
    if is_htmx(request):
        return render(
            request, "dm/_sync_status.html", {**_sync_context(request, db), "error": error}
        )
    if error:
        flash(request, error, "error")
    return RedirectResponse("/mj", status_code=303)


@router.get("/synchronisation")
def sync_status(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return render(request, "dm/_sync_status.html", _sync_context(request, db))


@router.get("/articles")
def article_list(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    q: Annotated[str, Query(max_length=200)] = "",
    type: Annotated[str, Query(max_length=50)] = "",
    section: Annotated[str, Query(max_length=30)] = "",
    tag: Annotated[str, Query(max_length=20)] = "",
    pj: Annotated[str, Query(max_length=12)] = "",
) -> Response:
    articles = article_service.search_articles(db, query=q, template_type=type)
    tag = tag if section == PERSON_SECTION else ""
    if tag in person_service.CATEGORY_LABELS:
        articles = person_service.dm_articles_in(db, articles, tag)
    context = {
        "articles": articles,
        "q": q,
        "selected_type": type,
        "types": article_service.template_type_counts(db),
        "tag": tag,
        "person_badges": person_service.dm_badge_index(db),
    }
    if is_htmx(request) and request.headers.get("hx-target") == "article-results":
        return render(request, "dm/_article_results.html", context)
    banner = media_service.section_banner(db, section)
    if section == PERSON_SECTION:
        return render(
            request,
            "personnage/page.html",
            {**context, "banner": banner, **dm_page_context(db, tag, pj)},
        )
    if section == LORE_SECTION:
        return render(
            request,
            "lore/page.html",
            {**context, "banner": banner, **lore_page_context(request, db, dm=True)},
        )
    journals = (
        dossier_service.journal_shelves(
            list(db.scalars(select(Character).order_by(Character.name)).all()),
            dm_render_context(db),
        )
        if section == ADVENTURE_KEY
        else []
    )
    return render(request, "dm/articles.html", {**context, "banner": banner, "journals": journals})


@router.get("/journal/{character_id}/{episode}")
def journal_entry(
    request: Request, character_id: int, episode: int, db: DbSession, dm: CurrentDm
) -> Response:
    character = db.get(Character, character_id)
    entry = dossier_service.find_journal_entry(character, episode) if character else None
    if character is None or entry is None:
        raise HTTPException(status_code=404)
    main = character.main_article
    return render(
        request,
        "journal/entry.html",
        {
            "character": character,
            "entry": dossier_service.journal_entry_view(entry, dm_render_context(db)),
            "episodes": [item.episode for item in character.journal],
            "base_href": f"/mj/journal/{character.id}",
            "crumbs": [("Accueil", "/mj"), ("Aventure", f"/mj/articles?section={ADVENTURE_KEY}")],
            "back_href": f"/mj/articles?section={ADVENTURE_KEY}",
            "fiche_href": f"/mj/articles/{main.wa_id}" if main is not None else None,
            "player_name": character.player.display_name,
        },
    )


@router.get("/articles/{wa_id}")
def article_detail(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    lieu: Annotated[str, Query(max_length=80)] = "",
) -> Response:
    article = _get_article(db, wa_id)
    render_context = dm_render_context(db)
    extra_fields = [
        (field.label, render_bbcode(field.value, render_context))
        for field in article_service.dm_extra_fields(article.raw)
    ]
    characters = db.scalars(
        select(Character).where(Character.main_article_id == wa_id).order_by(Character.name)
    ).all()
    return render(
        request,
        "dm/article.html",
        {
            "article": article,
            "content_html": render_bbcode(article.content, render_context),
            "extra_fields": extra_fields,
            "secrets": article_service.attached_secrets(article.raw),
            "linked": article_service.articles_by_ids(db, article.linked_ids or []),
            "backlinks": article_service.backlinks(db, wa_id),
            "characters": characters,
            "dossier": build_dossier(characters[0], render_context, _dm_article_href, dm=True)
            if characters
            else None,
            "relation_categories": dossier_service.RELATION_CATEGORIES,
            "relation_articles": article_service.all_available_articles(db) if characters else [],
            "artefact_fiche": artefact_fiche(db, article, render_context, dm=True),
            "person_badges": person_service.dm_badge_index(db),
            "person_tags": person_service.fiche_tags(db, article),
            "structure": build_structure(db, article, Viewer(dm=True), render_context, lieu),
        },
    )


@router.get("/articles/{wa_id}/territoire")
def article_territory(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    lieu: Annotated[str, Query(max_length=80)] = "",
) -> Response:
    article = _get_article(db, wa_id)
    return territory_response(
        request, db, article, Viewer(dm=True), dm_render_context(db), lieu, player_mode=False
    )


def _dm_article_href(article: WaArticle) -> str | None:
    return f"/mj/articles/{article.wa_id}" if article.removed_at is None else None


def _get_article(db: DbSession, wa_id: str) -> WaArticle:
    article = db.get(WaArticle, wa_id)
    if article is None:
        raise HTTPException(status_code=404)
    return article


@router.post("/articles/{wa_id}/medias")
def upload_media(
    request: Request,
    wa_id: str,
    db: DbSession,
    dm: CurrentDm,
    role: Annotated[str, Form(max_length=20)] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    article = _get_article(db, wa_id)
    try:
        media_role = media_service.parse_role(role)
        if file is None or not file.filename:
            raise MediaError("missing_file")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        media_service.attach_media(
            db, article=article, role=media_role, data=data, filename=file.filename
        )
    except MediaError as exc:
        db.rollback()
        flash(request, MEDIA_ERROR_MESSAGES.get(exc.code, "Envoi impossible."), "error")
    else:
        flash(request, MEDIA_SAVED_MESSAGES[media_role])
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@router.post("/articles/{wa_id}/medias/{media_id}/supprimer")
def remove_media(
    request: Request, wa_id: str, media_id: int, db: DbSession, dm: CurrentDm
) -> Response:
    asset = db.get(MediaAsset, media_id)
    if asset is None or asset.wa_article_id != wa_id:
        raise HTTPException(status_code=404)
    role = MediaRole(asset.role)
    media_service.remove_media(db, asset)
    flash(request, MEDIA_REMOVED_MESSAGES[role])
    return RedirectResponse(f"/mj/articles/{wa_id}", status_code=303)


@router.get("/joueurs")
def players_page(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return render(
        request,
        "dm/players.html",
        {"players": character_service.list_players(db), "form": {}},
    )


@router.post("/joueurs")
def create_player(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    display_name: Annotated[str, Form(max_length=200)] = "",
    login: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    try:
        player = create_user(db, display_name=display_name, login=login, role=Role.PLAYER)
    except AccountError as exc:
        return render(
            request,
            "dm/players.html",
            {
                "players": character_service.list_players(db),
                "form": {"display_name": display_name, "login": login},
                "error": PLAYER_ERROR_MESSAGES.get(exc.code, "Impossible de créer le joueur."),
            },
            status_code=400,
        )
    flash(
        request, f"Joueur « {player.display_name} » créé. Générez maintenant son lien d'invitation."
    )
    return RedirectResponse("/mj/joueurs", status_code=303)


def _get_player(db: DbSession, player_id: int) -> User:
    player = db.get(User, player_id)
    if player is None or player.role != Role.PLAYER:
        raise HTTPException(status_code=404)
    return player


@router.post("/joueurs/{player_id}/invitation")
def invite_player(request: Request, player_id: int, db: DbSession, dm: CurrentDm) -> Response:
    player = _get_player(db, player_id)
    if not player.is_active:
        raise HTTPException(status_code=400)
    token = create_invitation(db, player, get_settings().invitation_ttl_hours)
    invitation_url = str(request.url_for("invitation_form", token=token))
    context = {
        "player": player,
        "invitation_url": invitation_url,
        "ttl_hours": get_settings().invitation_ttl_hours,
    }
    if is_htmx(request):
        return render(request, "dm/_invitation_link.html", context)
    return render(
        request,
        "dm/players.html",
        {"players": character_service.list_players(db), "form": {}, "invitation": context},
    )


@router.post("/joueurs/{player_id}/mot-de-passe-provisoire")
def reset_player_password(
    request: Request, player_id: int, db: DbSession, dm: CurrentDm
) -> Response:
    player = _get_player(db, player_id)
    if not player.is_active:
        raise HTTPException(status_code=400)
    set_temporary_password(db, player, get_settings().player_initial_password.get_secret_value())
    flash(
        request,
        f"{player.display_name} se connecte désormais avec le mot de passe provisoire "
        "et devra en choisir un nouveau.",
    )
    return RedirectResponse("/mj/joueurs", status_code=303)


@router.post("/joueurs/{player_id}/desactiver")
def deactivate_player(request: Request, player_id: int, db: DbSession, dm: CurrentDm) -> Response:
    player = _get_player(db, player_id)
    set_active(db, player, False)
    flash(request, f"Le compte de {player.display_name} est désactivé.")
    return RedirectResponse("/mj/joueurs", status_code=303)


@router.post("/joueurs/{player_id}/reactiver")
def reactivate_player(request: Request, player_id: int, db: DbSession, dm: CurrentDm) -> Response:
    player = _get_player(db, player_id)
    set_active(db, player, True)
    flash(request, f"Le compte de {player.display_name} est réactivé.")
    return RedirectResponse("/mj/joueurs", status_code=303)


@router.get("/personnages")
def characters_page(request: Request, db: DbSession, dm: CurrentDm) -> Response:
    return render(
        request,
        "dm/characters.html",
        {
            "characters": character_service.list_characters(db),
            "players": character_service.list_players(db),
            "articles": article_service.all_available_articles(db),
            "form": {},
        },
    )


@router.post("/personnages")
def create_character(
    request: Request,
    db: DbSession,
    dm: CurrentDm,
    name: Annotated[str, Form(max_length=300)] = "",
    player_id: Annotated[int, Form()] = 0,
    main_article_id: Annotated[str, Form(max_length=36)] = "",
) -> Response:
    try:
        character = character_service.create_character(
            db, name=name, player_id=player_id, main_article_id=main_article_id or None
        )
    except character_service.CharacterError as exc:
        return render(
            request,
            "dm/characters.html",
            {
                "characters": character_service.list_characters(db),
                "players": character_service.list_players(db),
                "articles": article_service.all_available_articles(db),
                "form": {"name": name, "player_id": player_id, "main_article_id": main_article_id},
                "error": CHARACTER_ERROR_MESSAGES.get(exc.code, "Création impossible."),
            },
            status_code=400,
        )
    flash(request, f"Personnage « {character.name} » créé.")
    return RedirectResponse(f"/mj/personnages/{character.id}", status_code=303)


def _get_character(db: DbSession, character_id: int) -> Character:
    character = db.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404)
    return character


def _candidate_context(
    db: DbSession, character: Character, shared_ids: set[str], q: str, template_type: str
) -> dict[str, object]:
    return {
        "character": character,
        "candidates": article_service.search_articles(db, query=q, template_type=template_type),
        "shared_ids": shared_ids,
        "q": q,
        "selected_type": template_type,
    }


@router.get("/personnages/{character_id}")
def character_page(
    request: Request,
    character_id: int,
    db: DbSession,
    dm: CurrentDm,
    q: Annotated[str, Query(max_length=200)] = "",
    type: Annotated[str, Query(max_length=50)] = "",
) -> Response:
    character = _get_character(db, character_id)
    shared_ids = character_service.extra_article_ids(db, character)
    context = _candidate_context(db, character, shared_ids, q, type)
    if is_htmx(request) and request.headers.get("hx-target") == "candidate-results":
        return render(request, "dm/_character_candidates.html", context)
    return render(
        request,
        "dm/character.html",
        {
            **context,
            "players": character_service.list_players(db),
            "articles": article_service.all_available_articles(db),
            "types": article_service.template_type_counts(db),
            "shared_articles": article_service.articles_by_ids(db, shared_ids),
        },
    )


@router.post("/personnages/{character_id}")
def update_character(
    request: Request,
    character_id: int,
    db: DbSession,
    dm: CurrentDm,
    name: Annotated[str, Form(max_length=300)] = "",
    player_id: Annotated[int, Form()] = 0,
    main_article_id: Annotated[str, Form(max_length=36)] = "",
) -> Response:
    character = _get_character(db, character_id)
    try:
        character_service.update_character(
            db,
            character,
            name=name,
            player_id=player_id,
            main_article_id=main_article_id or None,
        )
    except character_service.CharacterError as exc:
        db.rollback()
        flash(request, CHARACTER_ERROR_MESSAGES.get(exc.code, "Modification impossible."), "error")
    else:
        flash(request, "Personnage enregistré.")
    return RedirectResponse(f"/mj/personnages/{character_id}", status_code=303)


@router.post("/personnages/{character_id}/articles")
def toggle_character_article(
    request: Request,
    character_id: int,
    db: DbSession,
    dm: CurrentDm,
    article_id: Annotated[str, Form(max_length=36)] = "",
    shared: Annotated[str, Form()] = "",
) -> Response:
    character = _get_character(db, character_id)
    try:
        character_service.set_extra_article(db, character, article_id, shared == "1")
    except character_service.CharacterError as exc:
        raise HTTPException(status_code=404) from exc
    if is_htmx(request):
        article = db.get(WaArticle, article_id)
        return render(
            request,
            "dm/_candidate_row.html",
            {
                "character": character,
                "article": article,
                "shared": article_id in character_service.extra_article_ids(db, character),
            },
        )
    return RedirectResponse(f"/mj/personnages/{character_id}", status_code=303)


@router.post("/personnages/{character_id}/supprimer")
def delete_character(request: Request, character_id: int, db: DbSession, dm: CurrentDm) -> Response:
    character = _get_character(db, character_id)
    name = character.name
    character_service.delete_character(db, character)
    flash(request, f"Personnage « {name} » supprimé.")
    return RedirectResponse("/mj/personnages", status_code=303)


def _dossier_url(character: Character) -> str:
    if character.main_article_id is None:
        return f"/mj/personnages/{character.id}"
    return f"/mj/articles/{character.main_article_id}#relations"


def _get_relation(db: DbSession, character: Character, relation_id: int) -> CharacterRelation:
    relation = db.get(CharacterRelation, relation_id)
    if relation is None or relation.character_id != character.id:
        raise HTTPException(status_code=404)
    return relation


def _relation_entry(
    name: str, category: str, role: str, status: str, article: str, text: str, gm_notes: str
) -> dict[str, str]:
    return {
        "name": name,
        "category": category,
        "role": role,
        "status": status,
        "article": article,
        "text": text,
        "gm_notes": gm_notes,
    }


def _relation_error(request: Request, db: DbSession, exc: dossier_service.DossierError) -> None:
    db.rollback()
    message = RELATION_ERROR_MESSAGES.get(exc.field or "", "Relation non enregistrée.")
    flash(request, f"Relation non enregistrée : {message[:1].lower()}{message[1:]}", "error")


RelationName = Annotated[str, Form(max_length=300)]
RelationShort = Annotated[str, Form(max_length=300)]
RelationText = Annotated[str, Form(max_length=60_000)]


@router.post("/personnages/{character_id}/relations")
def add_relation(
    request: Request,
    character_id: int,
    db: DbSession,
    dm: CurrentDm,
    name: RelationName = "",
    category: RelationShort = "",
    role: RelationShort = "",
    status: RelationShort = "",
    article: Annotated[str, Form(max_length=600)] = "",
    text: RelationText = "",
    gm_notes: RelationText = "",
) -> Response:
    character = _get_character(db, character_id)
    entry = _relation_entry(name, category, role, status, article, text, gm_notes)
    try:
        relation = dossier_service.add_relation(db, character, entry)
    except dossier_service.DossierError as exc:
        _relation_error(request, db, exc)
    else:
        flash(request, f"Relation « {relation.name} » ajoutée.")
    return RedirectResponse(_dossier_url(character), status_code=303)


@router.post("/personnages/{character_id}/relations/{relation_id}")
def update_relation(
    request: Request,
    character_id: int,
    relation_id: int,
    db: DbSession,
    dm: CurrentDm,
    name: RelationName = "",
    category: RelationShort = "",
    role: RelationShort = "",
    status: RelationShort = "",
    article: Annotated[str, Form(max_length=600)] = "",
    text: RelationText = "",
    gm_notes: RelationText = "",
) -> Response:
    character = _get_character(db, character_id)
    relation = _get_relation(db, character, relation_id)
    entry = _relation_entry(name, category, role, status, article, text, gm_notes)
    try:
        dossier_service.update_relation(db, character, relation, entry)
    except dossier_service.DossierError as exc:
        _relation_error(request, db, exc)
    else:
        flash(request, f"Relation « {relation.name} » enregistrée.")
    return RedirectResponse(_dossier_url(character), status_code=303)


@router.post("/personnages/{character_id}/relations/{relation_id}/supprimer")
def delete_relation(
    request: Request, character_id: int, relation_id: int, db: DbSession, dm: CurrentDm
) -> Response:
    character = _get_character(db, character_id)
    relation = _get_relation(db, character, relation_id)
    name = relation.name
    dossier_service.delete_relation(db, character, relation)
    flash(request, f"Relation « {name} » supprimée.")
    return RedirectResponse(_dossier_url(character), status_code=303)


def _get_event(db: DbSession, character: Character, event_id: int) -> CharacterEvent:
    event = db.get(CharacterEvent, event_id)
    if event is None or event.character_id != character.id:
        raise HTTPException(status_code=404)
    return event


def _events_url(character: Character, anchor: str = "evenements") -> str:
    if character.main_article_id is None:
        return f"/mj/personnages/{character.id}"
    return f"/mj/articles/{character.main_article_id}#{anchor}"


def _whole_number(value: str, field: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise dossier_service.DossierError(f"'{field}' must be a whole number", field) from exc


def _rows(keys: tuple[str, ...], *columns: list[str]) -> list[dict[str, str]]:
    return [
        dict(zip(keys, values, strict=True))
        for values in zip_longest(*columns, fillvalue="")
        if any(value.strip() for value in values)
    ]


EventShort = Annotated[str, Form(max_length=600)]
EventText = Annotated[str, Form(max_length=60_000)]
EventColumn = Annotated[list[str], Form()]


def _event_entry(
    title: str,
    session: str,
    year: str,
    age: str,
    approximate: str,
    place: str,
    place_article: str,
    text: str,
    consequences: str,
    gm_notes: str,
    people: list[dict[str, str]],
    organizations: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "title": title,
        "session": session,
        "year": _whole_number(year, "year"),
        "age": _whole_number(age, "age"),
        "approximate": bool(approximate),
        "place": place,
        "place_article": place_article,
        "text": text,
        "consequences": consequences,
        "gm_notes": gm_notes,
        "people": people,
        "organizations": organizations,
    }


def _event_error(request: Request, db: DbSession, exc: dossier_service.DossierError) -> None:
    db.rollback()
    message = EVENT_ERROR_MESSAGES.get(exc.field or "", "Événement non enregistré.")
    flash(request, f"Événement non enregistré : {message[:1].lower()}{message[1:]}", "error")


@router.post("/personnages/{character_id}/evenements")
@router.post("/personnages/{character_id}/evenements/{event_id}")
def save_event(
    request: Request,
    character_id: int,
    db: DbSession,
    dm: CurrentDm,
    event_id: int | None = None,
    title: EventShort = "",
    session: EventShort = "",
    year: EventShort = "",
    age: EventShort = "",
    approximate: EventShort = "",
    place: EventShort = "",
    place_article: EventShort = "",
    text: EventText = "",
    consequences: EventText = "",
    gm_notes: EventText = "",
    person_name: EventColumn = [],  # noqa: B006
    person_role: EventColumn = [],  # noqa: B006
    person_article: EventColumn = [],  # noqa: B006
    organization_name: EventColumn = [],  # noqa: B006
    organization_article: EventColumn = [],  # noqa: B006
) -> Response:
    character = _get_character(db, character_id)
    event = _get_event(db, character, event_id) if event_id is not None else None
    try:
        entry = _event_entry(
            title,
            session,
            year,
            age,
            approximate,
            place,
            place_article,
            text,
            consequences,
            gm_notes,
            _rows(("name", "role", "article"), person_name, person_role, person_article),
            _rows(("name", "article"), organization_name, organization_article),
        )
        if event is None:
            event = dossier_service.add_event(db, character, entry)
            flash(request, f"Événement « {event.title} » ajouté.")
        else:
            dossier_service.update_event(db, character, event, entry)
            flash(request, f"Événement « {event.title} » enregistré.")
    except dossier_service.DossierError as exc:
        _event_error(request, db, exc)
        return RedirectResponse(_events_url(character), status_code=303)
    return RedirectResponse(_events_url(character, f"evenement-{event.id}"), status_code=303)


@router.post("/personnages/{character_id}/evenements/{event_id}/supprimer")
def delete_event(
    request: Request, character_id: int, event_id: int, db: DbSession, dm: CurrentDm
) -> Response:
    character = _get_character(db, character_id)
    event = _get_event(db, character, event_id)
    title = event.title
    dossier_service.delete_event(db, character, event)
    flash(request, f"Événement « {title} » supprimé.")
    return RedirectResponse(_events_url(character), status_code=303)
