import logging
import secrets
import time
from collections.abc import Callable, Iterator
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Protocol, Self

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class WorldAnvilError(Exception):
    pass


class WorldAnvilAuthError(WorldAnvilError):
    pass


class WorldAnvilNotFoundError(WorldAnvilError):
    pass


class WorldAnvilSource(Protocol):
    def iter_world_articles(self, world_id: str) -> Iterator[dict[str, Any]]: ...

    def get_article(self, article_id: str, granularity: int = 2) -> dict[str, Any]: ...

    def get_image(self, image_id: int, granularity: int = 0) -> dict[str, Any]: ...

    def iter_world_timelines(self, world_id: str) -> Iterator[dict[str, Any]]: ...

    def get_timeline(self, timeline_id: str, granularity: int = 2) -> dict[str, Any]: ...

    def get_history(self, history_id: str, granularity: int = 1) -> dict[str, Any]: ...


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, retry_at.timestamp() - time.time())


def _error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict) and isinstance(error.get("summary"), str):
            return error["summary"]
    return ""


class WorldAnvilClient:
    def __init__(
        self,
        application_key: str,
        auth_token: str,
        *,
        base_url: str,
        user_agent: str,
        timeout: float = 30.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 30.0,
        min_interval: float = 0.2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not application_key or not auth_token:
            raise WorldAnvilAuthError("World Anvil credentials are not configured")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "x-application-key": application_key,
                "x-auth-token": auth_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._min_interval = min_interval
        self._sleep = sleep
        self._last_request_at = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.wa_application_key.get_secret_value(),
            settings.wa_auth_token.get_secret_value(),
            base_url=settings.wa_base_url,
            user_agent=settings.wa_user_agent,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _backoff(self, attempt: int) -> float:
        delay = self._backoff_base * (2**attempt)
        jitter = secrets.randbelow(1000) / 1000 * self._backoff_base / 2
        return min(self._backoff_max, delay) + jitter

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                response = self._http.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                self._last_request_at = time.monotonic()
                if attempt >= self._max_retries:
                    raise WorldAnvilError(f"Network error while calling {path}") from exc
                delay = self._backoff(attempt)
                logger.warning("Network error on %s, retrying in %.1fs", path, delay)
                self._sleep(delay)
                continue
            self._last_request_at = time.monotonic()
            status = response.status_code
            if status in RETRYABLE_STATUSES:
                if attempt >= self._max_retries:
                    raise WorldAnvilError(f"World Anvil returned HTTP {status} for {path}")
                retry_after = _retry_after_seconds(response) if status == 429 else None
                delay = (
                    min(retry_after, self._backoff_max * 2)
                    if retry_after is not None
                    else self._backoff(attempt)
                )
                logger.warning("HTTP %d on %s, retrying in %.1fs", status, path, delay)
                self._sleep(delay)
                continue
            if status in (401, 403):
                raise WorldAnvilAuthError(
                    f"World Anvil refused access to {path} (HTTP {status} {_error_code(response)})"
                )
            if status == 404:
                raise WorldAnvilNotFoundError(f"World Anvil resource not found: {path}")
            if status >= 400:
                raise WorldAnvilError(
                    f"World Anvil returned HTTP {status} for {path}: {_error_code(response)}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise WorldAnvilError(f"Invalid JSON returned for {path}") from exc
            if not isinstance(payload, dict):
                raise WorldAnvilError(f"Unexpected payload returned for {path}")
            if payload.get("success") in (False, "false"):
                raise WorldAnvilError(f"World Anvil reported a failure for {path}")
            return payload
        raise WorldAnvilError(f"Retries exhausted for {path}")

    def get_identity(self) -> dict[str, Any]:
        return self._request("GET", "/identity")

    def get_world(self, world_id: str, granularity: int = 0) -> dict[str, Any]:
        return self._request("GET", "/world", params={"id": world_id, "granularity": granularity})

    def _iter_listing(self, path: str, world_id: str) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            payload = self._request(
                "POST",
                path,
                params={"id": world_id},
                json={"limit": str(PAGE_SIZE), "offset": str(offset)},
            )
            entities = payload.get("entities") or []
            if not isinstance(entities, list):
                raise WorldAnvilError(f"Unexpected listing format for {path}")
            yield from entities
            if len(entities) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    def iter_world_articles(self, world_id: str) -> Iterator[dict[str, Any]]:
        return self._iter_listing("/world/articles", world_id)

    def iter_world_timelines(self, world_id: str) -> Iterator[dict[str, Any]]:
        return self._iter_listing("/world/timelines", world_id)

    def get_article(self, article_id: str, granularity: int = 2) -> dict[str, Any]:
        return self._request(
            "GET", "/article", params={"id": article_id, "granularity": granularity}
        )

    def get_image(self, image_id: int, granularity: int = 0) -> dict[str, Any]:
        return self._request("GET", "/image", params={"id": image_id, "granularity": granularity})

    def get_timeline(self, timeline_id: str, granularity: int = 2) -> dict[str, Any]:
        return self._request(
            "GET", "/timeline", params={"id": timeline_id, "granularity": granularity}
        )

    def get_history(self, history_id: str, granularity: int = 1) -> dict[str, Any]:
        return self._request(
            "GET", "/history", params={"id": history_id, "granularity": granularity}
        )
