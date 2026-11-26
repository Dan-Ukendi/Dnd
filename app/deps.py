from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User

SESSION_USER_KEY = "uid"
SESSION_VERSION_KEY = "sv"
SESSION_MASTER_KEY = "mk"

DbSession = Annotated[Session, Depends(get_db)]


class NotAuthenticatedError(Exception):
    pass


def log_in(request: Request, user: User, *, master_key: bool = False) -> None:
    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    request.session[SESSION_VERSION_KEY] = user.session_version
    if master_key:
        request.session[SESSION_MASTER_KEY] = True


def is_master_key_session(request: Request) -> bool:
    return request.session.get(SESSION_MASTER_KEY) is True


def get_current_user(request: Request, db: DbSession) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or (not user.has_password and not get_settings().passwordless_login)
        or user.session_version != request.session.get(SESSION_VERSION_KEY)
    ):
        request.session.clear()
        return None
    request.state.user = user
    return user


OptionalUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: OptionalUser) -> User:
    if user is None:
        raise NotAuthenticatedError
    return user


def require_dm(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != Role.DM:
        raise HTTPException(status_code=404)
    return user


ACCOUNT_SETUP_PATH = "/joueur/mot-de-passe"


class PasswordChangeRequiredError(Exception):
    pass


def require_player(request: Request, user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != Role.PLAYER:
        raise HTTPException(status_code=404)
    if (
        user.must_change_password
        and not is_master_key_session(request)
        and request.url.path != ACCOUNT_SETUP_PATH
    ):
        raise PasswordChangeRequiredError
    return user


CurrentDm = Annotated[User, Depends(require_dm)]
CurrentPlayer = Annotated[User, Depends(require_player)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
