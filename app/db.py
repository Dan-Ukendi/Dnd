import logging
import time
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=240,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        connect_args={
            "connect_timeout": 15,
            "prepare_threshold": None,
            "application_name": "campaign-companion",
        },
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def ping(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def wait_for_database(engine: Engine, attempts: int = 8, initial_delay: float = 1.0) -> None:
    delay = initial_delay
    for attempt in range(1, attempts + 1):
        try:
            ping(engine)
            return
        except OperationalError:
            if attempt == attempts:
                raise
            logger.warning(
                "Database not reachable (attempt %d/%d), retrying in %.1fs",
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 15.0)
