from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FicheSection, Npc, NpcRelation, Position, Role, StatBlock, User, WaArticle
from app.services.structure_import import import_structures
from tests.factories import login, make_article, make_character, make_user

KHAANAT = "Le Khaanat des Cinq Lances"
KHAANAT_CONTENT = (
    "[h1]Géographie et cités majeures[/h1][p]Des steppes.[/p]"
    "[h2]Les Cinq Cours des Khans (une Cour par Voie)[/h2]\n[ol]\n"
    "- [b]KHARA SULD[/b] (La Lance Noire) - Cour de la Voie du Guerrier\n"
    "-- [b]Description[/b] : un immense ordo.\n"
    "- [b]KHÜCHNIIN KHÖNDII[/b] (La Vallée de la Volonté)\n[/ol]"
)
COUNTRY_SECRET = "Les Métis (Kholimog) : certains développent des chadvar uniques cachés."
JARGAL_SECRET = "La vieille boot qui l'a sauvé vit encore."
ORIANNA_SECRET = "Elle a refusé un seul contrat."
JARGAL_HISTORY = "Jargal est né d'un Écho et d'une humaine."


def stat_blocks() -> list[dict[str, Any]]:
    return [
        {
            "key": "jargal",
            "n": "Jargal",
            "ac": 18,
            "hp": 150,
            "spd": "9 m",
            "ab": [20, 13, 18, 10, 12, 14],
            "sv": "FOR +9, CON +8",
            "cr": "9",
            "tr": [{"n": "Vision dans le noir 18 m", "t": ""}],
            "ac_": [{"n": "Grande épée", "t": "+9, 2d6+5 tranchant."}],
            "ba": [{"n": "Défi du Khan", "t": "une créature à 9 m fait un JS SAG DD 16."}],
            "re": [{"n": "Parade", "t": "+3 CA."}],
            "src": "D'après Veteran (SRD 5.1), mis à l'échelle FP 9",
        },
        {
            "key": "checheg",
            "n": "Checheg",
            "ac": 15,
            "acd": "armure du mage",
            "hp": 110,
            "ab": [9, 14, 14, 18, 14, 14],
            "cr": "10",
            "tr": [
                {
                    "n": "Incantation",
                    "t": "niveau 12, INT, DD 16, +8 au toucher.",
                    "sp": [["Niv. 6", "suggestion de groupe"]],
                }
            ],
            "src": "D'après Mage (SRD 5.1), mis à l'échelle FP 10",
        },
    ]


def places() -> list[dict[str, Any]]:
    return [
        {
            "key": "khaanat",
            "name": KHAANAT,
            "kind": "territoire",
            "article": KHAANAT,
            "source": KHAANAT,
            "anchor": "Géographie et Cités Majeures",
            "sections": [{"title": "Secret", "text": COUNTRY_SECRET, "visibility": "joueurs"}],
        },
        {
            "key": "khara-suld",
            "name": "Khara Suld (La Lance Noire)",
            "kind": "ville",
            "parent": "khaanat",
            "source": KHAANAT,
            "anchor": "KHARA SULD",
        },
        {
            "key": "khar-khunii-ger",
            "name": "Khar Khünii Ger (Yourte du Sang Noir)",
            "kind": "lieu",
            "parent": "khara-suld",
            "source": KHAANAT,
        },
        {
            "key": "khuchniin-khondii",
            "name": "Khüchniin Khöndii (La Vallée de la Volonté)",
            "kind": "ville",
            "parent": "khaanat",
            "source": KHAANAT,
        },
        {
            "key": "tomor-zar",
            "name": "Tömör Zar (Le Marché de Fer)",
            "kind": "ville",
            "parent": "khaanat",
            "source": KHAANAT,
        },
    ]


def npcs() -> list[dict[str, Any]]:
    def sections(history: str, secret: str) -> list[dict[str, str]]:
        return [
            {"title": "Rôle", "text": "Un rôle."},
            {"title": "Histoire", "text": history},
            {"title": "Secret", "text": secret, "visibility": "joueurs"},
            {"title": "Apparence", "text": "Une apparence."},
        ]

    return [
        {
            "key": "jargal",
            "name": "Jargal",
            "title": "Khan Baatar, Khan de la Voie du Guerrier",
            "gender": "homme",
            "race": "Demi-orc",
            "age": "38 ans",
            "standing": "Métis (Kholimog) à l'expression mixte",
            "place": "khara-suld",
            "statblock": "jargal",
            "tag": "lore",
            "sections": sections(JARGAL_HISTORY, JARGAL_SECRET),
        },
        {
            "key": "checheg",
            "name": "Checheg",
            "title": "Khan de la Voie de la Volonté",
            "place": "khuchniin-khondii",
            "statblock": "checheg",
            "tag": "lore",
            "sections": sections("Née dans les vallées du silence.", "Un dossier sur chaque Khan."),
        },
        {
            "key": "orianna",
            "name": "Orianna",
            "title": "Lieutenante de Checheg, exécutrice des dalhich",
            "place": "khuchniin-khondii",
            "tag": "encounter",
            "sections": sections("Prise à 6 ans par les dalhich.", ORIANNA_SECRET),
            "relations": [
                {
                    "npc": "checheg",
                    "label": "Supérieure",
                    "reverse_label": "Lieutenante, exécutrice des dalhich",
                }
            ],
        },
        {
            "key": "sukhbat",
            "name": "Sükhbat",
            "title": "Lieutenant de Checheg, agent des marchés",
            "place": "tomor-zar",
            "tag": "encounter",
            "relations": [
                {
                    "npc": "checheg",
                    "label": "Supérieure",
                    "reverse_label": "Lieutenant, agent des marchés",
                }
            ],
        },
    ]


