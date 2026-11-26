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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Position, Role, WaArticle
from app.services.structure_import import import_structures
from tests.factories import login, make_article, make_character, make_user

CLERGE = "Clergé d'Arès"
GUILDE = "La Guilde des Aventuriers"
SHONO = "Shönö"
LUX = "Lux Victrix"
SOURCES = Path(__file__).resolve().parent.parent / "source" / "structures"
ITEM_RE = re.compile(
    r'<li class="tree__item"[^>]*?aria-level="(\d)"[^>]*>.*?class="tree__name"[^>]*>([^<]+)<', re.S
)
INSTITUTIONS = ("clerge-conseil-des-prelats", "clerge-garde-blanche", "nuit-conseil")


def place(key: str, name: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "source": fields.pop("source", CLERGE),
        **fields,
    }


def clerge_positions() -> list[dict[str, Any]]:
    def position(key: str, **fields: Any) -> dict[str, Any]:
        return {"key": key, "organisation": CLERGE, "source": CLERGE, "site": "lux", **fields}

    return [
        position("kratos", name="Kratos", title="évêque d'Arès", head=True),
        position("berenike", name="Berenike", title="présidente du conseil", superior="kratos"),
        position("prelats", name="Autres prélats du conseil", superior="berenike", group=True),
        position("pretres", name="Les prêtres", superior="prelats", group=True),
        position("acolytes", name="Les acolytes", superior="pretres", group=True),
        position("inities", name="Les initiés", superior="acolytes", group=True),
        position("agathokles", name="Agathokles", title="capitaine", superior="kratos"),
        position("sergents", name="Les sergents", superior="agathokles", group=True),
        position("gardes", name="Les gardes blancs", superior="sergents", group=True),
        position(
            "anton", name="Anton", title="maître de l'infirmerie", site="infirmerie", staff=True
        ),
        position("hesperia", name="Hesperia", title="gardienne de la crypte", staff=True),
        position(
            "kara", name="Kara", title="évêque d'Arès de Khara Suld", site="tertre", head=True
        ),
        position("ownka", name="Ownka", title="évêque d'Arès de Boo Uul", site="cairns", head=True),
        position(
            "tertre-capitaine", name="Bataar", title="capitaine", superior="kara", site="tertre"
        ),
        position(
            "tertre-gardes",
            name="Les gardes du tertre",
            superior="tertre-capitaine",
            site="tertre",
            group=True,
        ),
    ]


def clerge_documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    places = [
        place("lux", LUX, "batiment", article=LUX, owner=CLERGE),
        place("infirmerie", "L'infirmerie", "piece", parent="lux", owner=CLERGE),
        place("tertre", "Le Tertre Rouge", "batiment", owner=CLERGE),
        place("cairns", "Le Sanctuaire des Cairns", "batiment", owner=CLERGE),
    ]
    positions = clerge_positions()
    for row in [*places, *positions]:
        row["visibility"] = visibility
    return [("clerge.toml", {"places": places, "positions": positions})]


@dataclass
class World:
    clerge: WaArticle
    lux: WaArticle


@pytest.fixture
def world(db: Session) -> World:
    make_user(db, "maitre", Role.DM)
    clerge = make_article(db, CLERGE, template_type="organization")
    lux = make_article(db, LUX, template_type="landmark")
    db.commit()
    return World(clerge, lux)


