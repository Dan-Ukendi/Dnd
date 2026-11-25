import copy
import re
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
from app.models import Place, Position, Role, User, WaArticle
from app.services.structure_excerpts import AnchorStatus, find_excerpt
from app.services.structure_import import import_structures, load_structure_file
from app.services.sync import sync_world
from tests.factories import bare_numbers, login, make_article, make_character, make_user
from tests.test_sync import WORLD, FakeSource

GUILD = """[h|g0]La Guilde[/h]
[p][i]Une guilde d'essai[/i][/p]
[h1|g1]Fiche rapide[/h1]
[ul]
- [b]Branche présentée :[/b] la ville haute
[/ul]
[h1|g2]Organisation interne[/h1]
[ul]
- [b]Le chef de branche :[/b] il dirige la Guilde de la ville.
- [b]Les réceptionnistes :[/b] Gronk tient le comptoir.
[/ul]
[h1|g3]Secrets[/h1]
[ul]
- [b]Le faux rang :[/b] les licences sont fausses.
[/ul]
[h1|g4]Événements de la session 1[/h1]
[p]La salle des trophées a brûlé.[/p]"""

CITY = """[h1|c0]La Cité[/h1]
[h2|c1]Gouvernement[/h2]
[p]La ville est la cour du Khan.[/p]
[h2|c2]Lieux notables[/h2]
[p][b]Le marché sombre[br][/b]Il se cache dans le dédale.[/p]
[p][b]La taverne[br][/b]Une immense taverne.[/p]
[h2|c3]Pistes pour la suite[/h2]
[p][b]La crypte[br][/b]Un passage oublié.[/p]"""

NATION = """[h|n0]Le Khaanat[/h]
[h1|n1]Géographie[/h1]
[p]Le Khaanat s'étend sur les terres centrales.[/p]
[h2|n2]Les Cours[/h2]
[ol]
- [b]KHARA[/b] (La Lance) - Cour du Guerrier
-- [b]Description[/b] : un camp fortifié.
- [b]BOO[/b] (La Montagne) - Cour du Mage
[/ol]"""


