import copy
import html
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Character,
    CharacterAction,
    CharacterArticle,
    CharacterEvent,
    CharacterEventPerson,
    CharacterProfile,
    Role,
    WaArticle,
    utcnow,
)
from app.services.access import dm_render_context
from app.services.dossiers import (
    action_family,
    build_dossier,
    import_dossier,
    normalize_action_kind,
)
from tests.factories import login, make_article, make_character, make_user

SOURCES = Path(__file__).resolve().parent.parent / "source" / "dossiers"
PRESENT = 829
ZILVRA_BIRTH = 808

ACTIONS: dict[str, Any] = {
    "character": "Khaetar Voss",
    "history": {
        "events": [
            {"title": "Naissance", "year": 798, "approximate": True},
            {
                "title": "Le lynchage et le pacte",
                "year": 813,
                "age": 15,
                "people": [{"name": "La voix", "role": "Patron"}],
                "gm_notes": "Le patron est un secret.",
            },
        ]
    },
    "actions": [
        {"type": "action", "name": "Fouet", "category": "Attaque, arme de guerre", "hit": "+5"},
        {"type": "action", "name": "Arme de pacte", "category": "Pacte de la lame", "hit": "+5"},
        {
            "type": "action",
            "name": "Fiole d'acide",
            "category": "Objet, remplace une attaque de l'action Attaquer",
            "save": "DEX 13",
        },
        {"type": "action", "name": "Explosion occulte", "category": "Sort mineur", "hit": "+5"},
        {"type": "action", "name": "Soins", "category": "Sort niv. 1"},
        {"type": "action", "name": "Aspect redoutable", "category": "Conduit divin"},
        {"type": "bonus", "name": "Châtiment divin", "category": "Sort niv. 1"},
        {"type": "bonus", "name": "Imposition des mains", "category": "Paladin"},
        {"type": "bonus", "name": "Attaque à deux armes", "category": "Attaque", "hit": "+5"},
        {"type": "bonus", "name": "Potion de soin", "category": "Objet"},
        {"type": "reaction", "name": "Bouclier", "category": "Sort niv. 1"},
        {"type": "reaction", "name": "Attaque d'opportunité", "category": "Attaque", "hit": "+5"},
        {"type": "free", "name": "Résistance au feu", "category": "Héritage infernal"},
    ],
}


@dataclass
class Table:
    keator: Character
    zilvra: Character
    khaetar: WaArticle


@pytest.fixture
def table(db: Session) -> Table:
    make_user(db, "maitre", Role.DM)
    khaetar = make_article(db, "Khaetar Voss")
    return Table(
        make_character(db, "Khaetar Voss", make_user(db, "keator"), khaetar),
        make_character(db, "Zilvra Vhaelor", make_user(db, "zilvra"), make_article(db, "Zilvra")),
        khaetar,
    )


def _layout(character: Character, db: Session) -> list[tuple[str, Any]]:
    dossier = build_dossier(character, dm_render_context(db), lambda article: None)
    return [
        (
            group.label,
            [(sub.label, [action.name for action in sub.actions]) for sub in group.subgroups]
            or [action.name for action in group.actions],
        )
        for group in dossier.action_groups
    ]


def _actions_block(page: str) -> str:
    match = re.search(r'<div class="action-groups".*?</details>\s*</div>\s*</section>', page, re.S)
    assert match, "the actions dropdowns are missing"
    return match.group(0)


@pytest.mark.parametrize(
    ("category", "hit", "family"),
    [
        ("Sort mineur, Explosion agonisante", "+5", "spell"),
        ("Sort niv. 1 lancé au niv. 2, Lame maudite", None, "spell"),
        ("Sorts de lignage", None, "spell"),
        ("Attaque, arme courante", "+5", "attack"),
        ("Attaque", None, "attack"),
        ("Pacte de la lame, forme à deux mains", "+5", "attack"),
        ("Objet, remplace une attaque de l'action Attaquer", None, "attack"),
        ("Conduit divin, Oathbreaker", None, "other"),
        ("Lame maudite", None, "other"),
        ("Sortilège de famille", None, "other"),
        (None, None, "other"),
    ],
)
def test_action_family(category: str | None, hit: str | None, family: str) -> None:
    assert action_family(category, hit) == family


