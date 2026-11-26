import re
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import BuildingType, Position, Role, WaArticle, WorldCategory
from app.services import world as world_service
from app.services.structure_import import import_structures
from app.services.sync import sync_world
from tests.factories import login, make_article, make_character, make_user, post_form
from tests.test_sync import WORLD, FakeSource

CLERGE = "Clergé d'Arès"
LUX = "Lux Victrix"
DEMEURE = "La demeure de Sophie"
TOWER = "La tour"
CITY = "La Vallée"
NATION = "Le Khaanat"
SALON = "La grande pièce où Sophie accueille ses invités."
BAR = "Un comptoir de bois sombre, adossé au salon."
NEF = "la grande salle de prière, ouverte à tous."
SECRET = "Le coffre a été vidé par Zilvra."
ROOM_SECRET = "Une trappe sous le comptoir."
DEMEURE_CONTENT = (
    "[h1]Fiche rapide[/h1]\n[ul]\n"
    "- [b]Propriétaire :[/b] Sophie, marchande naine\n"
    "- [b]Personnel :[/b] Gérard, barman personnel, et quelques domestiques\n[/ul]\n"
    "[h1]Présentation[/h1]\n[p]Une grande villa.[/p]\n"
    "[h1]Le rez-de-chaussée[/h1]\n"
    f"[h2]Le salon[/h2]\n[p]{SALON}[/p]\n"
    f"[h2]Le bar[/h2]\n[p]{BAR}[/p]\n"
    "[h1]Habitants[/h1]\n[ul]\n"
    "- [b]Les domestiques :[/b] quelques serviteurs discrets, qui tiennent la maison.\n[/ul]\n"
    f"[h1]Secrets[/h1]\n[ul]\n- {SECRET}\n[/ul]"
)
LUX_CONTENT = (
    "[h1]Présentation[/h1]\n[p]Le temple d'Arès.[/p]\n"
    "[h1]Les lieux du temple[/h1]\n[ul]\n"
    f"- [b]La nef :[/b] {NEF}\n"
    "- [b]L'infirmerie :[/b] où les prêtres soignent les blessés.\n[/ul]"
)
TAB_RE = re.compile(r'role="tab"[^>]*data-tab="([^"]+)">([^<]+)</button>')


@dataclass
class World:
    clerge: WaArticle
    lux: WaArticle
    demeure: WaArticle
    tower: WaArticle
    city: WaArticle
    nation: WaArticle


def documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    def place(key: str, name: str, kind: str, source: str, **fields: Any) -> dict[str, Any]:
        return {"key": key, "name": name, "kind": kind, "source": source, **fields}

    places = [
        place("vallee", CITY, "ville", CITY, article=CITY),
        place("lux", LUX, "batiment", LUX, parent="vallee", article=LUX, owner=CLERGE),
        place("lux-nef", "La nef", "piece", LUX, parent="lux", anchor="La nef"),
        place("lux-infirmerie", "L'infirmerie", "piece", LUX, parent="lux", anchor="L'infirmerie"),
        place("demeure", DEMEURE, "batiment", DEMEURE, parent="vallee", article=DEMEURE),
        place("demeure-rdc", "Le rez-de-chaussée", "niveau", DEMEURE, parent="demeure"),
        place(
            "demeure-salon", "Le salon", "piece", DEMEURE, parent="demeure-rdc", anchor="Le salon"
        ),
        place(
            "demeure-bar",
            "Le bar",
            "piece",
            DEMEURE,
            parent="demeure-rdc",
            anchor="Le bar",
            sections=[{"title": "Secret (MJ)", "text": ROOM_SECRET}],
        ),
        place("tour", TOWER, "batiment", TOWER, parent="vallee", article=TOWER),
    ]
    positions = [
        {
            "key": "lux-kratos",
            "organisation": CLERGE,
            "name": "Kratos",
            "title": "évêque d'Arès",
            "site": "lux",
            "head": True,
            "source": LUX,
        },
        {
            "key": "lux-anton",
            "organisation": CLERGE,
            "name": "Anton",
            "title": "maître de l'infirmerie",
            "site": "lux-infirmerie",
            "staff": True,
            "source": LUX,
        },
        {
            "key": "demeure-sophie-proprietaire",
            "place": "demeure",
            "name": "Sophie",
            "title": "propriétaire, marchande naine",
            "hierarchy": False,
            "household": "owner",
            "source": DEMEURE,
            "anchor": "Propriétaire",
        },
        {
            "key": "demeure-sophie-gerard",
            "place": "demeure",
            "name": "Gérard",
            "title": "barman personnel",
            "hierarchy": False,
            "household": "employee",
            "site": "demeure-bar",
            "source": DEMEURE,
            "anchor": "Personnel",
        },
        {
            "key": "demeure-sophie-domestiques",
            "place": "demeure",
            "name": "Les domestiques",
            "title": "quelques serviteurs discrets, qui tiennent la maison",
            "group": True,
            "hierarchy": False,
            "household": "employee",
            "source": DEMEURE,
            "anchor": "Les domestiques",
        },
    ]
    for row in [*places, *positions]:
        row["visibility"] = visibility
    return [("batiments.toml", {"places": places, "positions": positions})]


