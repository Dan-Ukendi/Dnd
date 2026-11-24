from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SyncRun, SyncStatus, WaArticle, WaImage, utcnow
from app.services.articles import search_articles
from app.services.sync import SyncAlreadyRunningError, start_run, sync_world
from app.worldanvil.client import WorldAnvilError, WorldAnvilNotFoundError

WORLD = "11111111-1111-1111-1111-111111111111"
A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "aaaaaaaa-0000-0000-0000-000000000002"
C = "aaaaaaaa-0000-0000-0000-000000000003"


def wa_date(value: str) -> dict[str, Any]:
    return {"date": f"{value}.000000", "timezone_type": 3, "timezone": "Europe/London"}


class FakeSource:
    def __init__(self) -> None:
        self.articles: dict[str, dict[str, Any]] = {}
        self.images: dict[int, dict[str, Any]] = {}
        self.article_calls: list[str] = []
        self.image_calls: list[int] = []
        self.fail_on: str | None = None

    def put(self, wa_id: str, title: str, content: str, updated: str, **extra: Any) -> None:
        self.articles[wa_id] = {
            "id": wa_id,
            "title": title,
            "state": "public",
            "isDraft": False,
            "isWip": True,
            "entityClass": extra.pop("entityClass", "Person"),
            "templateType": extra.pop("templateType", "person"),
            "url": f"https://www.worldanvil.com/w/monde/a/{title.lower().replace(' ', '-')}",
            "subscribergroups": [],
            "secrets": [],
            "content": content,
            "updateDate": wa_date(updated),
            **extra,
        }

    def iter_world_articles(self, world_id: str) -> Iterator[dict[str, Any]]:
        assert world_id == WORLD
        for article in self.articles.values():
            yield {key: article[key] for key in ("id", "title", "entityClass", "url", "updateDate")}

    def get_article(self, article_id: str, granularity: int = 2) -> dict[str, Any]:
        assert granularity == 2
        self.article_calls.append(article_id)
        if article_id == self.fail_on:
            raise WorldAnvilError("World Anvil returned HTTP 503 for /article")
        return self.articles[article_id]

    def get_image(self, image_id: int, granularity: int = 0) -> dict[str, Any]:
        self.image_calls.append(image_id)
        if image_id not in self.images:
            raise WorldAnvilNotFoundError("missing")
        return self.images[image_id]

    def iter_world_timelines(self, world_id: str) -> Iterator[dict[str, Any]]:
        return iter(())

    def get_timeline(self, timeline_id: str, granularity: int = 2) -> dict[str, Any]:
        raise WorldAnvilNotFoundError("missing")

    def get_history(self, history_id: str, granularity: int = 1) -> dict[str, Any]:
        raise WorldAnvilNotFoundError("missing")


@pytest.fixture
def source() -> FakeSource:
    fake = FakeSource()
    fake.put(
        A,
        "Bob",
        "[p]Un voleur halfelin, ami de @[Sophie](person:" + B + ").[/p]",
        "2026-11-22 13:45:47",
    )
    fake.put(
        B,
        "Sophie",
        "[p]Elle déteste les voleurs. Voir [url:https://www.worldanvil.com/w/monde/a/bob]Bob[/url].[/p][img:7]",
        "2026-11-22 13:50:00",
    )
    fake.images[7] = {
        "id": 7,
        "title": "Portrait",
        "url": "https://www.worldanvil.com/uploads/images/7.png",
        "state": "public",
        "subscribergroups": [],
    }
    return fake


def run(db: Session, source: FakeSource, *, full: bool = False) -> SyncRun:
    return sync_world(db, source, WORLD, full=full, trigger="cli")


def test_initial_sync_creates_articles(db: Session, source: FakeSource) -> None:
    result = run(db, source)
    assert result.status == SyncStatus.SUCCESS
    assert (result.created_count, result.updated_count, result.unchanged_count) == (2, 0, 0)
    bob = db.get(WaArticle, A)
    assert bob.title == "Bob"
    assert bob.template_type == "person"
    assert bob.player_visible
    assert bob.raw["content"].startswith("[p]")
    assert bob.content_text.startswith("Un voleur halfelin")
    assert bob.wa_updated_at is not None
    assert bob.linked_ids == [B]
    assert db.get(WaArticle, B).linked_ids == [A]
    image = db.get(WaImage, 7)
    assert image.url.endswith("/7.png")
    assert image.player_visible


