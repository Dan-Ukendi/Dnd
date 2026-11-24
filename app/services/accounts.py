import re
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Invitation, Role, User, utcnow
from app.security import (
    MIN_PASSWORD_LENGTH,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,49}$")
MAX_PASSWORD_LENGTH = 256


class AccountError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_login(login: str) -> str:
    return login.strip().lower()


def validate_password(password: str, confirmation: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError("password_too_short")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError("password_too_long")
    if confirmation is not None and password != confirmation:
        raise AccountError("password_mismatch")


def create_user(
    db: Session,
    *,
    display_name: str,
    login: str,
    role: Role,
    password: str | None = None,
) -> User:
    display_name = display_name.strip()
    login = normalize_login(login)
    if not display_name or len(display_name) > 100:
        raise AccountError("invalid_display_name")
    if not LOGIN_RE.match(login):
        raise AccountError("invalid_login")
    if db.scalar(select(User.id).where(User.login == login)) is not None:
        raise AccountError("login_taken")
    user = User(display_name=display_name, login=login, role=role, is_active=True)
    if password is not None:
        validate_password(password)
        user.password_hash = hash_password(password)
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, login: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.login == normalize_login(login)))
    if user is None or not user.is_active:
        verify_password(None, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    return user


def create_invitation(db: Session, user: User, ttl_hours: int) -> str:
    if user.role != Role.PLAYER:
        raise AccountError("not_a_player")
    db.execute(
        delete(Invitation).where(Invitation.user_id == user.id, Invitation.used_at.is_(None))
    )
    token = generate_token()
    db.add(
        Invitation(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        )
    )
    db.commit()
    return token


def find_valid_invitation(db: Session, token: str) -> Invitation | None:
    if not token or len(token) > 200:
        return None
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    if invitation is None or invitation.used_at is not None:
        return None
    if invitation.expires_at <= utcnow():
        return None
    user = invitation.user
    if not user.is_active or user.role != Role.PLAYER:
        return None
    return invitation


def accept_invitation(
    db: Session, invitation: Invitation, password: str, confirmation: str
) -> User:
    validate_password(password, confirmation)
    claimed = db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation.id, Invitation.used_at.is_(None))
        .with_for_update()
    )
    if claimed is None:
        db.rollback()
        raise AccountError("invitation_used")
    claimed.used_at = utcnow()
    user = claimed.user
    user.password_hash = hash_password(password)
    user.session_version += 1
    db.commit()
    return user


def set_active(db: Session, user: User, active: bool) -> None:
    user.is_active = active
    user.session_version += 1
    if not active:
        db.execute(
            delete(Invitation).where(Invitation.user_id == user.id, Invitation.used_at.is_(None))
        )
    db.commit()
