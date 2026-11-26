import json
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
    FicheSection,
    Npc,
    NpcRelation,
    NpcRole,
    Place,
    Position,
    Role,
    StatBlock,
    User,
    WaArticle,
)
from app.services.structure_import import import_structures, load_structure_file
from tests.factories import login, make_article, make_character, make_user

SECRET = "Une trappe mène aux galeries inférieures."
NPC_NOTE = "Il ne pardonnera pas le vol de l'amulette."
NPC_SECRET = "Elle a refusé un seul contrat."
STAT_TEXT = "the boss attacks three times with their shortsword."


def stat_blocks() -> list[dict[str, Any]]:
    return [
        {
            "key": "crime-boss-a5e",
            "n": "Crime Boss",
            "sz": "Medium",
            "tp": "humanoid",
            "al": "any alignment",
            "ac": 15,
            "hp": 127,
            "hd": "15d8+60",
            "spd": "30 ft.",
            "ab": [18, 14, 18, 16, 16, 9],
            "sk": "Deception +6",
            "se": "passive Perception 16",
            "la": "any two",
            "cr": "6",
            "ac_": [
                {"n": "Multiattack", "t": STAT_TEXT},
                {
                    "n": "Shortsword",
                    "t": "Melee Weapon Attack, +7 to hit, reach 5 ft., one target. "
                    "Hit: 7 (1d6 + 4) piercing damage.",
                },
            ],
            "re": [{"n": "Offhand Dagger", "t": "when missed, the boss makes a dagger attack."}],
            "src": "Level Up Advanced 5e Monstrous Menagerie",
        },
        {
            "key": "guard",
            "n": "Guard",
            "sz": "Medium",
            "tp": "humanoid (any race)",
            "al": "any alignment",
            "ac": 16,
            "acd": "chain shirt, shield",
            "hp": 11,
            "hd": "2d8+2",
            "spd": "30 ft.",
            "ab": [13, 12, 12, 10, 11, 10],
            "tr": [
                {
                    "n": "Spellcasting",
                    "t": "1st-level spellcaster.",
                    "sp": [["Cantrips (at will)", "light, sacred flame"]],
                }
            ],
            "cr": "1/8",
            "src": "5e Core Rules (SRD 5.1)",
        },
    ]


def places() -> list[dict[str, Any]]:
    return [
        {
            "key": "cite",
            "name": "La Cité",
            "kind": "ville",
            "article": "La Cité",
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "marche",
            "previous_keys": ["ancien-marche"],
            "name": "Le marché",
            "kind": "lieu",
            "parent": "cite",
            "owner": "La Nuit",
            "source": "La Nuit",
            "description": "Le cœur visible de la Nuit.",
            "visibility": "joueurs",
            "gm_notes": "Note du MJ sur le marché",
            "sections": [
                {"title": "Le lieu", "text": "Une caverne immense.", "visibility": "joueurs"},
                {"title": "Les règles", "text": "• La paix du marché.\n• La dîme."},
                {"title": "Secrets", "text": SECRET, "visibility": "joueurs"},
                {"title": "Coulisses", "text": "Texte réservé.", "visibility": "mj"},
            ],
        },
        {
            "key": "chemin",
            "name": "Le chemin des initiés",
            "kind": "piece",
            "parent": "marche",
            "owner": "La Nuit",
            "source": "La Nuit",
            "visibility": "mj",
            "sections": [{"title": "Le lieu", "text": "Un passage dérobé."}],
        },
        {
            "key": "rangee",
            "name": "La Rangée des Reliques",
            "kind": "lieu",
            "parent": "marche",
            "owner": "La Nuit",
            "source": "La Nuit",
            "description": "Des vitrines de verre.",
            "visibility": "joueurs",
        },
    ]


def npcs() -> list[dict[str, Any]]:
    return [
        {
            "key": "erdene",
            "name": "Erdene",
            "gender": "homme",
            "race": "Nain",
            "title": "l'Ezen du Grand Étal",
            "organisation": "La Nuit",
            "statblock": "crime-boss-a5e",
            "tag": "lore",
            "visibility": "joueurs",
            "description": "Un nain trapu aux mains épaisses.",
            "gm_notes": NPC_NOTE,
            "sections": [{"title": "Secret", "text": NPC_SECRET}],
        },
        {
            "key": "maren",
            "name": "Maren",
            "title": "Intendante du Grand Étal",
            "organisation": "La Nuit",
            "statblock": "guard",
            "visibility": "mj",
            "description": "Une femme mince.",
            "relations": [{"npc": "erdene", "label": "Supérieur", "reverse_label": "Intendante"}],
        },
    ]