def run(db: Session, docs: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    lines = import_structures(db, docs)
    db.expire_all()
    return lines


def dm_page(client: TestClient, path: str) -> str:
    login(client, "maitre")
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def hierarchy_part(page: str) -> str:
    start = page.find('id="structure-hierarchie"')
    return page[start if start >= 0 else page.index('id="hierarchie"') :]


def outline(page: str) -> list[tuple[int, str]]:
    return [(int(level), name) for level, name in ITEM_RE.findall(page)]


def staff_names(page: str) -> list[str]:
    if 'id="hierarchie-personnel"' not in page:
        return []
    part = page[page.index('id="hierarchie-personnel"') :]
    part = part[: part.index("</ul>")]
    return re.findall(r'class="member__name"[^>]*>([^<]+)<', part)


def test_organisation_page_shows_the_top_rank_and_direct_subordinates_only(
    client: TestClient, db: Session, world: World
) -> None:
    assert not any(line.failed for line in run(db, clerge_documents()))
    page = hierarchy_part(dm_page(client, f"/mj/articles/{world.clerge.wa_id}"))
    assert outline(page) == [
        (1, "Kratos"),
        (2, "Berenike"),
        (2, "Agathokles"),
        (1, "Kara"),
        (2, "Bataar"),
        (1, "Ownka"),
    ]
    for hidden in ("Autres prélats du conseil", "Les prêtres", "Les initiés", "Les sergents"):
        assert hidden not in page, hidden
    assert "Anton" not in page and "Hesperia" not in page
    assert 'id="hierarchie-personnel"' not in page
    assert page.count("1 subordonné sur la fiche du lieu") == 3
    assert "Hiérarchie complète par lieu" in page
    assert 'href="/mj/lieux/lux#hierarchie"' in page
    assert 'href="/mj/lieux/tertre#hierarchie"' in page


def test_place_fiche_shows_the_full_ladders_converging_on_the_head(
    client: TestClient, db: Session, world: World
) -> None:
    run(db, clerge_documents())
    page = hierarchy_part(dm_page(client, "/mj/lieux/lux"))
    assert outline(page) == [
        (1, "Kratos"),
        (2, "Berenike"),
        (3, "Autres prélats du conseil"),
        (4, "Les prêtres"),
        (5, "Les acolytes"),
        (6, "Les initiés"),
        (2, "Agathokles"),
        (3, "Les sergents"),
        (4, "Les gardes blancs"),
    ]
    assert staff_names(page) == ["Anton", "Hesperia"]
    assert "Sans supérieur défini" not in page
    kratos = page[page.index('aria-labelledby="noeud-kratos"') - 200 :]
    assert 'aria-expanded="true"' in kratos[: kratos.index("tree__row")]
    berenike = re.search(r'<li class="tree__item"[^>]*noeud-berenike"[^>]*>', page).group(0)
    assert 'aria-expanded="false"' in berenike
    assert '<div class="tree__panel" inert>' in page
    assert 'data-tree-all="expand"' in page and 'data-tree-all="collapse"' in page
    assert 'role="tree"' in page and 'role="group"' in page
    article = hierarchy_part(dm_page(client, f"/mj/articles/{world.lux.wa_id}"))
    assert outline(article) == outline(page)
    assert staff_names(article) == ["Anton", "Hesperia"]
    room = hierarchy_part(dm_page(client, "/mj/lieux/infirmerie"))
    assert outline(room) == [] and staff_names(room) == ["Anton"]


def test_a_temple_head_can_come_from_the_guard_ladder(
    client: TestClient, db: Session, world: World
) -> None:
    run(db, clerge_documents())
    page = hierarchy_part(dm_page(client, "/mj/lieux/tertre"))
    assert outline(page) == [(1, "Kara"), (2, "Bataar"), (3, "Les gardes du tertre")]


def test_staff_never_enter_the_tree_and_cannot_be_heads(db: Session, world: World) -> None:
    run(db, clerge_documents())
    anton = db.scalar(select(Position).where(Position.key == "anton"))
    assert anton.is_staff and anton.in_hierarchy and anton.superior_id is None
    docs = clerge_documents()
    docs[0][1]["positions"][0]["staff"] = True
    lines = import_structures(db, docs, dry_run=True)
    assert any(line.failed and "cannot be the head" in line.detail for line in lines), [
        line.detail for line in lines if line.failed
    ]


def test_staff_flag_import_is_idempotent(db: Session, world: World) -> None:
    run(db, clerge_documents())
    again = run(db, clerge_documents())
    assert {line.status for line in again if line.status != "warning"} == {"unchanged"}
    assert db.scalar(select(Position).where(Position.key == "hesperia")).is_staff
    docs = clerge_documents()
    next(row for row in docs[0][1]["positions"] if row["key"] == "hesperia").pop("staff")
    changed = {line.label: line.status for line in run(db, docs)}
    assert changed["position hesperia"] == "updated"
    assert not db.scalar(select(Position).where(Position.key == "hesperia")).is_staff


def test_players_never_see_dm_only_branches_or_staff(
    client: TestClient, db: Session, world: World
) -> None:
    alice = make_user(db, "alice")
    make_character(db, "Alice", alice, make_article(db, "Alice"), [world.clerge, world.lux])
    docs = clerge_documents("joueurs")
    rows = {row["key"]: row for row in docs[0][1]["positions"]}
    for key in ("agathokles", "hesperia", "pretres"):
        rows[key]["visibility"] = "mj"
    run(db, docs)
    login(client, "alice")
    organisation = hierarchy_part(client.get(f"/joueur/articles/{world.clerge.wa_id}").text)
    assert outline(organisation)[:2] == [(1, "Kratos"), (2, "Berenike")]
    assert "Agathokles" not in organisation and "Anton" not in organisation
    lux = client.get("/joueur/lieux/lux")
    assert lux.status_code == 200
    page = hierarchy_part(lux.text)
    names = [name for _, name in outline(page)]
    assert names == ["Kratos", "Berenike", "Autres prélats du conseil"]
    for secret in ("Agathokles", "Les sergents", "Les prêtres", "Les acolytes", "Hesperia"):
        assert secret not in lux.text, secret
    assert staff_names(page) == ["Anton"]
    assert "MJ seulement" not in lux.text


def test_institution_rows_are_removed_by_the_migration(db: Session, world: World) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.commit()
    command.downgrade(config, "0020")
    try:
        with db.get_bind().begin() as connection:
            for index, key in enumerate(("clerge-krateros", *INSTITUTIONS, "clerge-autres")):
                connection.execute(
                    text(
                        "INSERT INTO positions (key, organisation_id, name, visibility) "
                        "VALUES (:key, :org, :name, 'mj')"
                    ),
                    {"key": key, "org": world.clerge.wa_id, "name": f"Poste {index}"},
                )
            connection.execute(
                text(
                    "UPDATE positions SET superior_id = "
                    "(SELECT id FROM positions WHERE key = 'clerge-conseil-des-prelats') "
                    "WHERE key = 'clerge-autres'"
                )
            )
    finally:
        command.upgrade(config, "head")
    db.expire_all()
    rows = {row.key: row for row in db.scalars(select(Position))}
    assert set(rows) == {"clerge-krateros", "clerge-autres"}
    assert rows["clerge-autres"].superior_id is None
    assert not any(row.is_staff for row in rows.values())


def _source_positions() -> Iterator[dict[str, Any]]:
    for path in sorted(SOURCES.glob("*.toml")):
        with path.open("rb") as handle:
            yield from tomllib.load(handle).get("positions", [])


@pytest.mark.skipif(not SOURCES.is_dir(), reason="source/structures is not available")
def test_source_hierarchies_follow_the_rules() -> None:
    positions = {row["key"]: row for row in _source_positions()}
    assert not set(INSTITUTIONS) & set(positions)
    for row in positions.values():
        name = row.get("name", "")
        assert not re.match(r"^(Le|La|L')\s*(conseil|Garde|Boulē|Ecclesia)", name), name
        if row.get("staff"):
            assert "superior" not in row and not row.get("head"), row["key"]

    def chain(key: str) -> list[str]:
        found = [key]
        while positions[found[-1]].get("superior"):
            found.append(positions[found[-1]]["superior"])
        return found

    assert chain("clerge-inities") == [
        "clerge-inities",
        "clerge-acolytes",
        "clerge-pretres",
        "clerge-autres-prelats",
        "clerge-berenike",
        "clerge-krateros",
    ]
    assert chain("clerge-gardes-blancs") == [
        "clerge-gardes-blancs",
        "clerge-sergents-garde-blanche",
        "clerge-agathokles",
        "clerge-krateros",
    ]
    assert positions["clerge-krateros"].get("head") is True
    staff = {key for key, row in positions.items() if row.get("staff")}
    assert {"clerge-anton", "clerge-hesperia", "guilde-gronk", "nuit-mage-du-marche"} <= staff
    assert {
        "guilde-receptionnistes",
        "guilde-comptables",
        "guilde-gardes",
        "guilde-soigneurs",
    } <= staff
    heads = {key for key, row in positions.items() if row.get("head")}
    assert {"nuit-erdene", "nuit-tsetseg", "nuit-ganbold"} <= heads