def documents(**overrides: Any) -> list[tuple[str, dict[str, Any]]]:
    places = [
        {
            "key": "khaanat",
            "name": "Le Khaanat",
            "kind": "territoire",
            "article": "Le Khaanat",
            "source": "Le Khaanat",
            "anchor": "Géographie",
            "visibility": "joueurs",
        },
        {
            "key": "khara",
            "name": "Khara",
            "kind": "ville",
            "parent": "khaanat",
            "source": "Le Khaanat",
            "anchor": "KHARA",
            "visibility": "joueurs",
        },
        {
            "key": "cite",
            "name": "La Cité",
            "kind": "ville",
            "parent": "khaanat",
            "article": "La Cité",
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "taverne",
            "name": "La taverne",
            "kind": "batiment",
            "parent": "cite",
            "source": "La Cité",
            "anchor": "La taverne",
            "location": "la ville haute",
            "visibility": "joueurs",
            "gm_notes": "Note secrète du MJ sur la taverne",
        },
        {
            "key": "cave",
            "name": "La cave",
            "kind": "piece",
            "parent": "taverne",
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "marche",
            "name": "Le marché sombre",
            "kind": "lieu",
            "parent": "cite",
            "owner": "La Nuit",
            "source": "La Cité",
            "anchor": "Le marché sombre",
        },
        {
            "key": "etal-cache",
            "name": "L'étal caché",
            "kind": "lieu",
            "parent": "marche",
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "branche",
            "name": "La branche de la Guilde",
            "kind": "batiment",
            "parent": "cite",
            "owner": "La Guilde",
            "source": "La Guilde",
            "anchor": "Branche présentée",
            "visibility": "joueurs",
        },
        {
            "key": "comptoir",
            "name": "Le comptoir",
            "kind": "piece",
            "parent": "branche",
            "source": "La Guilde",
            "visibility": "joueurs",
        },
    ]
    positions = [
        {
            "key": "guilde-chef",
            "organisation": "La Guilde",
            "title": "Chef de branche",
            "site": "branche",
            "head": True,
            "source": "La Guilde",
            "anchor": "Le chef de branche",
            "visibility": "joueurs",
        },
        {
            "key": "guilde-second-chef",
            "organisation": "La Guilde",
            "name": "Brunhild",
            "title": "Cheffe adjointe",
            "head": True,
            "source": "La Guilde",
            "visibility": "joueurs",
        },
        {
            "key": "guilde-gronk",
            "organisation": "La Guilde",
            "person": "Gronk",
            "title": "Réceptionniste",
            "site": "branche",
            "superior": "guilde-chef",
            "source": "La Guilde",
            "anchor": "Les réceptionnistes",
            "visibility": "joueurs",
            "gm_notes": "Incohérence : note réservée au MJ",
        },
        {
            "key": "guilde-espion",
            "organisation": "La Guilde",
            "name": "L'espion",
            "title": "Agent double",
            "superior": "guilde-chef",
            "source": "La Guilde",
        },
        {
            "key": "guilde-recrue",
            "organisation": "La Guilde",
            "name": "La recrue de l'espion",
            "title": "Apprentie",
            "superior": "guilde-espion",
            "source": "La Guilde",
            "visibility": "joueurs",
        },
        {
            "key": "guilde-bob",
            "organisation": "La Guilde",
            "person": "Bob",
            "title": "Indicateur",
            "source": "La Guilde",
            "visibility": "joueurs",
        },
        {
            "key": "cite-khan",
            "place": "cite",
            "name": "Le Khan",
            "title": "Maître de la cité",
            "head": True,
            "source": "La Cité",
            "anchor": "Gouvernement",
            "visibility": "joueurs",
        },
        {
            "key": "cite-conseiller",
            "place": "cite",
            "name": "Le conseiller",
            "title": "Conseiller",
            "superior": "cite-khan",
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "khara-khan",
            "place": "khara",
            "name": "Khan Baatar",
            "title": "Khan Guerrier",
            "source": "Le Khaanat",
            "anchor": "KHARA",
            "visibility": "joueurs",
        },
        {
            "key": "taverne-patron",
            "place": "taverne",
            "person": "Gronk",
            "title": "habitué de la taverne",
            "hierarchy": False,
            "source": "La Cité",
            "visibility": "joueurs",
        },
        {
            "key": "marche-veilleur",
            "place": "etal-cache",
            "name": "Le veilleur",
            "title": "Garde de l'étal",
            "hierarchy": False,
            "source": "La Cité",
            "visibility": "joueurs",
        },
    ]
    data = {"places": places, "positions": positions}
    data.update(overrides)
    return [("monde.toml", data)]


@dataclass
class World:
    dm: User
    alice: User
    guild: WaArticle
    nuit: WaArticle
    nation: WaArticle
    city: WaArticle
    gronk: WaArticle
    bob: WaArticle


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    guild = make_article(db, "La Guilde", content=GUILD, template_type="organization")
    nuit = make_article(db, "La Nuit", content="[p]Une pègre.[/p]", template_type="organization")
    nation = make_article(db, "Le Khaanat", content=NATION, template_type="location")
    city = make_article(db, "La Cité", content=CITY, template_type="settlement")
    gronk = make_article(db, "Gronk", content="[p]Un orc.[/p]")
    bob = make_article(db, "Bob", content="[p]Un voleur.[/p]", player_visible=False)
    main = make_article(db, "Alice la voleuse")
    make_character(db, "Alice", alice, main, [guild, nation, city, gronk, bob])
    return World(dm, alice, guild, nuit, nation, city, gronk, bob)


