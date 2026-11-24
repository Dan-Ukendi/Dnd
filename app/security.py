import contextlib
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

CSRF_SESSION_KEY = "csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if password_hash is None:
        with contextlib.suppress(VerificationError):
            _hasher.verify(_DUMMY_HASH, password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str):
        token = generate_token()
        request.session[CSRF_SESSION_KEY] = token
    return token


async def csrf_protect(request: Request) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    expected = request.session.get(CSRF_SESSION_KEY)
    provided = request.headers.get(CSRF_HEADER)
    if not provided:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FORM_FIELD)
            provided = value if isinstance(value, str) else None
    if (
        not isinstance(expected, str)
        or not provided
        or not hmac.compare_digest(expected.encode(), provided.encode())
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


class LoginRateLimiter:
    def __init__(
        self,
        max_per_account: int = 5,
        max_per_ip: int = 20,
        window_seconds: float = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_per_account = max_per_account
        self.max_per_ip = max_per_ip
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _account_key(ip: str, login: str) -> str:
        return f"acct:{ip}:{login.strip().lower()}"

    @staticmethod
    def _ip_key(ip: str) -> str:
        return f"ip:{ip}"

    def _count(self, key: str, now: float) -> int:
        bucket = self._failures.get(key)
        if bucket is None:
            return 0
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if not bucket:
            del self._failures[key]
            return 0
        return len(bucket)

    def is_blocked(self, ip: str, login: str) -> bool:
        with self._lock:
            now = self._clock()
            return (
                self._count(self._account_key(ip, login), now) >= self.max_per_account
                or self._count(self._ip_key(ip), now) >= self.max_per_ip
            )

    def register_failure(self, ip: str, login: str) -> None:
        with self._lock:
            now = self._clock()
            for key in (self._account_key(ip, login), self._ip_key(ip)):
                self._failures.setdefault(key, deque()).append(now)

    def reset(self, ip: str, login: str) -> None:
        with self._lock:
            self._failures.pop(self._account_key(ip, login), None)
