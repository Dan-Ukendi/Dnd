from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TimelineEvent, TimelineEventFiche, TimelineEventLink

INVENTED = [f"E-{number:03d}" for number in range(1, 54)]


@pytest.fixture
def rerun_migration(db: Session) -> Iterator[object]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False

    def rerun() -> None:
        db.rollback()
        command.downgrade(config, "0008")
        command.upgrade(config, "head")

    yield rerun
    command.upgrade(config, "head")


def make_event(db: Session, code: str, **fields: object) -> TimelineEvent:
    values = {
        "title": f"Titre {code}",
        "summary": f"Résumé {code}.",
        "start_year": 100,
        "importance": "notable",
        "origin": "canon",
        **fields,
    }
    event = TimelineEvent(code=code, **values)
    db.add(event)
    return event


def link(db: Session, source: TimelineEvent, target: TimelineEvent) -> None:
    db.add(TimelineEventLink(source_id=source.id, target_id=target.id, origin="wa"))


@pytest.fixture
def legacy(db: Session) -> dict[str, TimelineEvent]:
    events = {code: make_event(db, code, origin="ajout") for code in INVENTED}
    events["E-007"].modified_by_dm = True
    events["A3"] = make_event(db, "A3")
    events["B3"] = make_event(db, "B3", place="Ancienne capitale")
    events["B7"] = make_event(db, "B7")
    events["B8"] = make_event(db, "B8", summary="Ancien résumé.")
    events["C15"] = make_event(db, "C15", summary="Naissance de Khaetar, vers 956.")
    events["MJ-1"] = make_event(db, "MJ-1", origin="ajout", modified_by_dm=True)
    db.flush()
    link(db, events["A3"], events["E-005"])
    link(db, events["B8"], events["E-046"])
    link(db, events["E-005"], events["A3"])
    link(db, events["E-007"], events["B7"])
    link(db, events["MJ-1"], events["A3"])
    db.add(TimelineEventFiche(event_id=events["E-001"].id, title="Fiche supprimée"))
    db.add(TimelineEventFiche(event_id=events["E-007"].id, title="Fiche gardée"))
    db.commit()
    return events


def state(db: Session) -> tuple[object, ...]:
    db.expire_all()
    codes = set(db.scalars(select(TimelineEvent.code)).all())
    links = set(
        db.execute(
            select(TimelineEvent.code, TimelineEventLink.target_id).join(
                TimelineEventLink, TimelineEventLink.source_id == TimelineEvent.id
            )
        ).all()
    )
    fiches = set(db.scalars(select(TimelineEventFiche.title)).all())
    return codes, links, fiches


def by_code(db: Session, code: str) -> TimelineEvent:
    return db.scalars(select(TimelineEvent).where(TimelineEvent.code == code)).one()


def test_migration_removes_invented_events_and_fixes_kept_ones(
    db: Session, legacy: dict[str, TimelineEvent], rerun_migration
) -> None:
    ids = {code: event.id for code, event in legacy.items()}
    rerun_migration()
    codes, links, fiches = state(db)
    assert codes == {"E-007", "A3", "B3", "B7", "B8", "C15", "MJ-1"}
    assert links == {("B8", ids["B7"]), ("E-007", ids["B7"]), ("MJ-1", ids["A3"])}
    assert fiches == {"Fiche gardée"}
    assert by_code(db, "B3").place == "Tout l'empire"
    assert by_code(db, "B8").summary == (
        "L'empire se partage entre le Khaanat des Cinq Lances, "
        "l'Héliopolis des Harmoniens et le Royaume de Sanguis Coronae."
    )
    assert by_code(db, "C15").summary == "Naissance de Khaetar, tiefling orphelin."
    kept = by_code(db, "E-007")
    assert (kept.title, kept.origin, kept.modified_by_dm) == ("Titre E-007", "ajout", True)
    mine = by_code(db, "MJ-1")
    assert (mine.title, mine.summary, mine.origin) == ("Titre MJ-1", "Résumé MJ-1.", "ajout")


def test_migration_is_idempotent(
    db: Session, legacy: dict[str, TimelineEvent], rerun_migration
) -> None:
    rerun_migration()
    first = state(db)
    rerun_migration()
    assert state(db) == first
    assert db.scalar(select(func.count()).select_from(TimelineEventLink)) == 3


def test_migration_keeps_corrections_made_by_the_dm(
    db: Session, legacy: dict[str, TimelineEvent], rerun_migration
) -> None:
    legacy["B3"].modified_by_dm = True
    db.commit()
    rerun_migration()
    assert by_code(db, "B3").place == "Ancienne capitale"