def roles() -> list[dict[str, Any]]:
    return [
        {
            "key": "veilleur",
            "name": "Veilleur",
            "organisation": "La Nuit",
            "statblock": "guard",
            "description": "Garde posté au bout d'une rangée.",
            "visibility": "joueurs",
        },
        {
            "key": "mage",
            "name": "Mage du marché",
            "organisation": "La Nuit",
            "statblock": "guard",
            "description": "Lecteur de traces.",
        },
    ]


def positions() -> list[dict[str, Any]]:
    return [
        {
            "key": "conseil",
            "organisation": "La Nuit",
            "name": "Le conseil",
            "head": True,
            "group": True,
            "source": "La Nuit",
            "visibility": "joueurs",
        },
        {
            "key": "pos-erdene",
            "organisation": "La Nuit",
            "name": "Erdene",
            "title": "l'Ezen du Grand Étal",
            "superior": "conseil",
            "site": "marche",
            "npc": "erdene",
            "source": "La Nuit",
            "visibility": "joueurs",
        },
        {
            "key": "pos-maren",
            "organisation": "La Nuit",
            "name": "Maren",
            "superior": "pos-erdene",
            "site": "marche",
            "npc": "maren",
            "source": "La Nuit",
            "visibility": "mj",
        },
        {
            "key": "pos-veilleurs",
            "previous_keys": ["ancien-veilleurs"],
            "organisation": "La Nuit",
            "name": "Les veilleurs",
            "superior": "pos-erdene",
            "site": "marche",
            "group": True,
            "npc_role": "veilleur",
            "source": "La Nuit",
            "visibility": "joueurs",
        },
        {
            "key": "pos-mage",
            "organisation": "La Nuit",
            "name": "Le mage du marché",
            "superior": "pos-erdene",
            "site": "marche",
            "npc_role": "mage",
            "source": "La Nuit",
            "visibility": "mj",
        },
    ]


def documents() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("statblocks.json", {"statblocks": stat_blocks()}),
        ("nuit.toml", {"places": places(), "positions": positions()}),
        ("npcs.toml", {"npcs": npcs(), "roles": roles()}),
    ]


@dataclass
class World:
    dm: User
    alice: User
    nuit: WaArticle
    city: WaArticle


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    nuit = make_article(db, "La Nuit", content="[p]Une pègre.[/p]", template_type="organization")
    city = make_article(db, "La Cité", content="[p]Une ville.[/p]", template_type="settlement")
    main = make_article(db, "Alice la voleuse")
    make_character(db, "Alice", alice, main, [nuit, city])
    return World(dm, alice, nuit, city)


def run_import(db: Session, docs: list[tuple[str, dict[str, Any]]] | None = None, **kwargs: Any):
    lines = import_structures(db, docs if docs is not None else documents(), **kwargs)
    db.expire_all()
    return lines


def statuses(lines: list[Any]) -> dict[str, str]:
    return {line.label: line.status for line in lines if line.status != "warning"}


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def dm_client(client: TestClient) -> TestClient:
    login(client, "maitre")
    return client


def player_client(client: TestClient) -> TestClient:
    login(client, "alice")
    return client


def test_migration_creates_and_drops_the_npc_tables(db: Session) -> None:
    tables = {"stat_blocks", "npcs", "npc_roles", "fiche_sections", "npc_relations"}
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0017")
    try:
        assert not tables & set(inspect(db.get_bind()).get_table_names())
        columns = {column["name"] for column in inspect(db.get_bind()).get_columns("positions")}
        assert not {"npc_id", "npc_role_id"} & columns
    finally:
        command.upgrade(config, "head")
    assert tables <= set(inspect(db.get_bind()).get_table_names())


def test_dry_run_reports_everything_without_writing(db: Session, world: World) -> None:
    lines = run_import(db, dry_run=True)
    assert not any(line.failed for line in lines)
    assert all(line.detail.endswith("[dry run]") for line in lines)
    assert {line.status for line in lines} == {"created"}
    assert statuses(lines)["statblock guard"] == "created"
    assert statuses(lines)["npc erdene"] == "created"
    assert statuses(lines)["role veilleur"] == "created"
    for model in (StatBlock, Place, Npc, NpcRole, Position, FicheSection, NpcRelation):
        assert count(db, model) == 0


