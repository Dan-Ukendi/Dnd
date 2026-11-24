from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Role, SyncRun, SyncStatus, TimelineEvent, TimelineEventLink
from app.services.chronology import load_seed, validate_event_data
from app.services.chronology_sync import parse_history_content
from app.services.sync import sync_world
from tests.factories import login, make_user

WORLD = "11111111-1111-1111-1111-111111111111"
TIMELINE = "7bc72ed0-5d45-4636-a6ac-47305e5d0d2a"
H1 = "aaaaaaaa-0000-0000-0000-000000000001"
H2 = "aaaaaaaa-0000-0000-0000-000000000002"
H3 = "aaaaaaaa-0000-0000-0000-000000000003"
H4 = "aaaaaaaa-0000-0000-0000-000000000004"
H5 = "aaaaaaaa-0000-0000-0000-000000000005"


def wa_date(value: str) -> dict[str, Any]:
    return {"date": f"{value}.000000", "timezone_type": 3, "timezone": "Europe/London"}


def content(
    summary: str,
    code: str | None,
    *,
    place: str = "Aterna",
    origin: str = "canon",
    status: str = "à valider",
    linked: str = "",
    chronicle: str = "",
) -> str:
    lines = [f"[p]{summary}[/p]", "[ul]", f"- [b]Lieu :[/b] {place}"]
    lines.append(f"- [b]Origine :[/b] {origin}")
    lines.append(f"- [b]Statut :[/b] {status}")
    if linked:
        lines.append(f"- [b]Lié à :[/b] {linked}")
    if chronicle:
        lines.append(f"- [b]Chronique :[/b] {chronicle}")
    if code:
        lines.append(f"- [b]Code :[/b] {code}")
    lines.append("[/ul]")
    return "\n".join(lines)


class FakeTimelineSource:
    def __init__(self) -> None:
        self.histories: dict[str, dict[str, Any]] = {}
        self.history_calls: list[str] = []
        self.timelines = [
            {"id": "bbbbbbbb-0000-0000-0000-000000000000", "title": "Autre frise"},
            {"id": TIMELINE, "title": "Chronologie de Valhar Molaris"},
        ]

    def put(
        self,
        history_id: str,
        title: str,
        year: str,
        text: str,
        *,
        ending: str | None = None,
        significance: int = 5,
        updated: str = "2026-11-24 10:00:00",
    ) -> None:
        self.histories[history_id] = {
            "id": history_id,
            "title": title,
            "year": year,
            "endingYear": ending,
            "significance": significance,
            "state": "private",
            "content": text,
            "updateDate": wa_date(updated),
        }

    def iter_world_articles(self, world_id: str) -> Iterator[dict[str, Any]]:
        return iter(())

    def get_article(self, article_id: str, granularity: int = 2) -> dict[str, Any]:
        raise AssertionError("no article expected")

    def get_image(self, image_id: int, granularity: int = 0) -> dict[str, Any]:
        raise AssertionError("no image expected")

    def iter_world_timelines(self, world_id: str) -> Iterator[dict[str, Any]]:
        assert world_id == WORLD
        return iter(self.timelines)

    def get_timeline(self, timeline_id: str, granularity: int = 2) -> dict[str, Any]:
        assert timeline_id == TIMELINE
        return {
            "id": TIMELINE,
            "histories": [
                {"id": item["id"], "title": item["title"], "updateDate": item["updateDate"]}
                for item in self.histories.values()
            ],
        }

    def get_history(self, history_id: str, granularity: int = 1) -> dict[str, Any]:
        self.history_calls.append(history_id)
        return self.histories[history_id]


@pytest.fixture
def source() -> FakeTimelineSource:
    fake = FakeTimelineSource()
    fake.put(H1, "Création de l'Empire", "0", content("Fondation.", "B1"))
    fake.put(
        H2,
        "Zilvra et Khaetar se disputent l'amulette ; Zilvra l'emporte",
        "987",
        content("Dispute.", "S1", chronicle="séance 1, jour J1"),
        significance=3,
    )
    fake.put(
        H3,
        "Poursuite par les veilleurs",
        "987",
        content(
            "Fuite.",
            "S2",
            linked="Zilvra et Khaetar se disputent l'amulette ; Zilvra l'emporte ; "
            "Création de l'Empire",
            chronicle="séance 1, jour J1",
        ),
        significance=1,
    )
    return fake


