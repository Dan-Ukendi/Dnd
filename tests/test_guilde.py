import re
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Npc, NpcRole, Place, Position, Role, StatBlock, User, WaArticle
from app.services import world as world_service
from app.services.structure_import import import_structures
from tests.factories import bare_numbers, login, make_article, make_character, make_user

GUILDE = "La Guilde des Aventuriers"
GRONK = "Gronk, fils de Brok"
CITY = "1. KHÜCHNIIN KHÖNDII La Vallée de la Volonté"
BUILDING = "La Guilde des Aventuriers de Khüchniin Khöndii"
LAYOUT = (
    "la réception en face de l'entrée, le tableau des quêtes à droite, "
    "le tableau d'honneur (classement) à gauche."
)
HONOUR = "classe les membres de la branche par rang, puis par points de quête"
ROOMS = {
    "guilde-grande-salle": ("La grande salle", "piece", "guilde-khuchniin-khondii"),
    "guilde-reception": ("La réception", "lieu", "guilde-grande-salle"),
    "guilde-tableau-des-quetes": ("Le tableau des quêtes", "lieu", "guilde-grande-salle"),
    "guilde-tableau-d-honneur": ("Le tableau d'honneur", "lieu", "guilde-grande-salle"),
}
BUILDING_CONTENT = (
    f"[h]{BUILDING}[/h]\n[p][i]Le bâtiment de la Guilde à Khüchniin Khöndii[/i][/p]\n"
    "[h1]Présentation[/h1]\n[p]La Guilde des Aventuriers est l'organisation qui met en relation "
    "ceux qui ont un problème et ceux qui savent le résoudre.[/p]\n"
    "[h1]Les lieux de la Guilde[/h1]\n[ul]\n"
    f"- [b]La grande salle :[/b] {LAYOUT}\n"
    "-- [b]La réception :[/b] le client dépose sa demande à la réception et verse une petite "
    "somme.\n"
    "-- [b]Le tableau des quêtes :[/b] une fiche de quête, affichée sur le tableau de la grande "
    "salle.\n"
    "-- [b]Le tableau d'honneur :[/b] un tableau d'honneur, affiché dans la grande salle, "
    f"{HONOUR}.\n[/ul]"
)
GUILDE_CONTENT = (
    "[h1]Organisation interne[/h1]\n[ul]\n"
    "- [b]Le chef de branche :[/b] il dirige la Guilde de la ville.\n"
    "- [b]Les coordinateurs de quêtes :[/b] ils reçoivent les demandes.\n"
    "- [b]Les réceptionnistes :[/b] ils accueillent les nouveaux venus.\n"
    "- [b]Les examinateurs :[/b] des aventuriers confirmés.\n"
    "- [b]Les comptables :[/b] ils gèrent les récompenses et les commissions.\n[/ul]"
)
SHARED_BLOCKS = {
    "commoner": ("Commoner", "0"),
    "acolyte": ("Acolyte", "1/4"),
    "guard": ("Guard", "1/8"),
    "veteran": ("Veteran", "3"),
    "scout": ("Scout", "1/2"),
}
ROLES = {
    "guilde-receptionnistes": ("Réceptionnistes", "", "commoner", "guilde-reception"),
    "guilde-coordinateurs": ("Coordinateurs de quêtes", "6", "scout", ""),
    "guilde-examinateurs": ("Examinateurs", "8", "veteran", ""),
    "guilde-comptables": ("Comptables", "4", "commoner", ""),
    "guilde-gardes": ("Gardes de la Guilde", "une dizaine", "guard", ""),
    "guilde-soigneurs": ("Soigneurs de l'infirmerie", "3", "acolyte", ""),
}
DIRECTION = {
    "miri": ("Miri", "Maîtresse de la Guilde", "Gnome"),
    "althaea": ("Althaea", "Chef des quêtes", "Tieffeline"),
    "makaria": ("Makaria", "Maîtresse des épreuves", "Naine"),
}


