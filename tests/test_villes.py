import html
import re
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Role, WaArticle, WorldCategory
from app.services.structure_import import import_structures
from tests.factories import login, make_article, make_character, make_user

NATION = "Le Khaanat"
CITY = "La Vallée"
OTHER_CITY = "Le Marché de Fer"
CLERGE = "Clergé d'Arès"
SHONO = "Shönö"
GUILDE = "La Guilde des Aventuriers"
LUX = "Lux Victrix"
DEMEURE = "La demeure de Sophie"
SECRET_NOTE = "Elle tient un dossier sur chaque Khan."
TAB_RE = re.compile(r'role="tab"[^>]*data-tab="([^"]+)">([^<]+)</button>')
NAME_RE = re.compile(r'class="member__name"[^>]*>([^<]+)<')
GROUP_RE = re.compile(r'id="habitants-([a-z0-9-]+)">(?:<a [^>]*>)?([^<]+)<')


@dataclass
class World:
    nation: WaArticle
    city: WaArticle
    lux: WaArticle
    demeure: WaArticle
    clerge: WaArticle
    shono: WaArticle
    guilde: WaArticle
    sophie: WaArticle
    zilvra: WaArticle


def documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    def place(key: str, name: str, kind: str, **fields: Any) -> dict[str, Any]:
        return {"key": key, "name": name, "kind": kind, "source": CITY, **fields}

    places = [
        place("khaanat", NATION, "territoire", article=NATION),
        place("vallee", CITY, "ville", parent="khaanat", article=CITY),
        place("tomor-zar", OTHER_CITY, "ville", parent="khaanat"),
        place("hall", "Le Hall du Silence", "lieu", parent="vallee"),
        place("ville-haute", "La ville haute", "quartier", parent="vallee"),
        place("guilde", "La Guilde de la Vallée", "batiment", parent="ville-haute", owner=GUILDE),
        place("reception", "La réception", "lieu", parent="guilde"),
        place("lux", LUX, "batiment", parent="vallee", article=LUX, owner=CLERGE),
        place("tertre", "Le Tertre Rouge", "batiment", parent="tomor-zar", owner=CLERGE),
        place("marche-noir", "Le marché noir", "lieu", parent="vallee", owner=SHONO),
        place("demeure", DEMEURE, "batiment", parent="vallee", article=DEMEURE),
    ]

    def position(key: str, **fields: Any) -> dict[str, Any]:
        return {"key": key, "source": CITY, **fields}

    positions = [
        position(
            "vallee-checheg",
            place="vallee",
            npc="checheg",
            title="Khan de la Voie de la Volonté",
            site="hall",
            head=True,
        ),
        position(
            "vallee-orianna",
            place="vallee",
            npc="orianna",
            title="Lieutenante de Checheg",
            superior="vallee-checheg",
        ),
        position(
            "vallee-sukhbat",
            place="vallee",
            npc="sukhbat",
            title="Lieutenant de Checheg, agent des marchés",
            superior="vallee-checheg",
            site="tomor-zar",
        ),
        position(
            "vallee-tomorzorig",
            place="vallee",
            name="Tömörzorig",
            title="ancien maître de la Voie de la Volonté",
            hierarchy=False,
        ),
        position(
            "clerge-kratos",
            organisation=CLERGE,
            name="Kratos",
            title="évêque d'Arès",
            site="lux",
            head=True,
        ),
        position(
            "clerge-pretres",
            organisation=CLERGE,
            name="Les prêtres",
            superior="clerge-kratos",
            site="lux",
            group=True,
        ),
        position(
            "clerge-kara",
            organisation=CLERGE,
            name="Kara",
            title="évêque d'Arès du Tertre",
            site="tertre",
            head=True,
        ),
        position(
            "shono-erdene",
            organisation=SHONO,
            name="Erdene",
            title="l'Ezen du Grand Étal",
            site="marche-noir",
            head=True,
        ),
        position("shono-bob", organisation=SHONO, name="Bob", title="le pickpocket"),
        position(
            "marche-bob",
            place="marche-noir",
            name="Bob",
            title="voleur à la tire",
            hierarchy=False,
        ),
        position(
            "guilde-gronk",
            organisation=GUILDE,
            name="Gronk",
            title="Réceptionniste",
            site="reception",
            staff=True,
        ),
        position(
            "guilde-zilvra",
            organisation=GUILDE,
            person="Zilvra",
            title="Rang B",
            site="guilde",
        ),
        position(
            "demeure-sophie",
            place="demeure",
            person="Sophie",
            title="propriétaire",
            hierarchy=False,
            household="owner",
        ),
    ]
    npcs = [
        {"key": "checheg", "name": "Checheg", "title": "Khan", "place": "vallee"},
        {
            "key": "orianna",
            "name": "Orianna",
            "title": "Lieutenante",
            "place": "vallee",
            "gm_notes": SECRET_NOTE,
        },
        {"key": "sukhbat", "name": "Sükhbat", "title": "Lieutenant", "place": "tomor-zar"},
        {
            "key": "miri",
            "name": "Miri",
            "title": "Maîtresse de la Guilde",
            "organisation": GUILDE,
            "place": "guilde",
        },
    ]
    for row in [*places, *positions, *npcs]:
        row["visibility"] = visibility
    return [
        ("ville.toml", {"places": places, "positions": positions}),
        ("npcs.toml", {"npcs": npcs}),
    ]