def run_import(db: Session, docs: list[tuple[str, dict[str, Any]]] | None = None, **kwargs: Any):
    lines = import_structures(db, docs if docs is not None else documents(), **kwargs)
    db.expire_all()
    return lines


def statuses(lines: list[Any]) -> dict[str, str]:
    return {line.label: line.status for line in lines if line.status != "warning"}


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_migration_creates_and_drops_both_tables(db: Session) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0011")
    try:
        assert not {"places", "positions"} & set(inspect(db.get_bind()).get_table_names())
    finally:
        command.upgrade(config, "head")
    assert {"places", "positions"} <= set(inspect(db.get_bind()).get_table_names())


def test_excerpts_follow_headings_and_labels_but_never_reserved_sections() -> None:
    heading = find_excerpt(CITY, "Gouvernement")
    assert heading.status == AnchorStatus.FOUND
    assert heading.source == "[p]La ville est la cour du Khan.[/p]"
    label = find_excerpt(CITY, "le marche sombre")
    assert label.source == "[p][b]Le marché sombre[br][/b]Il se cache dans le dédale.[/p]"
    item = find_excerpt(NATION, "KHARA")
    assert item.source.startswith("[ul]\n- [b]KHARA[/b]")
    assert "un camp fortifié" in item.source
    assert "BOO" not in item.source
    assert find_excerpt(GUILD, "Le chef de branche").source.endswith("de la ville.\n[/ul]")
    assert find_excerpt(GUILD, "Secrets").status == AnchorStatus.FORBIDDEN
    assert find_excerpt(GUILD, "Le faux rang").status == AnchorStatus.FORBIDDEN
    assert find_excerpt(GUILD, "Événements de la session 1").status == AnchorStatus.FORBIDDEN
    assert find_excerpt(CITY, "La crypte").status == AnchorStatus.FORBIDDEN
    assert find_excerpt(CITY, "Introuvable").status == AnchorStatus.MISSING


def test_import_is_idempotent_by_key(db: Session, world: World) -> None:
    first = run_import(db)
    assert set(statuses(first).values()) == {"created"}
    assert (count(db, Place), count(db, Position)) == (9, 11)
    ids = {row.key: row.id for row in db.scalars(select(Place))}
    second = run_import(db)
    assert set(statuses(second).values()) == {"unchanged"}
    assert {row.key: row.id for row in db.scalars(select(Place))} == ids
    changed = documents()
    changed[0][1]["places"][3]["location"] = "la ville basse"
    third = statuses(run_import(db, changed))
    assert third["place taverne"] == "updated"
    assert third["place cite"] == "unchanged"
    taverne = db.scalar(select(Place).where(Place.key == "taverne"))
    assert taverne.location == "la ville basse"
    assert taverne.parent_id == ids["cite"]
    gronk = db.scalar(select(Position).where(Position.key == "guilde-gronk"))
    chef = db.scalar(select(Position).where(Position.key == "guilde-chef"))
    assert gronk.superior_id == chef.id
    assert gronk.person_article_id == world.gronk.wa_id
    assert gronk.status == "a_valider"
    assert db.scalar(select(Place).where(Place.key == "marche")).visibility == "mj"


def test_dry_run_reports_without_writing(db: Session, world: World) -> None:
    lines = run_import(db, dry_run=True)
    assert set(statuses(lines).values()) == {"created"}
    assert all("[dry run]" in line.detail for line in lines)
    assert (count(db, Place), count(db, Position)) == (0, 0)


def test_rows_missing_from_the_files_are_deleted_only_with_prune(db: Session, world: World) -> None:
    run_import(db)
    docs = documents()
    docs[0][1]["places"] = [p for p in docs[0][1]["places"] if p["key"] != "comptoir"]
    docs[0][1]["positions"] = [p for p in docs[0][1]["positions"] if p["key"] != "guilde-bob"]
    kept = run_import(db, copy.deepcopy(docs))
    assert not any(line.status == "removed" for line in kept)
    assert (count(db, Place), count(db, Position)) == (9, 11)
    pruned = run_import(db, copy.deepcopy(docs), prune=True)
    assert {line.label for line in pruned if line.status == "removed"} == {
        "place comptoir",
        "position guilde-bob",
    }
    assert (count(db, Place), count(db, Position)) == (8, 10)