@pytest.fixture
def seeded(db: Session) -> dict[str, TimelineEvent]:
    rows = [
        {
            "code": "B1",
            "title": "Création de l'Empire",
            "start_year": 0,
            "importance": "majeur",
            "origin": "canon",
            "summary": "Fondation.",
            "place": "Aterna",
            "order": 10,
            "wa_history_id": H1,
        },
        {
            "code": "S1",
            "title": "Zilvra et Khaetar se disputent l'amulette ; Zilvra l'emporte",
            "start_year": 987,
            "importance": "notable",
            "origin": "canon",
            "summary": "Dispute.",
            "place": "Aterna",
            "chronicle": True,
            "session": 1,
            "day": "J1",
            "order": 90,
        },
        {
            "code": "S2",
            "title": "Poursuite par les veilleurs",
            "start_year": 987,
            "importance": "mineur",
            "origin": "canon",
            "summary": "Fuite.",
            "place": "Aterna",
            "linked": ["S1"],
            "chronicle": True,
            "session": 1,
            "day": "J1",
            "order": 91,
            "wa_history_id": H3,
        },
    ]
    load_seed(db, [validate_event_data(row) for row in rows])
    return {event.code: event for event in db.scalars(select(TimelineEvent)).all()}


def run(db: Session, source: FakeTimelineSource, *, full: bool = False) -> SyncRun:
    result = sync_world(db, source, WORLD, full=full, trigger="cli")
    db.expire_all()
    return result


def by_code(db: Session, code: str) -> TimelineEvent:
    event = db.scalar(select(TimelineEvent).where(TimelineEvent.code == code))
    assert event is not None
    return event


def links_of(db: Session, event: TimelineEvent) -> set[tuple[int, str]]:
    rows = db.scalars(select(TimelineEventLink).where(TimelineEventLink.source_id == event.id))
    return {(row.target_id, row.origin) for row in rows}


def test_parse_history_content_reads_every_field() -> None:
    parsed = parse_history_content(
        content(
            "L'empire possède désormais la totalité de son territoire.",
            "B6",
            place="Europe, Afrique du Nord, Proche-Orient",
            origin="ajout",
            linked="Chute de Kéthisphor ; Grand Triomphe",
            chronicle="séance 2, jour J2",
        )
    )
    assert parsed.summary == "L'empire possède désormais la totalité de son territoire."
    assert parsed.place == "Europe, Afrique du Nord, Proche-Orient"
    assert parsed.origin == "ajout"
    assert parsed.linked_titles == ("Chute de Kéthisphor", "Grand Triomphe")
    assert (parsed.session, parsed.day, parsed.code) == (2, "J2", "B6")