def test_import_is_idempotent(db: Session, world: World) -> None:
    first = run_import(db)
    assert set(statuses(first).values()) == {"created"}
    assert (count(db, StatBlock), count(db, Place), count(db, Npc)) == (2, 4, 2)
    assert (count(db, NpcRole), count(db, Position)) == (2, 5)
    assert (count(db, FicheSection), count(db, NpcRelation)) == (6, 1)
    second = run_import(db)
    assert set(statuses(second).values()) == {"unchanged"}
    assert (count(db, FicheSection), count(db, NpcRelation)) == (6, 1)
    docs = documents()
    docs[1][1]["places"][1]["sections"][0]["text"] = "Une caverne plus vaste encore."
    third = statuses(run_import(db, docs))
    assert third["place marche"] == "updated"
    assert third["place cite"] == "unchanged"
    assert count(db, FicheSection) == 6


def test_links_resolve_to_npcs_roles_and_stat_blocks(db: Session, world: World) -> None:
    run_import(db)
    erdene = db.scalar(select(Npc).where(Npc.key == "erdene"))
    block = db.get(StatBlock, erdene.stat_block_id)
    assert (block.key, block.armor_class, block.abilities) == (
        "crime-boss-a5e",
        15,
        stat_blocks()[0]["ab"],
    )
    assert block.actions[0] == {"name": "Multiattack", "text": STAT_TEXT}
    assert erdene.organisation_id == world.nuit.wa_id and erdene.category == "lore"
    position = db.scalar(select(Position).where(Position.key == "pos-erdene"))
    assert position.npc_id == erdene.id
    veilleurs = db.scalar(select(Position).where(Position.key == "pos-veilleurs"))
    assert db.get(NpcRole, veilleurs.npc_role_id).key == "veilleur"
    guard = db.scalar(select(StatBlock).where(StatBlock.key == "guard"))
    assert guard.traits[0]["spells"] == [["Cantrips (at will)", "light, sacred flame"]]


def test_secret_and_hook_sections_are_always_dm_only(db: Session, world: World) -> None:
    run_import(db)
    rows = {row.title: row.visibility for row in db.scalars(select(FicheSection))}
    assert rows["Secrets"] == "mj"
    assert rows["Secret"] == "mj"
    assert rows["Les règles"] == "joueurs"


def test_previous_keys_rename_the_existing_row(db: Session, world: World) -> None:
    docs = documents()
    docs[1][1]["places"][1]["key"] = "ancien-marche"
    docs[1][1]["places"][1].pop("previous_keys")
    for item in docs[1][1]["places"][2:]:
        item["parent"] = "ancien-marche"
    for item in docs[1][1]["positions"][1:]:
        item["site"] = "ancien-marche"
    run_import(db, docs)
    old_id = db.scalar(select(Place.id).where(Place.key == "ancien-marche"))
    lines = statuses(run_import(db))
    assert lines["place marche"] == "updated"
    assert db.scalar(select(Place.id).where(Place.key == "marche")) == old_id
    assert db.scalar(select(Place).where(Place.key == "ancien-marche")) is None
    assert count(db, Place) == 4
    assert set(statuses(run_import(db)).values()) == {"unchanged"}


def test_invalid_npc_references_fail_the_whole_run(db: Session, world: World) -> None:
    docs = documents()
    docs[2][1]["npcs"][0]["statblock"] = "missing"
    docs[1][1]["positions"][1]["person"] = "La Cité"
    lines = run_import(db, docs)
    failed = {line.label: line.detail for line in lines if line.failed}
    assert "position pos-erdene" in failed
    assert "at most one of" in failed["position pos-erdene"]
    assert count(db, Npc) == 0 and count(db, Place) == 0


def test_prune_removes_npcs_only_when_npc_files_are_given(db: Session, world: World) -> None:
    run_import(db)
    docs = documents()
    docs[2][1]["roles"] = docs[2][1]["roles"][:1]
    docs[1][1]["positions"] = docs[1][1]["positions"][:4]
    removed = [line.label for line in run_import(db, docs, prune=True) if line.status == "removed"]
    assert removed == ["position pos-mage", "role mage"]
    structures_only = [(name, data) for name, data in docs if name == "nuit.toml"]
    lines = run_import(db, structures_only, prune=True)
    assert not any(line.failed for line in lines)
    assert count(db, Npc) == 2 and count(db, StatBlock) == 2


