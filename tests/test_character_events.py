import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.models import (
    Character,
    CharacterArticle,
    CharacterEvent,
    CharacterEventOrganization,
    CharacterEventPerson,
    Role,
    TimelineEvent,
    WaArticle,
)
from app.services.access import dm_render_context
from app.services.dossiers import (
    DossierError,
    build_dossier,
    event_age_label,
    event_date_label,
    import_dossier,
)
from tests.factories import login, make_article, make_character, make_user, post_form

DOSSIER: dict[str, Any] = {
    "character": "Khaetar Voss",
    "history": {
        "backstory": "Je m'appelle Khaetar Voss.",
        "events": [
            {
                "session": "Séance 1, jour 2",
                "title": "Au Temple Blanc d'Arès",
                "year": 987,
                "place": "Temple Blanc d'Arès",
                "text": "Un vieux prêtre le prend à part.",
                "people": [
                    {"name": "Kratéros", "role": "Allié", "article": "Kratéros"},
                    {"name": "La voix", "role": "Patron"},
                ],
                "organizations": [{"name": "La Guilde des Aventuriers", "article": "La Guilde"}],
                "consequences": "Le Gage d'Arès.",
                "gm_notes": "Évêque en secret.",
            },
            {"title": "Il grandit en volant", "place": "la cour de boulangerie"},
            {
                "title": "Naissance",
                "year": 956,
                "approximate": True,
                "place_article": "Khüchniin Khöndii",
            },
            {"title": "Le lynchage et le pacte", "year": 971, "age": 15},
            {"title": "Le sang de Zariel", "year": 968, "age": 12, "approximate": True},
            {"title": "Un âge sans date", "age": 8},
        ],
    },
}


@dataclass
class Table:
    keator: Character
    zilvra: Character
    khaetar: WaArticle
    krateros: WaArticle
    city: WaArticle
    guild: WaArticle


@pytest.fixture
def table(db: Session) -> Table:
    make_user(db, "maitre", Role.DM)
    keator = make_user(db, "keator")
    zilvra = make_user(db, "zilvra")
    khaetar = make_article(db, "Khaetar Voss", content="[p]Occultiste.[/p]")
    return Table(
        make_character(db, "Khaetar Voss", keator, khaetar),
        make_character(db, "Zilvra Vhaelor", zilvra, make_article(db, "Zilvra")),
        khaetar,
        make_article(db, "Kratéros"),
        make_article(db, "Khüchniin Khöndii", template_type="settlement"),
        make_article(db, "La Guilde", template_type="organization"),
    )


def _import(db: Session) -> Character:
    return import_dossier(db, copy.deepcopy(DOSSIER), "khaetar.toml")


def _event(character: Character, title: str) -> CharacterEvent:
    return next(event for event in character.events if event.title == title)


def test_import_stores_every_event_field(db: Session, table: Table) -> None:
    character = _import(db)
    temple = _event(character, "Au Temple Blanc d'Arès")
    assert (temple.world_year, temple.age, temple.date_approximate) == (987, None, False)
    assert temple.session == "Séance 1, jour 2"
    assert temple.place_name == "Temple Blanc d'Arès"
    assert temple.place_article_id is None
    assert [(p.name, p.role, p.wa_article_id) for p in temple.people] == [
        ("Kratéros", "Allié", table.krateros.wa_id),
        ("La voix", "Patron", None),
    ]
    assert [(o.name, o.wa_article_id) for o in temple.organizations] == [
        ("La Guilde des Aventuriers", table.guild.wa_id)
    ]
    assert temple.consequences == "Le Gage d'Arès."
    assert temple.gm_notes == "Évêque en secret."
    birth = _event(character, "Naissance")
    assert birth.place_name == "Khüchniin Khöndii"
    assert birth.place_article_id == table.city.wa_id
    assert birth.date_approximate is True
    childhood = _event(character, "Il grandit en volant")
    assert (childhood.world_year, childhood.age, childhood.place_article_id) == (None, None, None)
    assert childhood.consequences == "" and childhood.people == []


