import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.credits import credit_for
from app.models import (
    LinkAlias,
    Npc,
    NpcRole,
    Place,
    Position,
    Role,
    StatBlock,
    User,
    WaArticle,
    WorldCategory,
    utcnow,
)
from app.services import world as world_service
from app.services.structure_import import import_structures
from tests.factories import bare_numbers, login, make_article, make_character, make_user

CLERGE = "Clergé d'Arès"
KRATEROS = "Kratéros"
CITY = "1. KHÜCHNIIN KHÖNDII La Vallée de la Volonté"
KHAANAT = "Le Khaanat des Cinq Lances"
TEMPLE_BLANC = "Le temple d'Arès (le Temple Blanc)"
TEMPLES = {
    "tertre-rouge": ("Le Tertre Rouge", "khara-suld"),
    "sanctuaire-des-cairns": ("Le Sanctuaire des Cairns", "boo-uul"),
    "temple-porte": ("Le Temple Porté", "nogoon-guru"),
    "cloitre-des-cent-huit-lances": ("Le Cloître des Cent Huit Lances", "tengeriin-tsetseg"),
    "temple-ares": (TEMPLE_BLANC, "khuchniin-khondii"),
}
ROOMS = {
    "temple-ares-nef": "La nef",
    "temple-ares-cour": "La cour d'entraînement",
    "temple-ares-infirmerie": "L'infirmerie",
    "temple-ares-salle-des-armes": "La salle des armes",
    "temple-ares-salle-du-conseil": "La salle du conseil",
    "temple-ares-cellules": "Les cellules",
    "temple-ares-crypte": "La crypte des champions",
}
KRATEROS_SECRET = "Seuls Berenike et Agathokles connaissent son vrai rang."
BERENIKE_NOTE = "Elle connaît le vrai rang de Kratéros."
BERENIKE_SHOWN = "Elle connaît le vrai rang de"
TEMPLE_BLANC_CONTENT = (
    f"[h]{TEMPLE_BLANC}[/h]\n[p][i]Le temple d'Arès de Khüchniin Khöndii[/i][/p]\n"
    "[h1]Présentation[/h1]\n[p]Chaque ville a son temple.[/p]\n"
    "[h1]Les lieux du temple[/h1]\n[ul]\n"
    + "".join(f"- [b]{name} :[/b] une pièce.\n" for name in ROOMS.values())
    + "[/ul]\n[h1]Personnel[/h1]\n[ul]\n"
    "- [b]Berenike :[/b] prélat président du conseil des prélats.\n"
    "- [b]Agathokles :[/b] capitaine de la Garde Blanche.\n"
    "- [b]La Garde Blanche :[/b] la milice sacrée du temple.\n"
    "- [b]Les prêtres, les acolytes et les initiés :[/b] le personnel courant du temple.\n"
    "[/ul]"
)


def clerge_content(temple_ids: dict[str, str]) -> str:
    rows = "".join(
        f"- [b]@[{title}](landmark:{temple_ids[key]}), Une ville :[/b] une phrase.\n"
        for key, (title, _) in TEMPLES.items()
    )
    return (
        "[h1]Fiche rapide[/h1]\n[ul]\n"
        "- [b]Temples :[/b] un dans chacune des cinq Cours du Khaanat des Cinq Lances\n[/ul]\n"
        f"[h1]Les temples du Khaanat[/h1]\n[ul]\n{rows}[/ul]\n"
        "[h1]Ordres et services[/h1]\n[h2]La Garde Blanche[/h2]\n[p]La milice sacrée.[/p]\n"
        "[h1]Membres notables[/h1]\n[ul]\n"
        "- [b]Kratéros, évêque d'Arès :[/b] le plus haut dignitaire du culte dans la ville.\n"
        "- [b]Le conseil des prélats :[/b] les dignitaires qui dirigent le temple.\n"
        "- [b]Kara, évêque d'Arès de Khara Suld :[/b] elle dirige le Tertre Rouge.\n[/ul]"
    )


def stat_blocks() -> list[dict[str, Any]]:
    def block(key: str, name: str, source: str, cr: str) -> dict[str, Any]:
        return {
            "key": key,
            "n": name,
            "tp": "Humanoïde de taille M",
            "ac": 16,
            "hp": 50,
            "ab": [16, 10, 14, 11, 16, 12],
            "cr": cr,
            "ac_": [{"n": "Multiattaque", "t": f"attaques de {name}."}],
            "src": source,
        }

    return [
        block("war-priest", "War Priest", "Monsters of the Multiverse p. 254", "9"),
        block("warrior-priest", "Warrior Priest", "Creature Codex (Kobold Press)", "8"),
        block("priest", "Priest", "SRD 5.1, Monster Manual", "2"),
        block("knight", "Knight", "5e Core Rules (SRD 5.1)", "3"),
        block("guard", "Guard", "5e Core Rules (SRD 5.1)", "1/8"),
    ]