def test_prune_refuses_to_drop_a_referenced_row(db: Session, world: World) -> None:
    run_import(db)
    docs = documents()
    docs[0][1]["places"] = [p for p in docs[0][1]["places"] if p["key"] != "branche"]
    lines = run_import(db, docs, prune=True)
    assert any(line.failed and "would be pruned" in line.detail for line in lines)
    assert count(db, Place) == 9


@pytest.mark.parametrize(
    ("section", "index", "change", "message"),
    [
        ("positions", 2, {"person": "Personne inconnue"}, "not found"),
        ("positions", 2, {"anchor": "Le faux rang"}, "reserved to the DM"),
        ("positions", 2, {"superior": "cite-khan"}, "same organisation"),
        ("positions", 6, {"superior": "guilde-chef"}, "attached to a place"),
        ("positions", 0, {"superior": "guilde-gronk"}, "cycle"),
        ("places", 1, {"parent": "khara"}, "itself"),
        ("places", 0, {"parent": "cave"}, "cycle"),
        ("places", 2, {"kind": "chateau"}, "kind must be"),
        ("positions", 0, {"place": "cite"}, "exactly one"),
        ("places", 0, {"source": ""}, "'source' is required"),
        ("places", 0, {"colour": "rouge"}, "unknown fields"),
    ],
)
def test_invalid_files_are_rejected_as_a_whole(
    db: Session,
    world: World,
    section: str,
    index: int,
    change: dict[str, str],
    message: str,
) -> None:
    docs = documents()
    docs[0][1][section][index].update(change)
    lines = run_import(db, docs)
    assert any(line.failed and message in line.detail for line in lines), lines
    assert (count(db, Place), count(db, Position)) == (0, 0)


def test_missing_anchor_is_a_warning(db: Session, world: World) -> None:
    docs = documents()
    docs[0][1]["places"][1]["anchor"] = "Un intitulé disparu"
    lines = run_import(db, docs)
    assert any(line.status == "warning" and "not found" in line.detail for line in lines)
    assert not any(line.failed for line in lines)
    assert count(db, Place) == 9


def test_cli_imports_a_folder(db: Session, world: World, tmp_path: Path) -> None:
    folder = tmp_path / "structures"
    folder.mkdir()
    (folder / "lieux.toml").write_text(
        '[[places]]\nkey = "seul"\nname = "Un lieu"\nkind = "lieu"\nsource = "La Cité"\n',
        encoding="utf-8",
    )
    assert cli_main(["import-structures", str(folder), "--dry-run"]) == 0
    assert count(db, Place) == 0
    assert cli_main(["import-structures", str(folder)]) == 0
    assert load_structure_file(folder / "lieux.toml")["places"][0]["key"] == "seul"
    assert count(db, Place) == 1
    (folder / "bad.toml").write_text("[[lieux]]\n", encoding="utf-8")
    assert cli_main(["import-structures", str(folder)]) == 1


def test_sync_never_touches_places_or_positions(db: Session, world: World) -> None:
    run_import(db)

    def snapshot() -> list[tuple[Any, ...]]:
        rows = [
            *[
                (row.key, row.name, row.parent_id, row.wa_article_id, row.owner_id, row.updated_at)
                for row in db.scalars(select(Place).order_by(Place.key))
            ],
            *[
                (row.key, row.title, row.superior_id, row.person_article_id, row.updated_at)
                for row in db.scalars(select(Position).order_by(Position.key))
            ],
        ]
        db.expire_all()
        return rows

    before = snapshot()
    source = FakeSource()
    source.put(world.city.wa_id, "La Cité", "[p]Nouveau texte.[/p]", "2026-11-26 10:00:00")
    source.put(world.gronk.wa_id, "Gronk", "[p]Un orc.[/p]", "2026-11-26 10:00:00")
    assert sync_world(db, source, WORLD, full=True, trigger="cli").status == "success"
    db.expire_all()
    assert db.get(WaArticle, world.guild.wa_id).removed_at is not None
    assert snapshot() == before
    assert (count(db, Place), count(db, Position)) == (9, 11)


