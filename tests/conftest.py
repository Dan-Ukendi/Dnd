import os
from collections.abc import Iterator

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

os.environ.update(
    {
        "DATABASE_URL": TEST_DATABASE_URL or "postgresql://invalid/invalid",
        "DATABASE_URL_UNPOOLED": "",
        "SECRET_KEY": "test-secret-key-0123456789abcdefghijklmnopqrstuvwxyz",
        "APP_ENV": "test",
        "WA_APPLICATION_KEY": "test-application-key",
        "WA_AUTH_TOKEN": "test-auth-token",
        "WA_WORLD_ID": "11111111-1111-1111-1111-111111111111",
    }
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine, get_session_factory, reset_engine  # noqa: E402

TABLES = (
    "timeline_event_fiches",
    "timeline_event_links",
    "timeline_events",
    "timeline_eras",
    "character_actions",
    "character_items",
    "character_relations",
    "character_events",
    "character_profiles",
    "media_variants",
    "media_assets",
    "character_articles",
    "characters",
    "invitations",
    "users",
    "wa_images",
    "wa_articles",
    "sync_runs",
)


def pytest_sessionstart(session: pytest.Session) -> None:
    if not TEST_DATABASE_URL:
        pytest.exit("TEST_DATABASE_URL must point to a disposable Postgres database", returncode=2)
    if "neon.tech" in TEST_DATABASE_URL:
        pytest.exit("Refusing to run the test suite against a Neon database", returncode=2)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    get_settings.cache_clear()
    reset_engine()
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    yield engine
    reset_engine()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def other_client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client