def test_sync_matches_by_history_id_then_code(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    result = run(db, source)
    assert result.status == SyncStatus.SUCCESS
    assert (result.events_created, result.events_removed) == (0, 0)
    assert by_code(db, "S1").wa_history_id == H2
    assert by_code(db, "B1").synced_at is not None
    assert len(db.scalars(select(TimelineEvent)).all()) == 3


def test_sync_updates_content_but_keeps_app_owned_fields(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    event = by_code(db, "B1")
    event.status = "etabli"
    event.visibility = "joueurs"
    event.chronicle = True
    event.session = 4
    event.day = "J2"
    event.sort_order = 42
    event.modified_by_dm = True
    db.commit()
    source.put(
        H1,
        "Fondation d'Aterna",
        "-3",
        content(
            "Une colline en Italie.",
            "B1",
            place="Latium",
            origin="ajout",
            status="à valider",
            chronicle="séance 9, jour J1",
        ),
        ending="2",
        significance=1,
        updated="2026-11-25 09:00:00",
    )
    result = run(db, source)
    assert result.events_updated == 1
    event = by_code(db, "B1")
    assert (event.title, event.start_year, event.end_year) == ("Fondation d'Aterna", -3, 2)
    assert (event.summary, event.place, event.origin) == (
        "Une colline en Italie.",
        "Latium",
        "ajout",
    )
    assert event.importance == "mineur"
    assert (event.status, event.visibility) == ("etabli", "joueurs")
    assert (event.chronicle, event.session, event.day, event.sort_order) == (True, 4, "J2", 42)
    assert event.modified_by_dm


def test_sync_inserts_new_histories_hidden_and_pending(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    source.put(
        H4,
        "Grand Triomphe de l'Unité",
        "347",
        content("Une colonne.", "E-029", origin="ajout", status="établi"),
        significance=3,
    )
    source.put(
        H5,
        "Le troll se réveille",
        "987",
        content("Un grondement.", None, chronicle="séance 2, jour J2"),
    )
    result = run(db, source)
    assert result.events_created == 2
    triumph = by_code(db, "E-029")
    assert (triumph.visibility, triumph.status, triumph.wa_history_id) == ("mj", "a_valider", H4)
    assert (triumph.importance, triumph.origin, triumph.start_year) == ("notable", "ajout", 347)
    assert triumph.sort_order == 11
    troll = db.scalar(select(TimelineEvent).where(TimelineEvent.wa_history_id == H5))
    assert troll.code == "WA-aaaaaaaa"
    assert (troll.visibility, troll.status) == ("mj", "a_valider")
    assert (troll.chronicle, troll.session, troll.day) == (True, 2, "J2")
    assert troll.sort_order == 92
    assert not troll.modified_by_dm


def test_sync_resolves_links_by_title_even_with_separators_in_titles(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    mj_link = TimelineEventLink(
        source_id=by_code(db, "S2").id, target_id=by_code(db, "B1").id, origin="mj"
    )
    db.add(mj_link)
    db.commit()
    run(db, source)
    s2 = by_code(db, "S2")
    assert links_of(db, s2) == {(by_code(db, "S1").id, "wa"), (by_code(db, "B1").id, "mj")}
    source.put(
        H3,
        "Poursuite par les veilleurs",
        "987",
        content("Fuite.", "S2"),
        significance=1,
        updated="2026-11-25 10:00:00",
    )
    run(db, source)
    assert links_of(db, by_code(db, "S2")) == {(by_code(db, "B1").id, "mj")}


def test_sync_marks_removed_histories_and_restores_them(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    run(db, source)
    removed = source.histories.pop(H1)
    result = run(db, source)
    assert result.events_removed == 1
    assert by_code(db, "B1").removed_at is not None
    source.histories[H1] = removed
    run(db, source)
    assert by_code(db, "B1").removed_at is None


def test_removed_events_are_hidden_everywhere(
    db: Session,
    seeded: dict[str, TimelineEvent],
    source: FakeTimelineSource,
    client: TestClient,
) -> None:
    make_user(db, "maitre", Role.DM)
    run(db, source)
    event = by_code(db, "B1")
    event.visibility = "joueurs"
    db.commit()
    del source.histories[H1]
    run(db, source)
    login(client, "maitre")
    assert client.get(f"/mj/chronologie/evenements/{event.id}").status_code == 404
    assert "Création de l&#39;Empire" not in client.get("/mj/chronologie").text


def test_incremental_sync_skips_unchanged_histories(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    run(db, source)
    source.history_calls.clear()
    result = run(db, source)
    assert source.history_calls == []
    assert (result.events_created, result.events_updated) == (0, 0)
    run(db, source, full=True)
    assert sorted(source.history_calls) == [H1, H2, H3]


def test_invalid_histories_are_skipped_without_failing_the_run(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    source.put(H4, "Dates inversées", "50", content("Erreur.", "X-1"), ending="10")
    source.put(H5, "", "60", content("Sans titre.", "X-2"))
    result = run(db, source)
    assert result.status == SyncStatus.SUCCESS
    assert result.events_created == 0
    assert db.scalar(select(TimelineEvent).where(TimelineEvent.code.in_(["X-1", "X-2"]))) is None


def test_sync_without_chronology_timeline_leaves_events_alone(
    db: Session, seeded: dict[str, TimelineEvent], source: FakeTimelineSource
) -> None:
    source.timelines = [{"id": "bbbbbbbb-0000-0000-0000-000000000000", "title": "Autre frise"}]
    result = run(db, source)
    assert result.status == SyncStatus.SUCCESS
    assert all(event.removed_at is None for event in db.scalars(select(TimelineEvent)).all())
    assert source.history_calls == []


def test_timeline_id_setting_skips_the_title_lookup(
    db: Session,
    seeded: dict[str, TimelineEvent],
    source: FakeTimelineSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "wa_timeline_id", TIMELINE)
    source.timelines = []
    result = run(db, source)
    assert result.status == SyncStatus.SUCCESS
    assert by_code(db, "S1").wa_history_id == H2