@pytest.fixture
def world(db: Session) -> World:
    make_user(db, "maitre", Role.DM)
    nation = make_article(
        db,
        NATION,
        template_type="organization",
        raw={"articleMetadata": '{ "variant": "country" }'},
    )
    city = make_article(db, CITY, template_type="settlement", content="[p]Une cité.[/p]")
    lux = make_article(db, LUX, template_type="landmark", content="[p]Un temple.[/p]")
    demeure = make_article(db, DEMEURE, template_type="landmark", content="[p]Une villa.[/p]")
    clerge = make_article(db, CLERGE, template_type="organization")
    shono = make_article(db, SHONO, template_type="organization")
    guilde = make_article(db, GUILDE, template_type="organization")
    sophie = make_article(db, "Sophie")
    zilvra = make_article(db, "Zilvra")
    for article, category in (
        (nation, "pays"),
        (city, "ville"),
        (lux, "batiment"),
        (demeure, "batiment"),
    ):
        db.add(WorldCategory(wa_id=article.wa_id, category=category))
    db.commit()
    return World(nation, city, lux, demeure, clerge, shono, guilde, sophie, zilvra)


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


def sections(people: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    parts = re.split(r'(?=<section class="tree-section household-group")', people)
    for part in parts:
        match = GROUP_RE.search(part)
        if match:
            found[html.unescape(match.group(2))] = [
                html.unescape(name) for name in NAME_RE.findall(part)
            ]
    return found


def test_only_city_fiches_get_the_habitants_tab(
    client: TestClient, db: Session, world: World
) -> None:
    assert not any(line.failed for line in run(db)), [line.detail for line in run(db)]
    assert tabs(dm_page(client, f"/mj/articles/{world.city.wa_id}")) == [
        "Fiche",
        "Territoire",
        "Habitants",
    ]
    assert tabs(dm_page(client, f"/mj/articles/{world.nation.wa_id}")) == [
        "Fiche",
        "Territoire",
        "Hiérarchie",
    ]
    assert tabs(dm_page(client, f"/mj/articles/{world.clerge.wa_id}")) == [
        "Fiche",
        "Territoire",
        "Hiérarchie",
    ]
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
    city = dm_page(client, f"/mj/articles/{world.city.wa_id}")
    assert 'id="structure-hierarchie"' not in city


def test_habitants_lists_the_leaders_then_the_people_by_organisation(
    client: TestClient, db: Session, world: World
) -> None:
    run(db)
    people = panel(dm_page(client, f"/mj/articles/{world.city.wa_id}"), "habitants")
    found = sections(people)
    assert list(found) == [
        "Chef de la ville",
        CLERGE,
        GUILDE,
        SHONO,
        "Autres habitants",
    ]
    assert found["Chef de la ville"] == ["Checheg"]
    assert found[CLERGE] == ["Kratos"]
    assert found[GUILDE] == ["Gronk", "Zilvra", "Miri"]
    assert found[SHONO] == ["Erdene", "Bob"]
    assert found["Autres habitants"] == ["Orianna", "Tömörzorig", "Sophie"]
    for absent in ("Les prêtres", "Kara", "Sükhbat"):
        assert absent not in people, absent
    assert people.index('id="habitants-chefs"') < people.index("Kratos")
    assert "Khan de la Voie de la Volonté" in people and "Le Hall du Silence" in people
    assert f'href="/mj/articles/{world.sophie.wa_id}"' in people
    assert f'href="/mj/articles/{world.zilvra.wa_id}"' in people
    assert 'href="/mj/pnj/checheg"' in people
    assert f'href="/mj/articles/{world.clerge.wa_id}"' in people
    assert "La demeure de Sophie" in people and "voleur à la tire" in people


def test_territoire_of_a_city_lists_places_only(
    client: TestClient, db: Session, world: World
) -> None:
    run(db)
    page = dm_page(client, f"/mj/articles/{world.city.wa_id}")
    territory = panel(page, "territoire")
    for place in ("La ville haute", LUX, "Le marché noir", DEMEURE, "Le Hall du Silence"):
        assert place in territory, place
    assert 'class="member__name"' not in territory
    for absent in ("Personnes notables", "Organisations présentes", "Checheg", "Kratos", "Bob"):
        assert absent not in territory, absent
    fragment = client.get(
        f"/mj/articles/{world.city.wa_id}/territoire?lieu=ville-haute",
        headers={"HX-Request": "true"},
    ).text
    assert "La Guilde de la Vallée" in fragment
    assert "Personnes notables" not in fragment and "Gronk" not in fragment
    nation = panel(dm_page(client, f"/mj/articles/{world.nation.wa_id}"), "territoire")
    assert "Personnes notables" in nation or "Organisations présentes" in nation


def test_players_never_see_dm_only_inhabitants(
    client: TestClient, db: Session, world: World
) -> None:
    alice = make_user(db, "alice")
    make_character(db, "Alice", alice, world.zilvra, [world.city, world.guilde])
    docs = documents("joueurs")
    rows = {row["key"]: row for _, doc in docs for kind in doc.values() for row in kind}
    for key in ("marche-noir", "orianna", "vallee-tomorzorig", "guilde-gronk"):
        rows[key]["visibility"] = "mj"
    run(db, docs)
    login(client, "alice")
    page = client.get(f"/joueur/articles/{world.city.wa_id}").text
    assert tabs(page) == ["Fiche", "Territoire", "Habitants"]
    people = panel(page, "habitants")
    found = sections(people)
    assert found["Chef de la ville"] == ["Checheg"]
    assert found[GUILDE] == ["Zilvra", "Miri"]
    assert CLERGE not in found and SHONO not in found
    for secret in ("Orianna", "Tömörzorig", "Gronk", "Bob", "Erdene", "Le marché noir"):
        assert secret not in page, secret
    for secret in (SECRET_NOTE, "MJ seulement", "Kratos"):
        assert secret not in page, secret


def test_players_see_no_city_tabs_when_everything_is_dm_only(
    client: TestClient, db: Session, world: World
) -> None:
    alice = make_user(db, "alice")
    make_character(db, "Alice", alice, world.zilvra, [world.city])
    run(db)
    login(client, "alice")
    page = client.get(f"/joueur/articles/{world.city.wa_id}").text
    assert tabs(page) == []
    for secret in ("Checheg", "Orianna", "Kratos", "Habitants", 'class="member__name"'):
        assert secret not in page, secret


def test_city_import_is_idempotent_when_people_change(db: Session, world: World) -> None:
    assert not any(line.failed for line in run(db))
    again = run(db)
    assert {line.status for line in again if line.status != "warning"} == {"unchanged"}
    docs = documents()
    tomorzorig = next(row for row in docs[0][1]["positions"] if row["key"] == "vallee-tomorzorig")
    tomorzorig["title"] = "la Volonté de Fer, ancien maître de la Voie de la Volonté"
    changed = {line.label: line.status for line in run(db, docs)}
    assert changed["position vallee-tomorzorig"] == "updated"
    final = run(db, docs)
    assert {line.status for line in final if line.status != "warning"} == {"unchanged"}