def places() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"key": "khaanat", "name": KHAANAT, "kind": "territoire", "article": KHAANAT},
        *(
            {"key": key, "name": key.title(), "kind": "ville", "parent": "khaanat"}
            for key in ("khara-suld", "boo-uul", "nogoon-guru", "tengeriin-tsetseg")
        ),
        {
            "key": "khuchniin-khondii",
            "name": "Khüchniin Khöndii (La Vallée de la Volonté)",
            "kind": "ville",
            "parent": "khaanat",
            "article": CITY,
        },
    ]
    for key, (title, parent) in TEMPLES.items():
        rows.append(
            {
                "key": key,
                "name": title,
                "kind": "batiment",
                "parent": parent,
                "article": title,
                "owner": CLERGE,
                "source": title,
                "anchor": "Présentation" if key == "temple-ares" else "",
            }
        )
    rows += [
        {
            "key": key,
            "name": name,
            "kind": "piece",
            "parent": "temple-ares",
            "source": TEMPLE_BLANC,
            "anchor": name,
        }
        for key, name in ROOMS.items()
    ]
    for row in rows:
        row.setdefault("source", KHAANAT)
        if not row.get("anchor"):
            row.pop("anchor", None)
    return rows


def npcs() -> list[dict[str, Any]]:
    def npc(key: str, title: str, place: str, block: str, **extra: str) -> dict[str, Any]:
        return {
            "key": key,
            "name": key.capitalize(),
            "title": title,
            "organisation": CLERGE,
            "place": place,
            "statblock": block,
            "tag": "encounter",
            **extra,
        }

    return [
        {**npc("krateros", "Évêque d'Arès", "temple-ares", "war-priest"), "name": KRATEROS}
        | {"article": KRATEROS, "gm_notes": KRATEROS_SECRET},
        npc("kara", "Évêque d'Arès de Khara Suld", "tertre-rouge", "war-priest"),
        npc(
            "berenike",
            "Prélat président du conseil des prélats",
            "temple-ares",
            "warrior-priest",
            gm_notes=BERENIKE_NOTE,
        ),
        npc("agathokles", "Capitaine de la Garde Blanche", "temple-ares", "knight"),
    ]


def roles() -> list[dict[str, Any]]:
    return [
        {
            "key": "temple-blanc-gardes",
            "name": "Gardes blancs",
            "organisation": CLERGE,
            "place": "temple-ares",
            "headcount": "environ 60",
            "statblock": "guard",
            "description": "Guerriers de la Garde Blanche, en blanc et bronze.",
        },
        {
            "key": "temple-blanc-prelats",
            "name": "Autres prélats du conseil",
            "organisation": CLERGE,
            "place": "temple-ares",
            "headcount": "4",
            "statblock": "priest",
            "description": "Siègent au conseil aux côtés du prélat président.",
        },
    ]


def positions() -> list[dict[str, Any]]:
    def position(key: str, **fields: Any) -> dict[str, Any]:
        return {"key": key, "organisation": CLERGE, "site": "temple-ares", **fields}

    return [
        position(
            "clerge-krateros",
            person=KRATEROS,
            title="évêque d'Arès",
            head=True,
            source=CLERGE,
            anchor="Kratéros, évêque d'Arès",
        ),
        position(
            "clerge-berenike",
            npc="berenike",
            title="prélat président du conseil des prélats",
            superior="clerge-krateros",
            source=TEMPLE_BLANC,
            anchor="Berenike",
        ),
        position(
            "clerge-autres-prelats",
            npc_role="temple-blanc-prelats",
            title="siègent au conseil",
            superior="clerge-berenike",
            group=True,
            source=TEMPLE_BLANC,
        ),
        position(
            "clerge-agathokles",
            npc="agathokles",
            title="capitaine de la Garde Blanche",
            superior="clerge-krateros",
            source=TEMPLE_BLANC,
            anchor="Agathokles",
        ),
        position(
            "clerge-gardes-blancs",
            npc_role="temple-blanc-gardes",
            title="guerriers de la Garde Blanche",
            superior="clerge-agathokles",
            group=True,
            source=TEMPLE_BLANC,
        ),
        position(
            "clerge-kara",
            npc="kara",
            title="évêque d'Arès de Khara Suld",
            site="tertre-rouge",
            head=True,
            source=CLERGE,
            anchor="Kara, évêque d'Arès de Khara Suld",
        ),
    ]