def test_actions_are_split_into_dropdown_groups(db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(ACTIONS))
    assert _layout(character, db) == [
        (
            "Action",
            [
                ("Attaques", ["Fouet", "Arme de pacte", "Fiole d'acide"]),
                ("Sorts", ["Explosion occulte", "Soins"]),
                ("Divers", ["Aspect redoutable"]),
            ],
        ),
        (
            "Action bonus",
            [
                ("Sorts", ["Châtiment divin"]),
                ("Divers", ["Imposition des mains", "Attaque à deux armes", "Potion de soin"]),
            ],
        ),
        ("Réaction", ["Bouclier", "Attaque d'opportunité"]),
        ("Caractéristique", ["Résistance au feu"]),
    ]


def test_empty_subgroups_are_not_shown(db: Session, table: Table) -> None:
    data = {
        "character": "Khaetar Voss",
        "actions": [
            {"type": "action", "name": "Hache", "category": "Arme", "hit": "+5"},
            {"type": "bonus", "name": "Frappe radieuse", "category": "Sort niv. 2"},
        ],
    }
    character = import_dossier(db, data)
    assert _layout(character, db) == [
        ("Action", [("Attaques", ["Hache"])]),
        ("Action bonus", [("Sorts", ["Frappe radieuse"])]),
    ]


def test_caracteristique_is_an_action_type() -> None:
    assert normalize_action_kind("Caractéristique") == "free"
    assert normalize_action_kind("sans action") == "free"


@pytest.mark.parametrize("viewer", ["keator", "maitre"])
def test_actions_tab_renders_nested_dropdowns(
    client: TestClient, db: Session, table: Table, viewer: str
) -> None:
    import_dossier(db, copy.deepcopy(ACTIONS))
    login(client, viewer)
    path = "/joueur" if viewer == "keator" else f"/mj/articles/{table.khaetar.wa_id}"
    page = client.get(path).text
    block = _actions_block(page)
    groups = re.findall(r'<details class="action-group action-group--(\w+)"', block)
    assert groups == ["action", "bonus", "reaction", "free"]
    assert re.findall(r'data-action-subgroup="(\w+)"', block) == [
        "attack",
        "spell",
        "other",
        "spell",
        "other",
    ]
    titles = re.findall(r'class="action-(?:sub)?group__title">([^<]+)<', block)
    assert titles == [
        "Action",
        "Attaques",
        "Sorts",
        "Divers",
        "Action bonus",
        "Sorts",
        "Divers",
        "Réaction",
        "Caractéristique",
    ]
    assert "Sans action" not in page
    assert "<summary" in block and " open" not in block
    assert "style=" not in block and "<script" not in block
    assert 'data-disclosure-all="open"' in page


def test_players_never_get_dm_only_content(
    client: TestClient, other_client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(ACTIONS))
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "keator")
    own = html.unescape(client.get("/joueur").text)
    assert "Fiole d'acide" in own and "Le lynchage et le pacte" in own
    assert "Le patron est un secret." not in own
    assert "Notes du MJ" not in own
    login(other_client, "zilvra")
    shared = html.unescape(other_client.get(f"/joueur/articles/{table.khaetar.wa_id}").text)
    assert "Fiole d'acide" not in shared
    assert "Le lynchage et le pacte" not in shared


def _snapshot(db: Session, character_id: int) -> tuple[Any, ...]:
    db.expire_all()
    character = db.get(Character, character_id)
    events = [
        (
            event.position,
            event.title,
            event.session,
            event.world_year,
            event.age,
            event.date_approximate,
            [(person.name, person.role) for person in event.people],
        )
        for event in character.events
    ]
    actions = [(action.position, action.kind, action.name) for action in character.actions]
    counts = tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (CharacterEvent, CharacterEventPerson, CharacterAction)
    )
    return events, actions, counts


