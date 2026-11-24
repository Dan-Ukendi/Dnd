from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings
from app.deps import ACCOUNT_SETUP_PATH, DbSession, OptionalUser, client_ip, log_in
from app.models import Role, User
from app.services.accounts import (
    AccountError,
    accept_invitation,
    authenticate,
    find_login_player,
    find_valid_invitation,
    login_players,
)
from app.templating import render

router = APIRouter()

ACCOUNT_ERROR_MESSAGES = {
    "password_too_short": "Le mot de passe doit contenir au moins 10 caractères.",
    "password_too_long": "Le mot de passe est trop long.",
    "password_mismatch": "Les deux mots de passe ne correspondent pas.",
    "invitation_used": "Ce lien d'invitation a déjà été utilisé.",
}


def home_url(user: User) -> str:
    return "/mj" if user.role == Role.DM else "/joueur"


@router.get("/")
def index(user: OptionalUser) -> RedirectResponse:
    return RedirectResponse(home_url(user) if user else "/connexion", status_code=303)


@router.get("/connexion")
def login_choice(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse(home_url(user), status_code=303)
    return render(request, "login_choice.html")


@router.get("/connexion/mj")
def login_form(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse(home_url(user), status_code=303)
    return render(request, "login.html", {"login": ""})


@router.get("/connexion/joueurs")
def login_players_page(request: Request, db: DbSession, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse(home_url(user), status_code=303)
    return render(request, "login_players.html", {"players": login_players(db)})


@router.get("/connexion/joueurs/{player_login}")
def login_player_form(
    request: Request, player_login: str, db: DbSession, user: OptionalUser
) -> Response:
    if user is not None:
        return RedirectResponse(home_url(user), status_code=303)
    player = find_login_player(db, player_login)
    if player is None:
        raise HTTPException(status_code=404)
    return render(request, "login_player.html", {"player": player})


def _login_error(
    request: Request, db: DbSession, mode: str, login: str, message: str, status_code: int
) -> Response:
    if mode == "joueur":
        player = find_login_player(db, login)
        if player is not None:
            return render(
                request,
                "login_player.html",
                {"player": player, "error": message},
                status_code=status_code,
            )
    return render(
        request, "login.html", {"login": login, "error": message}, status_code=status_code
    )


@router.post("/connexion")
def login_submit(
    request: Request,
    db: DbSession,
    login: Annotated[str, Form(max_length=200)] = "",
    password: Annotated[str, Form(max_length=512)] = "",
    mode: Annotated[str, Form(max_length=10)] = "mj",
) -> Response:
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    if limiter.is_blocked(ip, login):
        return _login_error(
            request,
            db,
            mode,
            login,
            "Trop de tentatives de connexion. Réessayez dans quelques minutes.",
            429,
        )
    user = authenticate(db, login, password)
    if user is None:
        limiter.register_failure(ip, login)
        message = (
            "Mot de passe incorrect."
            if mode == "joueur"
            else "Identifiant ou mot de passe incorrect."
        )
        return _login_error(request, db, mode, login, message, 400)
    limiter.reset(ip, login)
    log_in(request, user)
    if (
        user.role == Role.PLAYER
        and user.must_change_password
        and get_settings().player_password_change
    ):
        return RedirectResponse(ACCOUNT_SETUP_PATH, status_code=303)
    return RedirectResponse(home_url(user), status_code=303)


@router.post("/deconnexion")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/connexion", status_code=303)


@router.get("/invitation/{token}")
def invitation_form(request: Request, token: str, db: DbSession) -> Response:
    invitation = find_valid_invitation(db, token)
    if invitation is None:
        raise HTTPException(status_code=404)
    return render(request, "invitation.html", {"invited": invitation.user, "token": token})


@router.post("/invitation/{token}")
def invitation_submit(
    request: Request,
    token: str,
    db: DbSession,
    password: Annotated[str, Form(max_length=512)] = "",
    password_confirmation: Annotated[str, Form(max_length=512)] = "",
) -> Response:
    invitation = find_valid_invitation(db, token)
    if invitation is None:
        raise HTTPException(status_code=404)
    try:
        user = accept_invitation(db, invitation, password, password_confirmation)
    except AccountError as exc:
        return render(
            request,
            "invitation.html",
            {
                "invited": invitation.user,
                "token": token,
                "error": ACCOUNT_ERROR_MESSAGES.get(exc.code, "Impossible d'activer le compte."),
            },
            status_code=400,
        )
    log_in(request, user)
    return RedirectResponse("/joueur", status_code=303)