def documents(visibility: str = "mj") -> list[tuple[str, dict[str, Any]]]:
    rows = {
        "places": places(),
        "npcs": npcs(),
        "roles": roles(),
        "positions": positions(),
    }
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
    clerge: WaArticle
    krateros: WaArticle
    temples: dict[str, WaArticle]


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    temples = {
        key: make_article(
            db,
            title,
            content=TEMPLE_BLANC_CONTENT if key == "temple-ares" else "[p]Un temple.[/p]",
            template_type="landmark",
            player_visible=False,
        )
        for key, (title, _) in TEMPLES.items()
    }
    ids = {key: article.wa_id for key, article in temples.items()}
    clerge = make_article(db, CLERGE, content=clerge_content(ids), template_type="organization")
    krateros = make_article(db, KRATEROS, content="[p]Un prêtre agenouillé.[/p]")
    city = make_article(db, CITY, template_type="settlement")
    khaanat = make_article(db, KHAANAT, template_type="organization")
    db.add(WorldCategory(wa_id=khaanat.wa_id, category="pays"))
    db.commit()
    world_service.ensure_categories(db)
    db.commit()
    shared = [clerge, krateros, city, khaanat, *temples.values()]
    make_character(db, "Alice", alice, make_article(db, "Alice la voleuse"), shared)
    return World(dm, alice, clerge, krateros, temples)


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


def territory_panel(page: str) -> str:
    start = page.index('id="structure-territoire-body"')
    return page[start : page.index('id="structure-hierarchie"', start)]


def test_clerge_import_is_idempotent(db: Session, world: World) -> None:
    first = run_import(db)
    assert not any(line.failed or line.status == "warning" for line in first), first
    assert set(statuses(first).values()) == {"created"}
    assert (count(db, Npc), count(db, NpcRole), count(db, StatBlock)) == (4, 2, 5)
    assert set(statuses(run_import(db)).values()) == {"unchanged"}
    krateros = db.scalar(select(Npc).where(Npc.key == "krateros"))
    assert krateros.wa_article_id == world.krateros.wa_id
    assert krateros.organisation_id == world.clerge.wa_id
    by_key = {row.key: row for row in db.scalars(select(Position))}
    assert by_key["clerge-berenike"].superior_id == by_key["clerge-krateros"].id
    assert by_key["clerge-autres-prelats"].superior_id == by_key["clerge-berenike"].id
    assert by_key["clerge-agathokles"].superior_id == by_key["clerge-krateros"].id
    assert by_key["clerge-kara"].is_head and by_key["clerge-krateros"].is_head
    temples = {row.key: row for row in db.scalars(select(Place).where(Place.kind == "batiment"))}
    assert set(temples) == set(TEMPLES)
    assert all(row.owner_id == world.clerge.wa_id for row in temples.values())
    assert {key: row.wa_article_id for key, row in temples.items()} == {
        key: article.wa_id for key, article in world.temples.items()
    }


