import json
from collections.abc import Callable

import httpx
import pytest

from app.worldanvil.client import (
    WorldAnvilAuthError,
    WorldAnvilClient,
    WorldAnvilError,
    WorldAnvilNotFoundError,
)

APP_KEY = "app-key-secret-value"
TOKEN = "user-token-secret-value"


def build_client(
    handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float], max_retries: int = 3
) -> WorldAnvilClient:
    return WorldAnvilClient(
        APP_KEY,
        TOKEN,
        base_url="https://wa.test/api/external/boromir",
        user_agent="Test (tests, 1.0)",
        max_retries=max_retries,
        min_interval=0,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )


def test_requests_carry_authentication_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "u1", "success": True})

    with build_client(handler, []) as client:
        assert client.get_identity()["id"] == "u1"
    request = seen[0]
    assert request.headers["x-application-key"] == APP_KEY
    assert request.headers["x-auth-token"] == TOKEN
    assert request.headers["user-agent"] == "Test (tests, 1.0)"
    assert request.url.path == "/api/external/boromir/identity"


def test_article_listing_is_paginated() -> None:
    bodies: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        assert request.url.params["id"] == "world-1"
        offset = int(body["offset"])
        count = 50 if offset == 0 else 3
        entities = [{"id": f"a{offset + i}"} for i in range(count)]
        return httpx.Response(200, json={"success": True, "entities": entities})

    with build_client(handler, []) as client:
        articles = list(client.iter_world_articles("world-1"))
    assert len(articles) == 53
    assert bodies == [{"limit": "50", "offset": "0"}, {"limit": "50", "offset": "50"}]


def test_rate_limit_honours_retry_after() -> None:
    sleeps: list[float] = []
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"success": False}),
            httpx.Response(200, json={"id": "x", "success": True}),
        ]
    )
    with build_client(lambda request: next(responses), sleeps) as client:
        assert client.get_article("x")["id"] == "x"
    assert sleeps == [2.0]


def test_server_errors_are_retried_with_backoff_then_raised() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="unavailable")

    with build_client(handler, sleeps, max_retries=3) as client, pytest.raises(WorldAnvilError):
        client.get_article("x")
    assert len(calls) == 4
    assert len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2]


def test_network_errors_are_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"id": "ok", "success": True})

    with build_client(handler, []) as client:
        assert client.get_article("x")["id"] == "ok"
    assert len(attempts) == 3


@pytest.mark.parametrize(
    ("status", "payload", "error"),
    [
        (401, {"success": False, "error": "access_denied_auth_failed"}, WorldAnvilAuthError),
        (403, {"success": False, "error": "access_denied"}, WorldAnvilAuthError),
        (404, {"success": "false", "error": "resource_not_found"}, WorldAnvilNotFoundError),
        (422, {"success": False, "error": {"summary": "bad data"}}, WorldAnvilError),
        (200, {"success": "false"}, WorldAnvilError),
    ],
)
def test_errors_are_mapped_without_leaking_credentials(
    status: int, payload: dict[str, object], error: type[Exception]
) -> None:
    sleeps: list[float] = []
    client = build_client(lambda request: httpx.Response(status, json=payload), sleeps)
    with client, pytest.raises(error) as raised:
        client.get_article("x")
    assert sleeps == []
    message = str(raised.value)
    assert APP_KEY not in message
    assert TOKEN not in message


def test_missing_credentials_are_rejected() -> None:
    with pytest.raises(WorldAnvilAuthError):
        WorldAnvilClient("", "", base_url="https://wa.test", user_agent="Test")
