from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import DbSession, OptionalUser, client_ip, log_in
from app.models import Role, User
from app.services.accounts import (
    AccountError,
    accept_invitation,
    authenticate,
    find_valid_invitation,
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
def login_form(request: Request, user: OptionalUser) -> Response:
    if user is not None:
        return RedirectResponse(home_url(user), status_code=303)
    return render(request, "login.html", {"login": ""})


@router.post("/connexion")
def login_submit(
    request: Request,
    db: DbSession,
    login: Annotated[str, Form(max_length=200)] = "",
    password: Annotated[str, Form(max_length=512)] = "",
) -> Response:
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    if limiter.is_blocked(ip, login):
        return render(
            request,
            "login.html",
            {
                "login": login,
                "error": "Trop de tentatives de connexion. Réessayez dans quelques minutes.",
            },
            status_code=429,
        )
    user = authenticate(db, login, password)
    if user is None:
        limiter.register_failure(ip, login)
        return render(
            request,
            "login.html",
            {"login": login, "error": "Identifiant ou mot de passe incorrect."},
            status_code=400,
        )
    limiter.reset(ip, login)
    log_in(request, user)
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
