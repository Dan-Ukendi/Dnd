from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CharacterEvent,
    CharacterProfile,
    CharacterRelation,
    LoreEntry,
    Role,
    TimelineEvent,
)
from tests.factories import make_character, make_user

OLD_RUMOUR = "Elle sait qui a vraiment lancé la rumeur de 971 et a peur de le dire."
NEW_RUMOUR = "Elle sait qui a vraiment lancé la rumeur de 813 et a peur de le dire."


@pytest.fixture
def alembic_config() -> Iterator[Config]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    yield config
    command.upgrade(config, "head")


@pytest.fixture
def legacy(db: Session, alembic_config: Config) -> dict[str, int]:
    db.rollback()
    command.downgrade(alembic_config, "0015")

    def event(code: str, start: int, end: int | None = None, **fields: object) -> TimelineEvent:
        values = {"importance": "notable", "origin": "canon", **fields}
        row = TimelineEvent(
            code=code, title=f"Titre {code}", start_year=start, end_year=end, **values
        )
        db.add(row)
        return row

    event("A4", -485, 987)
    event("B7", 749)
    event("B8", 751)
    event("C1", 752)
    event("C4", 754)
    event("C5", 758)
    event("C7", 781)
    event("C15", 956)
    event("C17", 970, modified_by_dm=True)
    event("C23", 987, 987)
    event("S1", 987, summary="Au marché noir, en 987.")
    event("MJ-1", 987, origin="ajout")
    player = make_user(db, "joueur", Role.PLAYER)
    khaetar = make_character(db, "Khaetar Voss", player)
    db.add(CharacterProfile(character_id=khaetar.id, backstory="Né vers l'an 956, à 31 ans."))
    for position, (title, year) in enumerate(
        [("Naissance", 956), ("Le lynchage", 971), ("Dispute", 987), ("Non daté", None)]
    ):
        db.add(
            CharacterEvent(
                character_id=khaetar.id,
                position=position,
                title=title,
                world_year=year,
            )
        )
    db.add(
        CharacterRelation(
            character_id=khaetar.id,
            position=0,
            category="ennemis",
            name="Altan",
            role="Notable de quartier, à la tête de la foule en 971 (environ 58 ans)",
            gm_notes=OLD_RUMOUR,
            text="Il garde 971 pièces dans un coffre.",
        )
    )
    db.add(LoreEntry(title="Événements de la session 1", element_type="artefact", start_year=987))
    db.add(LoreEntry(title="La révolte", element_type="evenement", start_year=749))
    db.commit()
    return {"khaetar": khaetar.id}


def years(db: Session) -> dict[str, tuple[int, int | None]]:
    db.expire_all()
    rows = db.scalars(select(TimelineEvent)).all()
    return {row.code: (row.start_year, row.end_year) for row in rows}


def snapshot(db: Session) -> tuple[object, ...]:
    db.expire_all()
    events = {row.title: row.world_year for row in db.scalars(select(CharacterEvent)).all()}
    relation = db.scalars(select(CharacterRelation)).one()
    lore = {row.title: row.start_year for row in db.scalars(select(LoreEntry)).all()}
    profile = db.scalars(select(CharacterProfile)).one()
    summary = db.scalars(select(TimelineEvent.summary).where(TimelineEvent.code == "S1")).one()
    return (
        years(db),
        events,
        (relation.role, relation.gm_notes, relation.text),
        lore,
        profile.backstory,
        summary,
    )


def test_upgrade_moves_the_present_to_829(
    db: Session, legacy: dict[str, int], alembic_config: Config
) -> None:
    command.upgrade(alembic_config, "head")
    shifted, events, relation, lore, backstory, summary = snapshot(db)
    assert shifted == {
        "A4": (-485, 829),
        "B7": (749, None),
        "B8": (757, 760),
        "C1": (757, 760),
        "C4": (754, None),
        "C5": (758, None),
        "C7": (781, None),
        "C15": (798, None),
        "C17": (970, None),
        "C23": (829, 829),
        "S1": (829, None),
        "MJ-1": (987, None),
    }
    assert events == {"Naissance": 798, "Le lynchage": 813, "Dispute": 829, "Non daté": None}
    assert relation == (
        "Notable de quartier, à la tête de la foule en 813 (environ 58 ans)",
        NEW_RUMOUR,
        "Il garde 971 pièces dans un coffre.",
    )
    assert lore == {"Événements de la session 1": 829, "La révolte": 749}
    assert backstory == "Né vers l'an 798, à 31 ans."
    assert summary == "Au marché noir, en 829."


def test_downgrade_restores_the_old_years(
    db: Session, legacy: dict[str, int], alembic_config: Config
) -> None:
    before = snapshot(db)
    command.upgrade(alembic_config, "head")
    db.rollback()
    command.downgrade(alembic_config, "0015")
    assert snapshot(db) == before


def test_migration_is_idempotent(
    db: Session, legacy: dict[str, int], alembic_config: Config
) -> None:
    command.upgrade(alembic_config, "head")
    first = snapshot(db)
    db.rollback()
    command.downgrade(alembic_config, "0015")
    command.upgrade(alembic_config, "head")
    assert snapshot(db) == first