def positions() -> list[dict[str, Any]]:
    return [
        {
            "key": "khara-suld-khan-baatar",
            "place": "khara-suld",
            "npc": "jargal",
            "title": "Khan Baatar, Khan de la Voie du Guerrier",
            "site": "khar-khunii-ger",
            "source": KHAANAT,
            "anchor": "KHARA SULD",
        },
        {
            "key": "khuchniin-khondii-khan-checheg",
            "place": "khuchniin-khondii",
            "npc": "checheg",
            "title": "Khan de la Voie de la Volonté",
            "source": KHAANAT,
            "anchor": "KHÜCHNIIN KHÖNDII",
        },
        {
            "key": "khuchniin-khondii-orianna",
            "place": "khuchniin-khondii",
            "npc": "orianna",
            "title": "Lieutenante de Checheg, exécutrice des dalhich",
            "superior": "khuchniin-khondii-khan-checheg",
            "source": KHAANAT,
        },
        {
            "key": "khuchniin-khondii-sukhbat",
            "place": "khuchniin-khondii",
            "npc": "sukhbat",
            "title": "Lieutenant de Checheg, agent des marchés",
            "superior": "khuchniin-khondii-khan-checheg",
            "site": "tomor-zar",
            "source": KHAANAT,
        },
    ]


def documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    rows = {"places": places(), "npcs": npcs(), "positions": positions()}
    for items in rows.values():
        for item in items:
            item["visibility"] = visibility
    return [
        ("statblocks.json", {"statblocks": stat_blocks()}),
        ("khaanat.toml", {"places": rows["places"], "positions": rows["positions"]}),
        ("npcs.toml", {"npcs": rows["npcs"]}),
    ]


@dataclass
class World:
    dm: User
    alice: User
    khaanat: WaArticle


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    khaanat = make_article(db, KHAANAT, content=KHAANAT_CONTENT, template_type="settlement")
    make_character(db, "Alice", alice, make_article(db, "Alice la voleuse"), [khaanat])
    return World(dm, alice, khaanat)


def run_import(db: Session, docs: list[tuple[str, dict[str, Any]]] | None = None) -> list[Any]:
    lines = import_structures(db, docs if docs is not None else documents())
    db.expire_all()
    return lines


def statuses(lines: list[Any]) -> dict[str, str]:
    return {line.label: line.status for line in lines if line.status != "warning"}


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def dm_get(client: TestClient, path: str) -> str:
    login(client, "maitre")
    response = client.get(path)
    assert response.status_code == 200, path
    return response.text


def test_khaanat_import_is_idempotent_and_links_everything(db: Session, world: World) -> None:
    first = run_import(db)
    assert not any(line.failed or line.status == "warning" for line in first)
    assert set(statuses(first).values()) == {"created"}
    counts = (count(db, StatBlock), count(db, Npc), count(db, Position), count(db, NpcRelation))
    assert counts == (2, 4, 4, 2)
    assert count(db, FicheSection) == 13
    assert set(statuses(run_import(db)).values()) == {"unchanged"}
    assert count(db, FicheSection) == 13 and count(db, NpcRelation) == 2
    categories = dict(db.execute(select(Npc.key, Npc.category)).all())
    assert categories == {
        "jargal": "lore",
        "checheg": "lore",
        "orianna": "encounter",
        "sukhbat": "encounter",
    }
    secrets = db.scalars(select(FicheSection.visibility).where(FicheSection.title == "Secret"))
    assert set(secrets) == {"mj"}