def stat_blocks() -> list[dict[str, Any]]:
    shared = [
        {
            "key": key,
            "n": name,
            "sz": "Medium",
            "ac": 12,
            "hp": 10,
            "ab": [10, 10, 10, 10, 10, 10],
            "cr": cr,
            "ac_": [{"n": "Club", "t": "Melee Weapon Attack, +2 to hit."}],
            "src": "5e Core Rules (SRD 5.1)",
        }
        for key, (name, cr) in SHARED_BLOCKS.items()
    ]
    return [
        *shared,
        {
            "key": "miri",
            "n": "Miri",
            "sz": "Taille P",
            "ac": 18,
            "hp": 100,
            "spd": "7,5 m",
            "ab": [16, 14, 16, 13, 14, 15],
            "sv": "FOR +6, CON +6",
            "cr": "6",
            "tr": [{"n": "Commandement", "t": "1/repos court : un allié à 9 m."}],
            "ac_": [{"n": "Épée longue", "t": "+6, 1d8+3 tranchant."}],
            "src": "D'après Vétéran (SRD 5.1), relevé à FP 6",
        },
        {
            "key": "althaea",
            "n": "Althaea",
            "ac": 12,
            "acd": "15 avec armure du mage",
            "hp": 40,
            "ab": [9, 14, 11, 17, 12, 11],
            "cr": "6",
            "tr": [
                {
                    "n": "Incantation",
                    "t": "niveau 9, INT, DD 14, +6 au toucher.",
                    "sp": [["Niv. 5 (1)", "cône de froid"]],
                }
            ],
            "ac_": [{"n": "Dague", "t": "+5, 1d4+2 perforant."}],
            "src": "D'après Mage (SRD 5.1), FP 6, inchangé",
        },
        {
            "key": "makaria",
            "n": "Makaria",
            "ac": 18,
            "hp": 90,
            "ab": [17, 11, 18, 10, 12, 12],
            "cr": "5",
            "ac_": [{"n": "Marteau de guerre", "t": "+6, 1d8+3 contondant."}],
            "src": "D'après Vétéran (SRD 5.1), relevé à FP 5",
        },
    ]


def places() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "key": "khuchniin-khondii",
            "name": "Khüchniin Khöndii (La Vallée de la Volonté)",
            "kind": "ville",
            "article": CITY,
            "source": CITY,
        },
        {
            "key": "ville-haute",
            "name": "La ville haute",
            "kind": "quartier",
            "parent": "khuchniin-khondii",
            "source": CITY,
        },
        {
            "key": "guilde-khuchniin-khondii",
            "name": BUILDING,
            "kind": "batiment",
            "parent": "ville-haute",
            "article": BUILDING,
            "owner": GUILDE,
            "source": BUILDING,
            "anchor": "Présentation",
        },
    ]
    rows += [
        {"key": key, "name": name, "kind": kind, "parent": parent, "source": BUILDING}
        | {"anchor": name}
        for key, (name, kind, parent) in ROOMS.items()
    ]
    return rows


def npcs() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "name": name,
            "title": title,
            "gender": "femme",
            "race": race,
            "organisation": GUILDE,
            "place": "guilde-khuchniin-khondii",
            "statblock": key,
            "tag": "encounter",
        }
        for key, (name, title, race) in DIRECTION.items()
    ]


def roles() -> list[dict[str, Any]]:
    rows = []
    for key, (name, headcount, block, place) in ROLES.items():
        row = {
            "key": key,
            "name": name,
            "organisation": GUILDE,
            "place": place or "guilde-khuchniin-khondii",
            "statblock": block,
        }
        if headcount:
            row["headcount"] = headcount
        rows.append(row)
    return rows


def positions() -> list[dict[str, Any]]:
    def position(key: str, superior: str = "guilde-chef-de-branche", **fields: Any) -> dict:
        row = {
            "key": key,
            "organisation": GUILDE,
            "site": "guilde-khuchniin-khondii",
            "source": GUILDE,
            **fields,
        }
        if superior:
            row["superior"] = superior
        return row

    def group(key: str, superior: str = "guilde-chef-de-branche", **fields: Any) -> dict:
        return position(key, superior, npc_role=key, group=True, **fields)

    return [
        position(
            "guilde-chef-de-branche",
            "",
            npc="miri",
            title="Maîtresse de la Guilde (chef de branche)",
            head=True,
            anchor="Le chef de branche",
        ),
        position(
            "guilde-gronk",
            person=GRONK,
            title="Réceptionniste de la branche",
            site="guilde-reception",
            anchor="Les réceptionnistes",
        ),
        position("guilde-althaea", npc="althaea", title="Chef des quêtes"),
        position("guilde-makaria", npc="makaria", title="Maîtresse des épreuves"),
        group("guilde-receptionnistes", name="Les réceptionnistes", site="guilde-reception"),
        group("guilde-coordinateurs", "guilde-althaea", name="Les coordinateurs de quêtes"),
        group("guilde-examinateurs", "guilde-makaria", name="Les examinateurs"),
        group("guilde-comptables", name="Les comptables"),
        group("guilde-gardes", name="Les gardes de la Guilde"),
        group("guilde-soigneurs", name="Les soigneurs de l'infirmerie"),
    ]


def documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    rows = {"places": places(), "npcs": npcs(), "roles": roles(), "positions": positions()}
    for items in rows.values():
        for item in items:
            item["visibility"] = visibility
    return [
        ("statblocks.json", {"statblocks": stat_blocks()}),
        ("structures.toml", {"places": rows["places"], "positions": rows["positions"]}),
        ("npcs.toml", {"npcs": rows["npcs"], "roles": rows["roles"]}),
    ]


@dataclass
class World:
    dm: User
    alice: User
    guilde: WaArticle
    gronk: WaArticle
    building: WaArticle
    city: WaArticle


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    guilde = make_article(db, GUILDE, content=GUILDE_CONTENT, template_type="organization")
    gronk = make_article(db, GRONK, content="[p]Un orc massif.[/p]")
    city = make_article(db, CITY, template_type="settlement")
    building = make_article(
        db,
        BUILDING,
        content=BUILDING_CONTENT,
        template_type="landmark",
        player_visible=False,
    )
    db.commit()
    world_service.ensure_categories(db)
    db.commit()
    make_character(db, "Alice", alice, make_article(db, "Alice la voleuse"), [guilde, gronk, city])
    return World(dm, alice, guilde, gronk, building, city)


def run_import(db: Session, docs: list[tuple[str, dict[str, Any]]] | None = None) -> list[Any]:
    lines = import_structures(db, docs if docs is not None else documents())
    db.expire_all()
    return lines


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def dm_get(client: TestClient, path: str) -> str:
    login(client, "maitre")
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def territory_panel(page: str) -> str:
    start = page.index('id="structure-territoire-body"')
    return page[start : page.index('id="structure-hierarchie"', start)]


def shows(page: str, text: str) -> bool:
    return text in page or text.replace("'", "&#39;") in page


def test_guild_import_is_idempotent_and_shares_the_general_stat_blocks(
    db: Session, world: World
) -> None:
    first = run_import(db)
    assert not any(line.failed or line.status == "warning" for line in first), first
    assert {line.status for line in run_import(db)} == {"unchanged"}
    assert (count(db, Npc), count(db, NpcRole), count(db, StatBlock)) == (3, 6, 8)
    block_keys = {row.id: row.key for row in db.scalars(select(StatBlock))}
    blocks = {row.key: block_keys[row.stat_block_id] for row in db.scalars(select(NpcRole))}
    assert blocks == {key: block for key, (_, _, block, _) in ROLES.items()}
    by_key = {row.key: row for row in db.scalars(select(Position))}
    head = by_key["guilde-chef-de-branche"]
    miri = db.scalar(select(Npc).where(Npc.key == "miri"))
    assert head.is_head and head.npc_id == miri.id
    assert by_key["guilde-coordinateurs"].superior_id == by_key["guilde-althaea"].id
    assert by_key["guilde-examinateurs"].superior_id == by_key["guilde-makaria"].id
    for key in ("guilde-gronk", "guilde-althaea", "guilde-makaria", "guilde-soigneurs"):
        assert by_key[key].superior_id == head.id, key
    building = db.scalar(select(Place).where(Place.key == "guilde-khuchniin-khondii"))
    assert building.wa_article_id == world.building.wa_id
    assert building.owner_id == world.guilde.wa_id
    rooms = {row.key: row for row in db.scalars(select(Place).where(Place.key.in_(ROOMS)))}
    assert all(row.owner_id is None for row in rooms.values())
    assert rooms["guilde-grande-salle"].parent_id == building.id
    assert rooms["guilde-reception"].parent_id == rooms["guilde-grande-salle"].id