def _dm(client: TestClient) -> None:
    login(client, "maitre")


def _alice(client: TestClient) -> None:
    login(client, "alice")


def test_dm_organisation_page_has_territory_and_hierarchy_tabs(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    _dm(client)
    page = client.get(f"/mj/articles/{world.guild.wa_id}").text
    assert 'data-tab="territoire"' in page and 'data-tab="hierarchie"' in page
    assert 'id="structure-territoire"' in page and 'id="structure-hierarchie"' in page
    assert "La branche de la Guilde" in page and "Le comptoir" in page
    assert "Personnel" in page and "Réceptionniste" in page
    assert "Branche présentée" in page
    assert 'role="tree"' in page and 'role="treeitem"' in page and 'role="group"' in page
    assert 'aria-expanded="true"' in page and 'aria-level="2"' in page
    assert "Sommet</h3>" in page and "Sans supérieur défini</h3>" in page
    assert ">Territoire</button>" in page and ">Hiérarchie</button>" in page
    assert bare_numbers(page) == []
    assert "Brunhild" in page and "Titulaire non nommé" in page
    assert "Tout déplier" in page and "Tout replier" in page
    assert "Incohérence : note réservée au MJ" in page
    assert "À valider" in page and "MJ seulement" in page
    empty = client.get(f"/mj/articles/{world.nuit.wa_id}").text
    assert "Aucune hiérarchie consignée" in empty
    assert "Le marché sombre" in empty and "Aucun territoire consigné" not in empty


def test_dm_monde_page_navigates_levels_with_a_breadcrumb(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    _dm(client)
    page = client.get(f"/mj/articles/{world.nation.wa_id}").text
    assert 'aria-label="Niveau du territoire"' in page
    assert "Khara" in page and "La Cité" in page and "Explorer ce lieu" in page
    assert "Khan Baatar" in page and "Le Khan" in page and "Le conseiller" in page
    level = client.get(f"/mj/articles/{world.nation.wa_id}?lieu=cite").text
    crumbs = re.search(r'aria-label="Niveau du territoire">(.*?)</nav>', level, re.S).group(1)
    assert "Le Khaanat" in crumbs and 'aria-current="location">La Cité' in crumbs
    assert "La taverne" in level and "Le marché sombre" in level
    assert "Lieux contenus</h3>" in level
    assert bare_numbers(page) == [] and bare_numbers(level) == []
    assert "La Nuit" in level and "Note secrète du MJ sur la taverne" in level
    fragment = client.get(
        f"/mj/articles/{world.nation.wa_id}/territoire?lieu=taverne",
        headers={"HX-Request": "true"},
    )
    assert fragment.status_code == 200
    assert "<html" not in fragment.text and "La cave" in fragment.text
    assert "Gronk" in fragment.text and "habitué de la taverne" in fragment.text
    redirect = client.get(
        f"/mj/articles/{world.nation.wa_id}/territoire?lieu=taverne", follow_redirects=False
    )
    assert redirect.status_code == 303
    assert redirect.headers["location"].endswith("?lieu=taverne#territoire")
    missing = client.get(
        f"/mj/articles/{world.nation.wa_id}/territoire?lieu=inconnu",
        headers={"HX-Request": "true"},
    )
    assert missing.status_code == 404
    city = client.get(f"/mj/articles/{world.city.wa_id}").text
    assert "Maître de la cité" in city and "Khan Baatar" not in city


def test_monde_without_places_shows_an_empty_state(
    client: TestClient, db: Session, world: World
) -> None:
    _dm(client)
    page = client.get(f"/mj/articles/{world.city.wa_id}").text
    assert "Aucun lieu consigné" in page and "Aucune hiérarchie consignée" in page
    person = client.get(f"/mj/articles/{world.gronk.wa_id}").text
    assert 'data-tab="territoire"' not in person


def test_player_never_receives_dm_only_rows_or_their_subtrees(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    _alice(client)
    guild = client.get(f"/joueur/articles/{world.guild.wa_id}").text
    assert "La branche de la Guilde" in guild and "Le comptoir" in guild
    assert "Brunhild" in guild and "Gronk" in guild
    assert f'href="/joueur/articles/{world.gronk.wa_id}"' in guild
    assert "L'espion" not in guild and "La recrue de l'espion" not in guild
    assert "Bob" in guild and f"/joueur/articles/{world.bob.wa_id}" not in guild
    assert "Incohérence" not in guild and "À valider" not in guild
    assert "MJ seulement" not in guild and "Les licences sont fausses" not in guild
    level = client.get(f"/joueur/articles/{world.nation.wa_id}?lieu=cite").text
    assert "La taverne" in level and "La branche de la Guilde" in level
    assert "Le marché sombre" not in level and "L'étal caché" not in level
    assert "Le veilleur" not in level and "La Nuit" not in level
    assert "Note secrète du MJ" not in level
    hidden = client.get(
        f"/joueur/articles/{world.nation.wa_id}/territoire?lieu=etal-cache",
        headers={"HX-Request": "true"},
    )
    assert hidden.status_code == 404
    db.execute(Place.__table__.update().where(Place.key == "khaanat").values(visibility="mj"))
    db.commit()
    nation = client.get(f"/joueur/articles/{world.nation.wa_id}").text
    assert 'data-tab="territoire"' not in nation and "Khan Baatar" not in nation
    city = client.get(f"/joueur/articles/{world.city.wa_id}").text
    assert 'data-tab="territoire"' not in city and "Maître de la cité" not in city


def test_player_links_only_to_accessible_fiches_and_never_to_private_ones(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    nuit_link = f"/joueur/articles/{world.nuit.wa_id}"
    _alice(client)
    city = client.get(f"/joueur/articles/{world.nation.wa_id}?lieu=cite").text
    assert nuit_link not in city
    assert f"/joueur/articles/{world.city.wa_id}" in city
    assert "/mj/articles/" not in city
    assert client.get(f"/joueur/articles/{world.nuit.wa_id}").status_code == 404
    assert (
        client.get(
            f"/joueur/articles/{world.nuit.wa_id}/territoire", headers={"HX-Request": "true"}
        ).status_code
        == 404
    )


def test_player_without_visible_rows_gets_no_structure_tabs(
    client: TestClient, db: Session, world: World
) -> None:
    docs = documents()
    for section in ("places", "positions"):
        for entry in docs[0][1][section]:
            entry.pop("visibility", None)
    run_import(db, docs)
    _alice(client)
    page = client.get(f"/joueur/articles/{world.guild.wa_id}").text
    assert 'data-tab="territoire"' not in page and 'role="tree"' not in page
    assert "Chef de branche" not in page
    assert (
        client.get(
            f"/joueur/articles/{world.nation.wa_id}/territoire", headers={"HX-Request": "true"}
        ).status_code
        == 404
    )


def test_structure_markup_needs_no_inline_style_or_script(
    client: TestClient, db: Session, world: World
) -> None:
    run_import(db)
    _dm(client)
    page = client.get(f"/mj/articles/{world.nation.wa_id}?lieu=cite").text
    body = page[page.index('id="structure-territoire"') :]
    assert "style=" not in body and "<script" not in body and "onclick" not in body