def test_events_are_sorted_by_date_with_undated_events_apart(db: Session, table: Table) -> None:
    dossier = build_dossier(_import(db), dm_render_context(db), lambda article: None)
    assert [event.title for event in dossier.dated_events] == [
        "Naissance",
        "Le sang de Zariel",
        "Le lynchage et le pacte",
        "Au Temple Blanc d'Arès",
    ]
    assert [event.title for event in dossier.undated_events] == [
        "Il grandit en volant",
        "Un âge sans date",
    ]
    whens = {event.title: event.when for event in dossier.events}
    assert whens["Naissance"] == "Vers l'an 956"
    assert whens["Le sang de Zariel"] == "Vers l'an 968 · vers 12 ans"
    assert whens["Le lynchage et le pacte"] == "An 971 · 15 ans"
    assert whens["Un âge sans date"] == "8 ans"
    assert whens["Il grandit en volant"] is None


def test_date_labels() -> None:
    assert event_date_label(None, True) is None
    assert event_date_label(0, False) == "An 0"
    assert event_date_label(-40, False) == "40 av. É."
    assert event_date_label(-40, True) == "Vers 40 av. É."
    assert event_age_label(1, False, alone=True) == "1 an"
    assert event_age_label(12, True, alone=True) == "Vers 12 ans"


@pytest.mark.parametrize(
    "event",
    [
        {"title": "X", "people": [{"name": "Y", "article": "Fiche inconnue"}]},
        {"title": "X", "people": [{"role": "Allié"}]},
        {"title": "X", "organizations": [{"name": "Z", "article": "Inconnue"}]},
        {"title": "X", "place_article": "Lieu inconnu"},
        {"title": "X", "year": "vers 956"},
        {"title": "X", "year": 956.5},
        {"title": "X", "age": -1},
        {"title": "X", "approximate": "oui"},
        {"title": "X", "people": "Kratéros"},
        {"year": 987},
    ],
)
def test_invalid_events_are_rejected(db: Session, table: Table, event: dict[str, Any]) -> None:
    with pytest.raises(DossierError):
        import_dossier(db, {"character": "Khaetar Voss", "history": {"events": [event]}})


def test_owner_reads_his_events_with_links_to_accessible_fiches_only(
    client: TestClient, db: Session, table: Table
) -> None:
    _import(db)
    db.add(CharacterArticle(character_id=table.keator.id, article_id=table.krateros.wa_id))
    db.commit()
    login(client, "keator")
    for path in ("/joueur", f"/joueur/articles/{table.khaetar.wa_id}"):
        page = client.get(path).text
        assert ">Récit<" in page
        assert "Événements marquants" in page
        assert "appelle Khaetar Voss." in page
        assert "Au Temple Blanc d&#39;Arès" in page
        assert "Le Gage d" in page
        assert f'href="/joueur/articles/{table.krateros.wa_id}"' in page
        assert f'href="/joueur/articles/{table.guild.wa_id}"' not in page
        assert f'href="/joueur/articles/{table.city.wa_id}"' not in page
        assert "La Guilde des Aventuriers" in page
        assert "Évêque en secret." not in page
        assert "Notes du MJ" not in page
        assert "/evenements" not in page
        assert "person_article" not in page
        assert "Ajouter un événement" not in page


def test_other_players_never_see_the_events(client: TestClient, db: Session, table: Table) -> None:
    _import(db)
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "zilvra")
    for path in ("/joueur", f"/joueur/articles/{table.khaetar.wa_id}"):
        page = client.get(path).text
        assert "Au Temple Blanc" not in page
        assert "Un vieux prêtre le prend à part." not in page
        assert "Évêque en secret." not in page
        assert "evenement-" not in page


def test_dm_sees_every_event_with_gm_notes_and_forms(
    client: TestClient, db: Session, table: Table
) -> None:
    character = _import(db)
    temple = _event(character, "Au Temple Blanc d'Arès")
    login(client, "maitre")
    page = client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert f'id="evenement-{temple.id}"' in page
    assert "Évêque en secret." in page
    assert f'href="/mj/articles/{table.krateros.wa_id}"' in page
    assert f'href="/mj/articles/{table.city.wa_id}"' in page
    assert f'action="/mj/personnages/{table.keator.id}/evenements/{temple.id}"' in page
    assert f'action="/mj/personnages/{table.keator.id}/evenements"' in page
    assert ">Frise<" in page and ">Non datés<" in page
    zilvra_page = client.get(f"/mj/articles/{table.zilvra.main_article_id}").text
    assert "Aucun événement marquant" in zilvra_page


def _event_form(**values: Any) -> dict[str, Any]:
    return {
        "title": "La rencontre",
        "session": "",
        "year": "",
        "age": "",
        "place": "",
        "place_article": "",
        "text": "",
        "consequences": "",
        "gm_notes": "",
        **values,
    }