def test_full_text_search_uses_french_stemming(db: Session, source: FakeSource) -> None:
    run(db, source)
    titles = {article.title for article in search_articles(db, query="voleur")}
    assert titles == {"Bob", "Sophie"}
    assert [article.title for article in search_articles(db, query="déteste")] == ["Sophie"]


def test_incremental_sync_skips_unchanged_articles(db: Session, source: FakeSource) -> None:
    run(db, source)
    source.article_calls.clear()
    source.image_calls.clear()
    result = run(db, source)
    assert (result.created_count, result.updated_count, result.unchanged_count) == (0, 0, 2)
    assert source.article_calls == []
    assert source.image_calls == []


def test_incremental_sync_refetches_changed_articles(db: Session, source: FakeSource) -> None:
    run(db, source)
    source.article_calls.clear()
    source.put(A, "Bob", "[p]Bob a changé.[/p]", "2026-11-23 09:00:00")
    result = run(db, source)
    assert (result.updated_count, result.unchanged_count) == (1, 1)
    assert source.article_calls == [A]
    db.expire_all()
    assert db.get(WaArticle, A).content_text == "Bob a changé."
    assert db.get(WaArticle, A).linked_ids == []


def test_full_sync_refetches_everything(db: Session, source: FakeSource) -> None:
    run(db, source)
    source.article_calls.clear()
    result = run(db, source, full=True)
    assert (result.updated_count, result.unchanged_count) == (2, 0)
    assert sorted(source.article_calls) == [A, B]


def test_articles_deleted_on_world_anvil_are_marked_removed(
    db: Session, source: FakeSource
) -> None:
    run(db, source)
    del source.articles[B]
    result = run(db, source)
    assert result.removed_count == 1
    db.expire_all()
    removed = db.get(WaArticle, B)
    assert removed.removed_at is not None
    assert not removed.player_visible
    assert db.get(WaArticle, A).linked_ids == []


def test_restricted_articles_are_hidden_from_players(db: Session, source: FakeSource) -> None:
    source.put(
        C,
        "Intrigue",
        "[p]Le plan.[/p]",
        "2026-11-22 10:00:00",
        entityClass="Plot",
        templateType="plot",
    )
    source.put(A, "Bob", "[p]Visible[/p][secret:s1]", "2026-11-22 10:00:00")
    run(db, source)
    assert not db.get(WaArticle, C).player_visible
    bob = db.get(WaArticle, A)
    assert not bob.player_visible
    assert "réservées" in bob.visibility_reason


def test_failed_sync_is_recorded_and_rolled_back(db: Session, source: FakeSource) -> None:
    run(db, source)
    source.put(A, "Bob modifié", "[p]Nouveau[/p]", "2026-11-24 08:00:00")
    source.put(C, "Nouveau", "[p]Nouveau[/p]", "2026-11-24 08:00:00")
    source.fail_on = C
    result = run(db, source)
    assert result.status == SyncStatus.FAILED
    assert "HTTP 503" in result.error
    db.expire_all()
    assert db.get(WaArticle, A).title == "Bob"
    assert db.get(WaArticle, C) is None


def test_concurrent_runs_are_refused_but_stale_ones_recovered(db: Session) -> None:
    start_run(db, full=False, trigger="ui")
    with pytest.raises(SyncAlreadyRunningError):
        start_run(db, full=False, trigger="cli")
    stale = db.scalar(select(SyncRun))
    stale.started_at = utcnow() - timedelta(hours=1)
    db.commit()
    fresh = start_run(db, full=False, trigger="cli")
    db.refresh(stale)
    assert stale.status == SyncStatus.FAILED
    assert fresh.status == SyncStatus.RUNNING