def test_clerge_lists_exactly_the_five_temples_with_links(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    page = dm_get(client, f"/mj/articles/{world.clerge.wa_id}")
    panel = territory_panel(page)
    assert "Lieux contenus" in panel
    assert re.findall(r'data-place="([^"]+)"', panel) == list(TEMPLES)
    for key, article in world.temples.items():
        assert f'href="/mj/articles/{article.wa_id}"' in panel, key
    for name in ROOMS.values():
        assert name not in panel, name
    assert "Personnel" not in panel and "lieux</span>" not in panel
    assert "Bâtiment" in panel and bare_numbers(panel) == []
    for article in world.temples.values():
        assert f'href="/mj/articles/{article.wa_id}"' in page


def test_the_seven_rooms_and_the_personnel_live_under_the_temple_blanc(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    temple = db.scalar(select(Place).where(Place.key == "temple-ares"))
    rooms = db.scalars(select(Place).where(Place.kind == "piece")).all()
    assert {room.key for room in rooms} == set(ROOMS)
    assert {room.parent_id for room in rooms} == {temple.id}
    place = dm_get(client, "/mj/lieux/temple-ares")
    for key in ROOMS:
        assert f'href="/mj/lieux/{key}"' in place, key
    for expected in (
        'id="hierarchie"',
        f'href="/mj/articles/{world.krateros.wa_id}"',
        'href="/mj/pnj/berenike"',
        'href="/mj/pnj/agathokles"',
        'href="/mj/roles/temple-blanc-gardes"',
    ):
        assert expected in place, expected
    tree = place[place.index('id="hierarchie"') :]
    assert ">Le conseil des prélats<" not in tree and ">La Garde Blanche<" not in tree
    fiche = client.get(f"/mj/articles/{world.temples['temple-ares'].wa_id}").text
    panel = territory_panel(fiche)
    for name in ROOMS.values():
        assert name.replace("'", "&#39;") in panel, name
    assert "Personnes notables" in panel and "capitaine de la Garde Blanche" in panel
    assert ">La Garde Blanche<" not in panel
    assert f'href="/mj/articles/{world.krateros.wa_id}"' in panel
    buildings = client.get("/mj/articles?section=monde&categorie=batiments").text
    for title, _ in TEMPLES.values():
        assert title.replace("'", "&#39;") in buildings, title
    tertre = client.get(f"/mj/articles/{world.temples['tertre-rouge'].wa_id}").text
    assert KHAANAT in tertre


def test_npcs_and_groups_render_with_stat_blocks_for_the_dm(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    berenike = dm_get(client, "/mj/pnj/berenike")
    for expected in (
        "Prélat président du conseil des prélats",
        '<span class="pill pill--encounter">Rencontre campagne</span>',
        'class="statblock"',
        "Warrior Priest",
        "Creature Codex (Kobold Press) · Kobold Press",
        BERENIKE_SHOWN,
        'href="/mj/lieux/temple-ares"',
        "Relève de",
    ):
        assert expected in berenike, expected
    kara = client.get("/mj/pnj/kara").text
    assert "War Priest" in kara and "Wizards of the Coast" in kara
    assert 'href="/mj/lieux/tertre-rouge"' in kara
    gardes = client.get("/mj/roles/temple-blanc-gardes").text
    for expected in ("Gardes blancs", "Effectif", "environ 60", "Guard", "FP 1/8"):
        assert expected in gardes, expected
    krateros = client.get(f"/mj/articles/{world.krateros.wa_id}").text
    assert "War Priest" in krateros and KRATEROS_SECRET in krateros
    assert (
        "/mj/pnj/krateros" not in client.get("/mj/articles?section=personnage&tag=encounter").text
    )
    credits = client.get("/credits").text
    assert "Creature Codex" in credits and "Monsters of the Multiverse" in credits


def test_credits_follow_the_stat_block_sources() -> None:
    assert credit_for("Monsters of the Multiverse p. 254").key == "monsters-of-the-multiverse"
    assert credit_for("Creature Codex (Kobold Press)").key == "creature-codex"
    assert credit_for("SRD 5.1, Monster Manual").key == "srd-5-1"


@pytest.mark.parametrize("visibility", ["mj", "joueurs"])
def test_nothing_dm_only_reaches_players(
    client: TestClient, db: Session, world: World, visibility: str
) -> None:
    run_import(db, documents(visibility))
    login(client, "alice")
    clerge = client.get(f"/joueur/articles/{world.clerge.wa_id}")
    assert clerge.status_code == 200 and "Le Tertre Rouge" in clerge.text
    krateros = client.get(f"/joueur/articles/{world.krateros.wa_id}")
    assert krateros.status_code == 200
    pages = [clerge.text, krateros.text]
    for article in world.temples.values():
        assert f"/articles/{article.wa_id}" not in clerge.text
        assert client.get(f"/joueur/articles/{article.wa_id}").status_code == 404
    paths = (
        "pnj/krateros",
        "pnj/berenike",
        "pnj/kara",
        "roles/temple-blanc-gardes",
        "lieux/temple-ares",
        "lieux/tertre-rouge",
        "lieux/temple-ares-crypte",
    )
    for path in paths:
        response = client.get(f"/joueur/{path}")
        pages.append(response.text)
        assert response.status_code == (404 if visibility == "mj" else 200), path
    if visibility == "mj":
        assert "Lieux contenus" not in clerge.text and "Berenike" not in clerge.text
    for page in pages:
        for hidden in (
            KRATEROS_SECRET,
            BERENIKE_SHOWN,
            "statblock",
            "War Priest",
            "Warrior Priest",
            "MJ seulement",
            "Notes du MJ",
        ):
            assert hidden not in page, hidden
    for path in ("/mj/pnj/berenike", "/mj/lieux/temple-ares"):
        assert client.get(path).status_code == 404


@pytest.fixture
def rerun_migration(db: Session) -> Iterator[Any]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False

    def rerun() -> None:
        db.commit()
        command.downgrade(config, "0018")
        command.upgrade(config, "head")

    yield rerun
    command.upgrade(config, "head")


def test_migration_removes_the_noblesse_thar_gath_fiche(db: Session, rerun_migration: Any) -> None:
    thar_gath = "0fcf5a36-a03a-455f-ab12-0c843d2a6543"
    db.add(
        WaArticle(
            wa_id=thar_gath,
            title="Noblesse Thar-Gath",
            template_type="profession",
            raw={},
            removed_at=utcnow(),
        )
    )
    db.commit()
    other = make_article(db, CLERGE, linked_ids=[thar_gath])
    db.add(LinkAlias(alias="Thar-Gath", wa_article_id=thar_gath))
    db.commit()
    rerun_migration()
    db.expire_all()
    assert db.get(WaArticle, thar_gath) is None
    assert count(db, LinkAlias) == 0
    assert db.get(WaArticle, other.wa_id).linked_ids == []