def test_dm_creates_edits_and_deletes_an_event(
    client: TestClient, db: Session, table: Table
) -> None:
    login(client, "maitre")
    page = f"/mj/articles/{table.khaetar.wa_id}"
    response = post_form(
        client,
        f"/mj/personnages/{table.keator.id}/evenements",
        _event_form(
            year="987",
            age="31",
            approximate="1",
            place_article="Khüchniin Khöndii",
            person_name=["", "Bob", ""],
            person_role=["", "Adversaire", ""],
            person_article=["Kratéros", "", ""],
            organization_name=["", ""],
            organization_article=["La Guilde", ""],
            text="Ligne un.\r\n\r\nLigne deux.",
            gm_notes="Secret.",
        ),
        page,
    )
    assert response.status_code == 303
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert character.profile.events_edited_at is not None
    event = character.events[0]
    assert response.headers["location"] == f"{page}#evenement-{event.id}"
    assert (event.world_year, event.age, event.date_approximate) == (987, 31, True)
    assert (event.place_name, event.place_article_id) == ("Khüchniin Khöndii", table.city.wa_id)
    assert [(p.name, p.role, p.wa_article_id) for p in event.people] == [
        ("Kratéros", None, table.krateros.wa_id),
        ("Bob", "Adversaire", None),
    ]
    assert [(o.name, o.wa_article_id) for o in event.organizations] == [
        ("La Guilde", table.guild.wa_id)
    ]
    assert event.text == "Ligne un.\n\nLigne deux."
    assert "Événements modifiés dans l'application" in client.get(page).text

    response = post_form(
        client,
        f"/mj/personnages/{table.keator.id}/evenements/{event.id}",
        _event_form(title="La rencontre, revue", person_name=["Gerel"], person_role=["Témoin"]),
        page,
    )
    assert response.status_code == 303
    db.expire_all()
    event = db.get(CharacterEvent, event.id)
    assert event.title == "La rencontre, revue"
    assert (event.world_year, event.age, event.date_approximate) == (None, None, False)
    assert event.place_name is None
    assert [(p.name, p.role) for p in event.people] == [("Gerel", "Témoin")]
    assert event.organizations == []
    assert event.gm_notes == ""
    assert db.scalar(select(func.count()).select_from(CharacterEventPerson)) == 1

    response = post_form(
        client, f"/mj/personnages/{table.keator.id}/evenements/{event.id}/supprimer", {}, page
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{page}#evenements"
    assert db.scalar(select(func.count()).select_from(CharacterEvent)) == 0
    assert db.scalar(select(func.count()).select_from(CharacterEventPerson)) == 0


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"title": " "}, "le titre est obligatoire"),
        ({"year": "vers 956"}, "l&#39;année doit être un nombre entier"),
        ({"age": "-3"}, "l&#39;âge doit être un nombre entier positif"),
        ({"place_article": "Nulle part"}, "aucune fiche World Anvil"),
        ({"person_role": ["Allié"]}, "chaque personne doit avoir un nom"),
        ({"person_name": ["X"], "person_article": ["Inconnu"]}, "chaque personne doit avoir"),
        ({"organization_article": ["Inconnue"]}, "chaque organisation doit avoir"),
    ],
)
def test_invalid_event_edits_are_refused(
    client: TestClient, db: Session, table: Table, values: dict[str, Any], message: str
) -> None:
    character = _import(db)
    event_id = _event(character, "Naissance").id
    login(client, "maitre")
    page = f"/mj/articles/{table.khaetar.wa_id}"
    post_form(
        client,
        f"/mj/personnages/{table.keator.id}/evenements/{event_id}",
        _event_form(**values),
        page,
    )
    assert f"Événement non enregistré : {message}" in client.get(page).text
    db.expire_all()
    assert db.get(CharacterEvent, event_id).title == "Naissance"
    assert db.get(Character, table.keator.id).profile.events_edited_at is None


def test_event_routes_are_dm_only(client: TestClient, db: Session, table: Table) -> None:
    character = _import(db)
    event_id = character.events[0].id
    base = f"/mj/personnages/{table.keator.id}/evenements"
    login(client, "keator")
    for path in (base, f"{base}/{event_id}", f"{base}/{event_id}/supprimer"):
        response = post_form(client, path, _event_form(title="Pirate"), "/joueur")
        assert response.status_code == 404
    db.expire_all()
    assert db.get(CharacterEvent, event_id).title == "Au Temple Blanc d'Arès"
    assert db.scalar(select(func.count()).select_from(CharacterEvent)) == 6