@pytest.fixture
def world(db: Session) -> World:
    make_user(db, "maitre", Role.DM)
    clerge = make_article(db, CLERGE, template_type="organization")
    lux = make_article(db, LUX, template_type="landmark", content=LUX_CONTENT)
    demeure = make_article(db, DEMEURE, template_type="landmark", content=DEMEURE_CONTENT)
    tower = make_article(db, TOWER, template_type="landmark", content="[p]Une tour.[/p]")
    city = make_article(db, CITY, template_type="settlement", content="[p]Une cité.[/p]")
    nation = make_article(
        db,
        NATION,
        template_type="organization",
        raw={"articleMetadata": '{ "variant": "country" }'},
    )
    for article, category in (
        (lux, "batiment"),
        (demeure, "batiment"),
        (tower, "batiment"),
        (city, "ville"),
        (nation, "pays"),
    ):
        db.add(WorldCategory(wa_id=article.wa_id, category=category))
    db.commit()
    return World(clerge, lux, demeure, tower, city, nation)


def run(db: Session, docs: list[tuple[str, dict[str, Any]]] | None = None) -> list[Any]:
    lines = import_structures(db, docs or documents())
    db.expire_all()
    return lines


def dm_page(client: TestClient, path: str) -> str:
    login(client, "maitre")
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def tabs(page: str) -> list[str]:
    return [label for _, label in TAB_RE.findall(page)]


def panel(page: str, key: str) -> str:
    start = page.index(f'id="structure-{key}"')
    end = page.find('class="tabs__panel', start)
    return page[start : end if end >= 0 else len(page)]


def shows(page: str, value: str) -> bool:
    return value in page or value.replace("'", "&#39;") in page


def test_tabs_follow_the_building_type_and_leave_other_fiches_alone(
    client: TestClient, db: Session, world: World
) -> None:
    assert not any(line.failed for line in run(db))
    assert tabs(dm_page(client, f"/mj/articles/{world.lux.wa_id}")) == [
        "Fiche",
        "Architecture",
        "Hiérarchie",
    ]
    assert tabs(dm_page(client, f"/mj/articles/{world.demeure.wa_id}")) == [
        "Fiche",
        "Architecture",
        "Habitants",
    ]
    assert tabs(dm_page(client, f"/mj/articles/{world.tower.wa_id}")) == [
        "Fiche",
        "Architecture",
        "Hiérarchie",
    ]
    for article in (world.clerge, world.city, world.nation):
        assert tabs(dm_page(client, f"/mj/articles/{article.wa_id}")) == [
            "Fiche",
            "Territoire",
            "Hiérarchie",
        ], article.title


def test_architecture_lists_the_nested_rooms_with_their_text(
    client: TestClient, db: Session, world: World
) -> None:
    run(db)
    page = dm_page(client, f"/mj/articles/{world.demeure.wa_id}")
    rooms = panel(page, "architecture")
    assert re.findall(r'data-place="([^"]+)"', rooms) == [
        "demeure-rdc",
        "demeure-salon",
        "demeure-bar",
    ]
    assert SALON in rooms and BAR in rooms
    assert ROOM_SECRET in rooms and "MJ seulement" in rooms
    bar = rooms[rooms.index('data-place="demeure-bar"') :]
    assert "Gérard" in bar and "barman personnel" in bar
    assert 'href="/mj/lieux/demeure-salon"' in rooms
    assert SECRET not in rooms
    assert "Territoire" not in rooms and 'id="structure-territoire' not in page
    temple = panel(dm_page(client, f"/mj/articles/{world.lux.wa_id}"), "architecture")
    assert re.findall(r'data-place="([^"]+)"', temple) == ["lux-nef", "lux-infirmerie"]
    assert NEF in temple and "Anton" in temple
    tower = panel(dm_page(client, f"/mj/articles/{world.tower.wa_id}"), "architecture")
    assert "Aucune pièce consignée" in tower