def test_the_organisation_lists_the_building_once_and_never_its_rooms(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    page = dm_get(client, f"/mj/articles/{world.guilde.wa_id}")
    panel = territory_panel(page)
    assert re.findall(r'data-place="([^"]+)"', panel) == ["guilde-khuchniin-khondii"]
    assert f'href="/mj/articles/{world.building.wa_id}"' in panel
    for name, _, _ in ROOMS.values():
        assert not shows(panel, name), name
    assert bare_numbers(panel) == []
    hierarchy = page[page.index('id="structure-hierarchie"') :]
    for expected in ('href="/mj/pnj/miri"', 'href="/mj/roles/guilde-coordinateurs"', GRONK):
        assert expected in hierarchy, expected


def test_the_building_fiche_shows_its_rooms_with_their_text(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    fiche = dm_get(client, f"/mj/articles/{world.building.wa_id}")
    panel = territory_panel(fiche)
    assert re.findall(r'data-place="([^"]+)"', panel) == ["guilde-grande-salle"]
    for name, _, _ in ROOMS.values():
        assert shows(panel, name), name
    assert shows(panel, LAYOUT) and HONOUR in panel
    assert f'href="/mj/articles/{world.city.wa_id}"' in fiche
    hall = dm_get(client, "/mj/lieux/guilde-grande-salle")
    for key in ("guilde-reception", "guilde-tableau-des-quetes", "guilde-tableau-d-honneur"):
        assert f'href="/mj/lieux/{key}"' in hall, key
    reception = client.get("/mj/lieux/guilde-reception").text
    assert f'href="/mj/articles/{world.gronk.wa_id}"' in reception
    assert 'href="/mj/roles/guilde-receptionnistes"' in reception
    buildings = client.get("/mj/articles?section=monde&categorie=batiments").text
    assert BUILDING in buildings
    city = client.get(f"/mj/articles/{world.city.wa_id}").text
    assert f'href="/mj/articles/{world.building.wa_id}"' in city


def test_direction_fiches_and_staff_groups_render_with_their_stat_blocks(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    login(client, "maitre")
    for key, (name, title, race) in DIRECTION.items():
        page = client.get(f"/mj/pnj/{key}").text
        for expected in (
            name,
            title,
            race,
            '<span class="pill pill--encounter">Rencontre campagne</span>',
            'class="statblock"',
            f'href="/mj/articles/{world.guilde.wa_id}"',
            'href="/mj/lieux/guilde-khuchniin-khondii"',
            "Wizards of the Coast",
        ):
            assert shows(page, expected), (key, expected)
    assert "cône de froid" in client.get("/mj/pnj/althaea").text
    miri = client.get("/mj/pnj/miri").text
    assert "Taille P" in miri and "Commandement" in miri and "FP 6" in miri
    for key, (name, headcount, block, _) in ROLES.items():
        page = client.get(f"/mj/roles/{key}").text
        assert shows(page, name) and SHARED_BLOCKS[block][0] in page, key
        assert f"FP {SHARED_BLOCKS[block][1]}" in page, key
        if headcount:
            assert "Effectif" in page and headcount in page, key
    encounters = client.get("/mj/articles?section=personnage&tag=encounter").text
    for key in DIRECTION:
        assert f'href="/mj/pnj/{key}"' in encounters, key


@pytest.mark.parametrize("visibility", ["mj", "joueurs"])
def test_nothing_dm_only_reaches_players(
    client: TestClient, db: Session, world: World, visibility: str
) -> None:
    run_import(db, documents(visibility))
    login(client, "alice")
    guilde = client.get(f"/joueur/articles/{world.guilde.wa_id}")
    gronk = client.get(f"/joueur/articles/{world.gronk.wa_id}")
    assert guilde.status_code == 200 and gronk.status_code == 200
    assert client.get(f"/joueur/articles/{world.building.wa_id}").status_code == 404
    pages = [guilde.text, gronk.text]
    assert f"/articles/{world.building.wa_id}" not in guilde.text
    paths = (
        *(f"pnj/{key}" for key in DIRECTION),
        *(f"roles/{key}" for key in ROLES),
        "lieux/guilde-khuchniin-khondii",
        *(f"lieux/{key}" for key in ROOMS),
    )
    for path in paths:
        response = client.get(f"/joueur/{path}")
        pages.append(response.text)
        assert response.status_code == (404 if visibility == "mj" else 200), path
    if visibility == "mj":
        assert "Lieux contenus" not in guilde.text
        assert "Miri" not in guilde.text and "Althaea" not in guilde.text
    for page in pages:
        for hidden in ("statblock", "Commoner", "Veteran", "cône de froid", "MJ seulement"):
            assert hidden not in page, hidden
    for path in ("/mj/pnj/miri", "/mj/lieux/guilde-grande-salle", "/mj/roles/guilde-gardes"):
        assert client.get(path).status_code == 404