def test_dm_sees_the_khans_and_lieutenants_under_their_tags(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    lore = dm_get(client, "/mj/articles?section=personnage&tag=lore")
    assert 'href="/mj/pnj/jargal"' in lore and 'href="/mj/pnj/checheg"' in lore
    assert "/mj/pnj/orianna" not in lore
    encounter = client.get("/mj/articles?section=personnage&tag=encounter").text
    assert 'href="/mj/pnj/orianna"' in encounter and 'href="/mj/pnj/sukhbat"' in encounter
    assert "/mj/pnj/jargal" not in encounter
    jargal = client.get("/mj/pnj/jargal").text
    for expected in (
        '<span class="pill pill--lore">Lore</span>',
        "Khan Baatar, Khan de la Voie du Guerrier",
        "Métis (Kholimog) à l&#39;expression mixte",
        "38 ans",
        "Rôle",
        JARGAL_HISTORY,
        JARGAL_SECRET,
        "Apparence",
        'class="statblock"',
        "FP 9",
        "Défi du Khan",
        "Actions bonus",
        "Réactions",
        "Wizards of the Coast",
    ):
        assert expected in jargal, expected
    order = [jargal.index(title) for title in ("Rôle", "Histoire", ">Secret<", "Apparence")]
    assert order == sorted(order)
    assert jargal.index("Apparence") < jargal.index('class="statblock"')
    orianna = client.get("/mj/pnj/orianna").text
    assert '<span class="pill pill--encounter">Rencontre campagne</span>' in orianna
    checheg = client.get("/mj/pnj/checheg").text
    assert "Niv. 6" in checheg and "suggestion de groupe" in checheg


def test_each_khan_and_his_cour_link_both_ways(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    jargal = dm_get(client, "/mj/pnj/jargal")
    assert 'href="/mj/lieux/khara-suld"' in jargal
    assert 'href="/mj/lieux/khar-khunii-ger"' in jargal
    cour = client.get("/mj/lieux/khara-suld").text
    assert 'id="hierarchie"' in cour and 'href="/mj/pnj/jargal"' in cour
    assert "Khan Baatar, Khan de la Voie du Guerrier" in cour
    assert 'href="/mj/lieux/khar-khunii-ger"' in cour
    assert 'href="/mj/pnj/jargal"' in client.get("/mj/lieux/khar-khunii-ger").text
    vallee = client.get("/mj/lieux/khuchniin-khondii").text
    assert 'href="/mj/pnj/checheg"' in vallee
    assert 'href="/mj/lieux/khuchniin-khondii"' in client.get("/mj/pnj/checheg").text
    country = client.get(f"/mj/articles/{world.khaanat.wa_id}").text
    card = country[country.index('data-place="khara-suld"') :]
    card = card[: card.index("</details>")]
    assert 'href="/mj/pnj/jargal"' in card


def test_lieutenants_point_to_checheg(client: TestClient, db: Session, world: World) -> None:
    run_import(db)
    orianna = dm_get(client, "/mj/pnj/orianna")
    assert "Supérieure : " in orianna and 'href="/mj/pnj/checheg"' in orianna
    assert "Relève de" in orianna
    sukhbat = client.get("/mj/pnj/sukhbat").text
    assert "Supérieure : " in sukhbat and 'href="/mj/pnj/checheg"' in sukhbat
    assert 'href="/mj/lieux/tomor-zar"' in sukhbat
    checheg = client.get("/mj/pnj/checheg").text
    assert "Lieutenante, exécutrice des dalhich : " in checheg
    assert "Lieutenant, agent des marchés : " in checheg
    assert 'href="/mj/pnj/orianna"' in checheg and 'href="/mj/pnj/sukhbat"' in checheg
    orianna_position = db.scalar(
        select(Position).where(Position.key == "khuchniin-khondii-orianna")
    )
    checheg_position = db.scalar(
        select(Position).where(Position.key == "khuchniin-khondii-khan-checheg")
    )
    assert orianna_position.superior_id == checheg_position.id
    assert 'href="/mj/pnj/sukhbat"' in client.get("/mj/lieux/tomor-zar").text


def test_dm_country_fiche_shows_the_dm_only_secret(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db, documents("joueurs"))
    country = dm_get(client, f"/mj/articles/{world.khaanat.wa_id}")
    assert COUNTRY_SECRET in country and "MJ seulement" in country
    assert COUNTRY_SECRET in client.get("/mj/lieux/khaanat").text
    client.cookies.clear()
    login(client, "alice")
    shared = client.get(f"/joueur/articles/{world.khaanat.wa_id}")
    assert shared.status_code == 200 and "Des steppes." in shared.text
    assert "chadvar" not in shared.text
    place = client.get("/joueur/lieux/khaanat")
    assert place.status_code == 200 and "chadvar" not in place.text


@pytest.mark.parametrize("visibility", ["mj", "joueurs"])
def test_secrets_and_stat_blocks_never_reach_players(
    client: TestClient, db: Session, world: World, visibility: str
) -> None:
    run_import(db, documents(visibility))
    login(client, "alice")
    pages = [
        client.get(f"/joueur/{path}")
        for path in (
            f"articles/{world.khaanat.wa_id}",
            "lieux/khaanat",
            "lieux/khara-suld",
            "lieux/khuchniin-khondii",
            "pnj/jargal",
            "pnj/checheg",
            "pnj/orianna",
            "pnj/sukhbat",
        )
    ]
    if visibility == "mj":
        assert all(page.status_code == 404 for page in pages[1:])
    else:
        assert all(page.status_code == 200 for page in pages)
        assert JARGAL_HISTORY in pages[4].text
    for page in pages:
        for hidden in (
            JARGAL_SECRET,
            ORIANNA_SECRET,
            "chadvar",
            "statblock",
            "Défi du Khan",
            "MJ seulement",
        ):
            assert hidden not in page.text, hidden
    for path in ("/mj/pnj/jargal", "/mj/lieux/khara-suld"):
        assert client.get(path).status_code == 404
    personnage = client.get("/joueur/articles?section=personnage").text
    assert "/pnj/" not in personnage