def test_habitants_groups_the_owner_and_the_household_staff(
    client: TestClient, db: Session, world: World
) -> None:
    run(db)
    page = dm_page(client, f"/mj/articles/{world.demeure.wa_id}")
    people = panel(page, "habitants")
    groups = re.findall(r'class="tree-section__title" id="habitants-([a-z]+)">([^<]+)<', people)
    assert groups == [("proprietaire", "Propriétaire"), ("employes", "Employés")]
    owner = people[: people.index('id="habitants-employes"')]
    staff = people[people.index('id="habitants-employes"') :]
    assert "Sophie" in owner and "propriétaire, marchande naine" in owner
    assert re.findall(r'class="member__name"[^>]*>([^<]+)<', staff) == [
        "Gérard",
        "Les domestiques",
    ]
    assert 'id="habitants-residents"' not in people and "Hiérarchie" not in tabs(page)
    place = dm_page(client, "/mj/lieux/demeure")
    assert 'id="architecture"' in place and 'id="habitants"' in place
    assert "Personnes présentes" not in place and 'id="hierarchie"' not in place
    assert "Lieux contenus" not in place and "Habitation" in place
    lux = dm_page(client, "/mj/lieux/lux")
    assert 'id="architecture"' in lux and 'id="hierarchie"' in lux
    assert 'id="habitants"' not in lux and "Branche d&#39;une organisation" in lux
    room = dm_page(client, "/mj/lieux/demeure-rdc")
    assert "Lieux contenus" in room and 'id="architecture"' not in room


def test_type_defaults_and_the_dm_override(client: TestClient, db: Session, world: World) -> None:
    run(db)
    page = dm_page(client, f"/mj/articles/{world.demeure.wa_id}")
    assert "Type de bâtiment" in page
    assert ">Automatique (Habitation)</option>" in page
    lux = dm_page(client, f"/mj/articles/{world.lux.wa_id}")
    assert "Automatique (Branche d&#39;une organisation)" in lux
    tower = dm_page(client, f"/mj/articles/{world.tower.wa_id}")
    assert '<option value="" selected>Automatique</option>' in tower
    assert "Type de bâtiment" not in dm_page(client, f"/mj/articles/{world.city.wa_id}")

    path = f"/mj/articles/{world.demeure.wa_id}/type-batiment"
    response = post_form(client, path, {"building_type": "branch"}, "/mj")
    assert response.status_code == 303
    db.expire_all()
    assert db.get(BuildingType, world.demeure.wa_id).building_type == "branch"
    assert tabs(client.get(f"/mj/articles/{world.demeure.wa_id}").text) == [
        "Fiche",
        "Architecture",
        "Hiérarchie",
    ]
    post_form(client, path, {"building_type": ""}, "/mj")
    db.expire_all()
    assert db.get(BuildingType, world.demeure.wa_id) is None
    assert "Habitants" in tabs(client.get(f"/mj/articles/{world.demeure.wa_id}").text)

    post_form(
        client,
        f"/mj/articles/{world.tower.wa_id}/type-batiment",
        {"building_type": "dwelling"},
        "/mj",
    )
    assert tabs(client.get(f"/mj/articles/{world.tower.wa_id}").text)[-1] == "Habitants"
    post_form(client, path, {"building_type": "chateau"}, "/mj")
    post_form(
        client, f"/mj/articles/{world.city.wa_id}/type-batiment", {"building_type": "branch"}, "/mj"
    )
    db.expire_all()
    assert db.get(BuildingType, world.demeure.wa_id) is None
    assert db.get(BuildingType, world.city.wa_id) is None


def test_sync_never_overwrites_the_building_type(db: Session) -> None:
    source = FakeSource()
    house = "bbbbbbbb-0000-0000-0000-0000000000d1"
    source.put(
        house,
        DEMEURE,
        "[p]Une villa.[/p]",
        "2026-11-22 13:45:47",
        templateType="landmark",
        entityClass="Landmark",
    )
    sync_world(db, source, WORLD, full=False, trigger="cli")
    article = db.get(WaArticle, house)
    assert article.world_category == "batiment" and article.building_type is None
    world_service.set_building_type(db, article, "dwelling")
    source.put(
        house,
        DEMEURE,
        "[p]Une grande villa.[/p]",
        "2026-11-23 10:00:00",
        templateType="landmark",
        entityClass="Landmark",
    )
    sync_world(db, source, WORLD, full=True, trigger="cli")
    db.expire_all()
    article = db.get(WaArticle, house)
    assert article.content == "[p]Une grande villa.[/p]"
    assert article.building_type == "dwelling"
    with pytest.raises(world_service.WorldCategoryError):
        world_service.set_building_type(db, article, "habitation")