def test_import_is_idempotent(db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(ACTIONS))
    first = _snapshot(db, table.keator.id)
    import_dossier(db, copy.deepcopy(ACTIONS))
    assert _snapshot(db, table.keator.id) == first
    assert first[2] == (2, 1, len(ACTIONS["actions"]))


# Migration 0023


@pytest.fixture
def alembic_config(db: Session) -> Iterator[Config]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    yield config
    db.rollback()
    command.upgrade(config, "head")


KHAETAR_SESSION = (
    "Dispute pour l'amulette au marché noir",
    "Refuge au Troll qui Rit",
    "Licence de rang B et contrat de Sophie",
    "À la Lux Victrix d'Arès",
    "Bob chassé de la taverne",
)
ZILVRA_SESSION = (
    "L'amulette disputée au marché noir",
    "L'accord au Troll qui Rit",
    "La bourse volée par Bob, puis reprise",
    "La licence de rang B",
    "Le coffre de Sophie",
    "Le contrat signé « Maelys »",
)
ZILVRA_BACKSTORY = (
    "Naissance dans la Maison Veldrynn",
    "Le cercle de fidèles",
    "L'entrée au couvent de Lolth",
    "La rupture du serment",
    "Le départ du couvent et de la baronnie",
    "La remontée à la surface",
)


def _events(db: Session, character_id: int) -> dict[str, tuple[int | None, int | None, bool]]:
    db.expire_all()
    rows = db.scalars(select(CharacterEvent).where(CharacterEvent.character_id == character_id))
    return {row.title: (row.world_year, row.age, row.date_approximate) for row in rows}


def test_migration_drops_session_events_and_dates_zilvra(
    db: Session, table: Table, alembic_config: Config
) -> None:
    db.rollback()
    command.downgrade(alembic_config, "0022")
    edited = utcnow()
    db.add(CharacterProfile(character_id=table.zilvra.id, events_edited_at=edited))
    rows: list[CharacterEvent] = [
        CharacterEvent(character_id=table.keator.id, position=0, title="Naissance", world_year=798),
        CharacterEvent(
            character_id=table.keator.id,
            position=1,
            title="Une séance marquante",
            session="Séance 2",
            world_year=829,
        ),
    ]
    for offset, title in enumerate(KHAETAR_SESSION, start=2):
        event = CharacterEvent(
            character_id=table.keator.id,
            position=offset,
            title=title,
            session="Séance 1, jour 1",
            world_year=829,
        )
        event.people = [CharacterEventPerson(position=0, name="Zilvra", role="Alliée")]
        rows.append(event)
    for position, title in enumerate(ZILVRA_BACKSTORY):
        year = 815 if title == "Le cercle de fidèles" else None
        rows.append(
            CharacterEvent(
                character_id=table.zilvra.id, position=position, title=title, world_year=year
            )
        )
    for offset, title in enumerate(ZILVRA_SESSION, start=len(ZILVRA_BACKSTORY)):
        rows.append(
            CharacterEvent(
                character_id=table.zilvra.id,
                position=offset,
                title=title,
                session="Séance 1, jour 2",
                world_year=829,
            )
        )
    db.add_all(rows)
    db.commit()

    command.upgrade(alembic_config, "0023")
    assert set(_events(db, table.keator.id)) == {"Naissance", "Une séance marquante"}
    assert db.scalar(select(func.count()).select_from(CharacterEventPerson)) == 0
    zilvra = _events(db, table.zilvra.id)
    assert zilvra == {
        "Naissance dans la Maison Veldrynn": (808, None, True),
        "Le cercle de fidèles": (815, None, False),
        "L'entrée au couvent de Lolth": (822, 14, True),
        "La rupture du serment": (826, 18, True),
        "Le départ du couvent et de la baronnie": (826, 18, True),
        "La remontée à la surface": (827, 19, True),
    }
    for year, age, _ in zilvra.values():
        if age is not None:
            assert year - age == ZILVRA_BIRTH
    assert PRESENT - ZILVRA_BIRTH == 21
    profile = db.get(CharacterProfile, table.zilvra.id)
    assert profile is not None and profile.events_edited_at == edited

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0022")
    assert _events(db, table.zilvra.id) == {
        title: (815, None, False) if title == "Le cercle de fidèles" else (None, None, False)
        for title in ZILVRA_BACKSTORY
    }