def test_cli_reads_json_stat_blocks(db: Session, world: World, tmp_path: Path) -> None:
    folder = tmp_path / "npcs"
    folder.mkdir()
    (folder / "statblocks.json").write_text(json.dumps(stat_blocks()), encoding="utf-8")
    assert (
        load_structure_file(folder / "statblocks.json")["statblocks"][0]["key"] == "crime-boss-a5e"
    )
    assert cli_main(["import-structures", str(folder), "--dry-run"]) == 0
    assert count(db, StatBlock) == 0
    assert cli_main(["import-structures", str(folder)]) == 0
    db.expire_all()
    assert count(db, StatBlock) == 2


def test_dm_place_page_shows_every_section_children_and_positions(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    page = dm_client(client).get("/mj/lieux/marche")
    assert page.status_code == 200
    text = page.text
    for expected in (
        "Le cœur visible de",
        "Une caverne immense.",
        "<li>La paix du marché.</li>",
        SECRET,
        "Texte réservé.",
        "Note du MJ sur le marché",
        "Lieux contenus",
        'href="/mj/lieux/chemin"',
        'href="/mj/lieux/rangee"',
        "Postes liés",
        'href="/mj/pnj/erdene"',
        'href="/mj/roles/veilleur"',
        'href="/mj/lieux/cite"',
        "MJ seulement",
    ):
        assert expected in text, expected
    assert "style=" not in text[text.index("<main") :] and "onclick" not in text


def test_player_place_page_hides_dm_sections_rows_and_notes(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    player_client(client)
    text = client.get("/joueur/lieux/marche").text
    assert "Une caverne immense." in text and "La paix du marché." in text
    for hidden in (SECRET, "Texte réservé.", "Note du MJ", "MJ seulement", "Le chemin des initiés"):
        assert hidden not in text, hidden
    assert 'href="/joueur/pnj/erdene"' in text
    assert 'href="/joueur/roles/veilleur"' in text
    assert "Maren" not in text and "Le mage du marché" not in text
    assert client.get("/joueur/lieux/chemin").status_code == 404
    assert client.get("/joueur/lieux/inconnu").status_code == 404


def test_player_needs_an_accessible_fiche_to_open_a_place(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    bob = make_user(db, "bob")
    make_character(db, "Bob", bob, make_article(db, "Bob le voleur"))
    login(client, "bob")
    assert client.get("/joueur/lieux/marche").status_code == 404
    assert client.get("/joueur/pnj/erdene").status_code == 404
    assert client.get("/joueur/roles/veilleur").status_code == 404


def test_place_without_sections_has_no_empty_block(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    for viewer, path in ((dm_client, "/mj/lieux/rangee"), (player_client, "/joueur/lieux/rangee")):
        text = viewer(client).get(path).text
        assert "Des vitrines de verre." in text
        assert "fiche-section" not in text
        assert "Lieux contenus" not in text and "Postes liés" not in text


def test_dm_npc_page_shows_stat_block_notes_and_secrets(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    dm_client(client)
    text = client.get("/mj/pnj/erdene").text
    for expected in (
        "Un nain trapu",
        NPC_NOTE,
        NPC_SECRET,
        'class="statblock"',
        "Crime Boss",
        "The boss attacks three times",
        "<em>Melee Weapon Attack</em>",
        "<em>Hit:</em>",
        "18 (+4)",
        "9 (−1)",
        "FP 6",
        "Level Up Advanced 5e Monstrous Menagerie",
        "EN Publishing",
        'href="/credits#credit-a5e"',
        "Intendante : ",
        'href="/mj/pnj/maren"',
        "l&#39;Ezen du Grand Étal",
    ):
        assert expected in text, expected
    maren = client.get("/mj/pnj/maren").text
    assert "Supérieur : " in maren and "Guard" in maren
    role = client.get("/mj/roles/mage").text
    assert 'class="statblock"' in role and "Lecteur de traces." in role
    guard = client.get("/mj/roles/veilleur").text
    assert "Cantrips (at will)" in guard and "CC BY 4.0" in guard


def test_player_npc_and_role_pages_never_show_dm_content(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    player_client(client)
    text = client.get("/joueur/pnj/erdene").text
    assert "Un nain trapu" in text
    for hidden in (NPC_NOTE, NPC_SECRET, "statblock", "Crime Boss", "Maren", "MJ seulement"):
        assert hidden not in text, hidden
    assert client.get("/joueur/pnj/maren").status_code == 404
    role = client.get("/joueur/roles/veilleur").text
    assert "Garde posté" in role and "statblock" not in role and "Guard" not in role
    assert client.get("/joueur/roles/mage").status_code == 404


def test_player_never_reaches_dm_routes(client: TestClient, db: Session, world: World) -> None:
    run_import(db)
    player_client(client)
    for path in ("/mj/lieux/marche", "/mj/pnj/erdene", "/mj/roles/veilleur"):
        assert client.get(path).status_code == 404


def test_lieux_shortcut_redirects_by_role(client: TestClient, db: Session, world: World) -> None:
    run_import(db)
    assert client.get("/lieux/marche", follow_redirects=False).headers["location"] == "/"
    dm_client(client)
    response = client.get("/lieux/marche", follow_redirects=False)
    assert response.headers["location"] == "/mj/lieux/marche"


def test_place_card_name_opens_the_place_page(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    dm_client(client)
    text = client.get(f"/mj/articles/{world.nuit.wa_id}").text
    assert 'class="place-card__name place-card__name--link" href="/mj/lieux/marche"' in text
    assert '<details class="place-card" data-place="marche">' in text
    assert "Une caverne immense." not in text
    assert 'href="/mj/pnj/erdene"' in text


def test_app_npcs_join_the_lore_tag_for_the_dm_only(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    dm_client(client)
    tiles = client.get("/mj/articles?section=personnage").text
    assert "2 fiches" in tiles
    lore = client.get("/mj/articles?section=personnage&tag=lore").text
    assert 'href="/mj/pnj/erdene"' in lore and 'href="/mj/pnj/maren"' in lore
    assert "2 fiches" in lore
    found = client.get("/mj/articles?section=personnage&tag=lore&q=intendante").text
    assert 'href="/mj/pnj/maren"' in found and 'href="/mj/pnj/erdene"' not in found
    encounter = client.get("/mj/articles?section=personnage&tag=encounter").text
    assert "/mj/pnj/" not in encounter
    client.cookies.clear()
    player_client(client)
    assert "/pnj/" not in client.get("/joueur/articles?section=personnage").text
    assert client.get("/joueur/articles?section=personnage&tag=lore").status_code == 404


def test_npc_sheet_on_a_world_anvil_person_is_dm_only(
    client: TestClient, db: Session, world: World
) -> None:
    person = make_article(db, "Kratéros", content="[p]Un évêque.[/p]")
    make_character(db, "Alice bis", world.alice, None, [person])
    docs = documents()
    docs[2][1]["npcs"].append(
        {
            "key": "krateros",
            "name": "Kratéros",
            "article": "Kratéros",
            "statblock": "guard",
            "gm_notes": "Son vrai rang est caché.",
        }
    )
    run_import(db, docs)
    dm_client(client)
    text = client.get(f"/mj/articles/{person.wa_id}").text
    assert 'class="statblock"' in text and "Son vrai rang est caché." in text
    assert (
        'href="/mj/pnj/krateros"' not in client.get("/mj/articles?section=personnage&tag=lore").text
    )
    client.cookies.clear()
    player_client(client)
    shared = client.get(f"/joueur/articles/{person.wa_id}").text
    assert "Un évêque." in shared
    assert "statblock" not in shared and "Son vrai rang" not in shared


def test_credits_page_lists_the_licences(client: TestClient, db: Session, world: World) -> None:
    assert client.get("/credits", follow_redirects=False).status_code == 303
    player_client(client)
    text = client.get("/credits").text
    for expected in (
        "System Reference Document 5.1",
        "Creative Commons Attribution 4.0",
        "Level Up Advanced 5th Edition, Monstrous Menagerie",
        "EN Publishing",
        "Black Flag SRD",
        "Kobold Press",
        "Open5e",
        'id="credit-srd-5-1"',
    ):
        assert expected in text, expected
    assert 'href="/credits"' in client.get("/joueur").text