def test_household_import_is_idempotent_and_validated(db: Session, world: World) -> None:
    first = run(db)
    assert not any(line.failed for line in first), [line.detail for line in first if line.failed]
    again = run(db)
    assert {line.status for line in again if line.status != "warning"} == {"unchanged"}
    rows = {row.key: row.household for row in db.scalars(select(Position))}
    assert rows["demeure-sophie-proprietaire"] == "owner"
    assert rows["demeure-sophie-gerard"] == "employee"
    assert rows["lux-kratos"] == ""
    docs = documents()
    gerard = next(row for row in docs[0][1]["positions"] if row["key"] == "demeure-sophie-gerard")
    gerard["household"] = "resident"
    changed = {line.label: line.status for line in run(db, docs)}
    assert changed["position demeure-sophie-gerard"] == "updated"
    gerard["household"] = "majordome"
    docs[0][1]["positions"][0]["household"] = "owner"
    lines = import_structures(db, docs, dry_run=True)
    details = [line.detail for line in lines if line.failed]
    assert any("household must be one of" in detail for detail in details), details
    assert any("only allowed on a position attached to a place" in detail for detail in details)


def test_players_never_see_dm_only_rooms_people_or_sections(
    client: TestClient, db: Session, world: World
) -> None:
    alice = make_user(db, "alice")
    make_character(db, "Alice", alice, make_article(db, "Alice"), [world.demeure, world.city])
    docs = documents("joueurs")
    rows = {row["key"]: row for row in docs[0][1]["places"] + docs[0][1]["positions"]}
    rows["demeure-sophie-gerard"]["visibility"] = "mj"
    rows["demeure-salon"]["visibility"] = "mj"
    run(db, docs)
    login(client, "alice")
    page = client.get(f"/joueur/articles/{world.demeure.wa_id}").text
    assert tabs(page) == ["Fiche", "Architecture", "Habitants"]
    rooms = panel(page, "architecture")
    assert re.findall(r'data-place="([^"]+)"', rooms) == ["demeure-rdc", "demeure-bar"]
    people = panel(page, "habitants")
    assert "Sophie" in people and "Les domestiques" in people
    for secret in (ROOM_SECRET, "MJ seulement", "Type de bâtiment"):
        assert not shows(page, secret), secret
    for part in (rooms, people):
        for secret in ("Gérard", "barman", SALON, SECRET):
            assert not shows(part, secret), secret
    place = client.get("/joueur/lieux/demeure").text
    for secret in ("Gérard", SALON, ROOM_SECRET, SECRET, "MJ seulement"):
        assert not shows(place, secret), secret
    assert "Sophie" in place
    path = f"/mj/articles/{world.demeure.wa_id}/type-batiment"
    response = client.post(path, data={"building_type": "branch"}, follow_redirects=False)
    assert response.status_code in (303, 401, 403)
    db.expire_all()
    assert db.get(BuildingType, world.demeure.wa_id) is None


def test_players_see_no_building_tabs_when_everything_is_dm_only(
    client: TestClient, db: Session, world: World
) -> None:
    alice = make_user(db, "alice")
    make_character(db, "Alice", alice, make_article(db, "Alice"), [world.demeure])
    run(db)
    login(client, "alice")
    page = client.get(f"/joueur/articles/{world.demeure.wa_id}").text
    assert tabs(page) == []
    assert 'class="member__name"' not in page and "data-place=" not in page
    for secret in (ROOM_SECRET, "MJ seulement", 'id="structure-architecture"'):
        assert not shows(page, secret), secret


def test_the_migration_sets_the_household_roles(db: Session, world: World) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    place = {"key": "demeure", "name": DEMEURE, "kind": "batiment", "source": DEMEURE}
    run(db, [("demeure.toml", {"places": [place]})])
    db.commit()
    command.downgrade(config, "0021")
    try:
        with db.get_bind().begin() as connection:
            for key in ("demeure-sophie-proprietaire", "demeure-sophie-gerard", "autre"):
                connection.execute(
                    text(
                        "INSERT INTO positions (key, place_id, name, visibility) "
                        "VALUES (:key, (SELECT id FROM places WHERE key = 'demeure'), :key, 'mj')"
                    ),
                    {"key": key},
                )
    finally:
        command.upgrade(config, "head")
    db.expire_all()
    rows = {row.key: row.household for row in db.scalars(select(Position))}
    assert rows == {
        "demeure-sophie-proprietaire": "owner",
        "demeure-sophie-gerard": "employee",
        "autre": "",
    }