# The DM's own dossier files (git-ignored): checked only where they are present.

needs_sources = pytest.mark.skipif(
    not (SOURCES / "khaetar.toml").is_file() or not (SOURCES / "zilvra.toml").is_file(),
    reason="source/dossiers is not available",
)


def _source(name: str) -> dict[str, Any]:
    return tomllib.loads((SOURCES / f"{name}.toml").read_text(encoding="utf-8"))


def _linked_articles(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if "article" in value or "article_id" in value:
            yield value
        for item in value.values():
            yield from _linked_articles(item)
    elif isinstance(value, list):
        for item in value:
            yield from _linked_articles(item)


def _stub_articles(db: Session, data: dict[str, Any]) -> None:
    for entry in _linked_articles(data):
        wa_id = entry.get("article_id")
        title = entry.get("article") or entry.get("name")
        if wa_id and db.get(WaArticle, wa_id) is None:
            article = make_article(db, title)
            db.execute(
                update(WaArticle).where(WaArticle.wa_id == article.wa_id).values(wa_id=wa_id)
            )
            db.commit()
        elif not wa_id and db.scalar(select(WaArticle).where(WaArticle.title == title)) is None:
            make_article(db, title)


@needs_sources
@pytest.mark.parametrize("name", ["khaetar", "zilvra"])
def test_source_notable_events_hold_no_session_events(name: str) -> None:
    events = _source(name)["history"]["events"]
    assert events
    assert [event["title"] for event in events if event.get("session")] == []
    assert all(event.get("year") != PRESENT for event in events)


@needs_sources
def test_source_zilvra_dates_follow_her_age() -> None:
    events = _source("zilvra")["history"]["events"]
    assert [event["title"] for event in events] == list(ZILVRA_BACKSTORY)
    years = [event["year"] for event in events]
    assert years == sorted(years)
    assert years[0] == ZILVRA_BIRTH and PRESENT - years[0] == 21
    assert all(event.get("approximate") is True for event in events)
    for event in events:
        if "age" in event:
            assert event["year"] - event["age"] == ZILVRA_BIRTH
    departure = next(event for event in events if event["title"].startswith("Le départ"))
    assert departure["age"] == 18 and departure["year"] == 826
    assert all(event["year"] < PRESENT for event in events)


@needs_sources
def test_source_dossiers_import_into_dropdowns_without_leaks(
    client: TestClient, other_client: TestClient, db: Session, table: Table
) -> None:
    for name, character in (("khaetar", table.keator), ("zilvra", table.zilvra)):
        data = _source(name)
        _stub_articles(db, data)
        import_dossier(db, copy.deepcopy(data))
        first = _snapshot(db, character.id)
        import_dossier(db, copy.deepcopy(data))
        assert _snapshot(db, character.id) == first
        db.expire_all()
        dossier = build_dossier(
            db.get(Character, character.id), dm_render_context(db), lambda article: None
        )
        assert [event.session for event in dossier.events if event.session] == []
        listed = 0
        for group in dossier.action_groups:
            if group.key in ("action", "bonus"):
                assert group.subgroups and all(sub.actions for sub in group.subgroups)
                assert sum(len(sub.actions) for sub in group.subgroups) == len(group.actions)
                for sub in group.subgroups:
                    spells = [action.category.startswith("Sort") for action in sub.actions]
                    assert all(spells) if sub.key == "spell" else not any(spells)
            else:
                assert group.subgroups == []
            listed += len(group.actions)
        assert listed == len(data["actions"])
        assert "Caractéristique" in [group.label for group in dossier.action_groups]
    secrets = [
        entry["gm_notes"]
        for entry in _source("khaetar")["relations"] + _source("zilvra").get("relations", [])
        if entry.get("gm_notes")
    ]
    for viewer, login_name in ((client, "keator"), (other_client, "zilvra")):
        login(viewer, login_name)
        page = html.unescape(viewer.get("/joueur").text)
        assert "Notes du MJ" not in page
        for secret in secrets:
            assert secret.split(".")[0] not in page