def test_event_edits_require_a_csrf_token(client: TestClient, db: Session, table: Table) -> None:
    login(client, "maitre")
    response = client.post(
        f"/mj/personnages/{table.keator.id}/evenements",
        data=_event_form(),
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert db.scalar(select(func.count()).select_from(CharacterEvent)) == 0


def test_event_of_another_character_is_not_found(
    client: TestClient, db: Session, table: Table
) -> None:
    character = _import(db)
    event_id = character.events[0].id
    login(client, "maitre")
    page = f"/mj/articles/{table.khaetar.wa_id}"
    for path in (f"{event_id}", f"{event_id}/supprimer"):
        response = post_form(
            client, f"/mj/personnages/{table.zilvra.id}/evenements/{path}", _event_form(), page
        )
        assert response.status_code == 404
    assert db.scalar(select(func.count()).select_from(CharacterEvent)) == 6


def test_reimport_keeps_events_edited_in_the_app(
    client: TestClient, db: Session, table: Table
) -> None:
    _import(db)
    login(client, "maitre")
    post_form(
        client,
        f"/mj/personnages/{table.keator.id}/evenements",
        _event_form(title="Ajout du MJ"),
        f"/mj/articles/{table.khaetar.wa_id}",
    )
    updated = {
        "character": "Khaetar Voss",
        "history": {"backstory": "Nouvelle version.", "events": [{"title": "Seul"}]},
    }
    db.expire_all()
    import_dossier(db, copy.deepcopy(updated))
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert character.profile.backstory == "Nouvelle version."
    assert len(character.events) == 7
    assert "Ajout du MJ" in [event.title for event in character.events]
    import_dossier(db, copy.deepcopy(updated), replace_events=True)
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert [event.title for event in character.events] == ["Seul"]
    assert character.profile.events_edited_at is None
    assert db.scalar(select(func.count()).select_from(CharacterEventOrganization)) == 0


def test_cli_can_replace_events_edited_in_the_app(
    tmp_path: Path, db: Session, table: Table
) -> None:
    character = _import(db)
    character.profile.events_edited_at = character.profile.updated_at
    db.commit()
    (tmp_path / "khaetar.toml").write_text(
        'character = "Khaetar Voss"\n\n[[history.events]]\ntitle = "Seul"\nyear = 990\n\n'
        '[[history.events.people]]\nname = "Bob"\nrole = "Adversaire"\n',
        encoding="utf-8",
    )
    assert cli_main(["import-dossiers", str(tmp_path)]) == 0
    db.expire_all()
    assert len(db.get(Character, table.keator.id).events) == 6
    assert cli_main(["import-dossiers", str(tmp_path), "--replace-events"]) == 0
    db.expire_all()
    events = db.get(Character, table.keator.id).events
    assert [(event.title, event.world_year) for event in events] == [("Seul", 990)]
    assert [(person.name, person.role) for person in events[0].people] == [("Bob", "Adversaire")]


def test_world_chronology_is_untouched(client: TestClient, db: Session, table: Table) -> None:
    before = db.scalar(select(func.count()).select_from(TimelineEvent))
    _import(db)
    login(client, "maitre")
    post_form(
        client,
        f"/mj/personnages/{table.keator.id}/evenements",
        _event_form(title="Événement personnel", year="987"),
        f"/mj/articles/{table.khaetar.wa_id}",
    )
    assert db.scalar(select(func.count()).select_from(TimelineEvent)) == before
    assert "Événement personnel" in client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert "Événement personnel" not in client.get("/mj/chronologie").text


def test_migration_adds_and_removes_the_event_details(db: Session) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0012")
    try:
        inspector = inspect(db.get_bind())
        tables = set(inspector.get_table_names())
        assert not {"character_event_people", "character_event_organizations"} & tables
        columns = {column["name"] for column in inspector.get_columns("character_events")}
        assert columns == {"id", "character_id", "position", "session", "title", "text"}
    finally:
        command.upgrade(config, "head")
    inspector = inspect(db.get_bind())
    assert {"character_event_people", "character_event_organizations"} <= set(
        inspector.get_table_names()
    )
    profile_columns = {column["name"] for column in inspector.get_columns("character_profiles")}
    assert "events_edited_at" in profile_columns
