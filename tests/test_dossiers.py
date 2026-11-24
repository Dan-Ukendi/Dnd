import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.models import Character, CharacterArticle, CharacterRelation, Role, WaArticle
from app.services.access import dm_render_context
from app.services.dossiers import DossierError, build_dossier, import_dossier
from tests.factories import login, make_article, make_character, make_user

DOSSIER: dict[str, Any] = {
    "character": "Khaetar Voss",
    "history": {
        "backstory": "Né orphelin dans le Khaanat.\n\nUn [b]pacte[/b] <script>alert(1)</script>",
        "events": [
            {"session": "Séance 1", "title": "L'amulette du marché noir", "text": "Dispute."},
            {"title": "Le Temple Blanc"},
        ],
    },
    "relations": [
        {"category": "Ennemis", "name": "La foule du lynchage"},
        {"category": "family", "name": "Mère inconnue", "role": "Mère", "status": "Disparue"},
        {"category": "Mentors", "name": "Le vieux prêtre"},
        {"category": "connaissances", "name": "Kratéros", "article": "Kratéros", "text": "Évêque."},
    ],
    "inventory": [
        {"name": "Pot en fer", "quantity": 1, "category": "Équipement"},
        {"name": "Amulette bénie", "article": "Le Gage d'Arès", "equipped": True},
    ],
}


@dataclass
class Table:
    keator: Character
    zilvra: Character
    krateros: WaArticle
    khaetar: WaArticle


@pytest.fixture
def table(db: Session) -> Table:
    make_user(db, "maitre", Role.DM)
    keator = make_user(db, "keator")
    zilvra = make_user(db, "zilvra")
    khaetar = make_article(db, "Khaetar Voss", content="[p]Occultiste tieffelin.[/p]")
    krateros = make_article(db, "Kratéros")
    make_article(db, "Le Gage d'Arès", template_type="item")
    return Table(
        make_character(db, "Khaetar Voss", keator, khaetar),
        make_character(db, "Zilvra Vhaelor", zilvra, make_article(db, "Zilvra")),
        krateros,
        khaetar,
    )


def test_import_creates_the_dossier(db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(DOSSIER), "khaetar.toml")
    assert character.id == table.keator.id
    assert character.profile is not None
    assert character.profile.source_file == "khaetar.toml"
    assert [event.title for event in character.events] == [
        "L'amulette du marché noir",
        "Le Temple Blanc",
    ]
    assert [relation.category for relation in character.relations] == [
        "ennemis",
        "famille",
        "mentors",
        "connaissances",
    ]
    assert character.relations[3].wa_article_id == table.krateros.wa_id
    assert [item.equipped for item in character.items] == [False, True]


def test_relation_groups_follow_the_category_order(db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(DOSSIER))
    dossier = build_dossier(character, dm_render_context(db), lambda article: "/x")
    assert [group.label for group in dossier.relation_groups] == [
        "Famille",
        "Connaissances",
        "Ennemis",
        "Mentors",
    ]
    assert dossier.relation_count == 4
    assert "<script" not in dossier.backstory
    assert "<strong>pacte</strong>" in dossier.backstory


def test_reimport_replaces_the_previous_dossier(db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(DOSSIER))
    import_dossier(
        db, {"character": "khaetar voss", "relations": [{"category": "amis", "name": "Zilvra"}]}
    )
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert character.profile.backstory == ""
    assert character.events == []
    assert [relation.name for relation in character.relations] == ["Zilvra"]
    assert character.items == []
    assert db.scalar(select(func.count()).select_from(CharacterRelation)) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"character": "Personne"},
        {"relations": [{"category": "amis"}]},
        {"relations": [{"category": "", "name": "Sans catégorie"}]},
        {"relations": [{"category": "amis", "name": "X", "article": "Fiche inconnue"}]},
        {"inventory": [{"name": "Dette", "quantity": -1}]},
        {"inventory": [{"name": "Épée", "equipped": "oui"}]},
        {"history": {"events": [{"session": "Séance 2"}]}},
    ],
)
def test_invalid_dossiers_are_rejected(db: Session, table: Table, change: dict[str, Any]) -> None:
    data = {**copy.deepcopy(DOSSIER), **change}
    with pytest.raises(DossierError):
        import_dossier(db, data)


def test_owner_sees_the_dossier_tabs(client: TestClient, db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(DOSSIER))
    login(client, "keator")
    page = client.get("/joueur").text
    for label in ("Relations", "Histoire", "Inventaire"):
        assert f">{label}<" in page
    assert "Né orphelin dans le Khaanat." in page
    assert "Le vieux prêtre" in page
    assert "<script>alert" not in page
    assert f'href="/joueur/articles/{table.krateros.wa_id}"' not in page
    own = client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    assert "Né orphelin dans le Khaanat." in own


def test_relation_links_only_to_accessible_fiches(
    client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(DOSSIER))
    db.add(CharacterArticle(character_id=table.keator.id, article_id=table.krateros.wa_id))
    db.commit()
    login(client, "keator")
    assert f'href="/joueur/articles/{table.krateros.wa_id}"' in client.get("/joueur").text


def test_other_players_never_see_the_dossier(client: TestClient, db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(DOSSIER))
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "zilvra")
    page = client.get(f"/joueur/articles/{table.khaetar.wa_id}")
    assert page.status_code == 200
    assert "Occultiste tieffelin." in page.text
    assert "Né orphelin dans le Khaanat." not in page.text
    assert "Le vieux prêtre" not in page.text


def test_dm_sees_the_dossier_with_links(client: TestClient, db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(DOSSIER))
    login(client, "maitre")
    page = client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert "Né orphelin dans le Khaanat." in page
    assert f'href="/mj/articles/{table.krateros.wa_id}"' in page
    assert "Amulette bénie" in page


def test_empty_dossier_shows_placeholders_for_characters(
    client: TestClient, db: Session, table: Table
) -> None:
    login(client, "keator")
    page = client.get("/joueur").text
    assert "Aucune relation consignée" in page
    assert "Histoire encore à écrire" in page
    assert "Besace vide" in page
    assert "import-dossiers" not in page


def test_cli_imports_a_folder(tmp_path: Path, db: Session, table: Table) -> None:
    (tmp_path / "khaetar.toml").write_text(
        'character = "Khaetar Voss"\n\n[history]\nbackstory = """\nNé orphelin.\n"""\n\n'
        '[[relations]]\ncategory = "famille"\nname = "Mère inconnue"\n\n'
        '[[inventory]]\nname = "Pot en fer"\nquantity = 2\n',
        encoding="utf-8",
    )
    (tmp_path / "inconnu.toml").write_text('character = "Personne"\n', encoding="utf-8")
    assert cli_main(["import-dossiers", str(tmp_path)]) == 1
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert character.profile.backstory.strip() == "Né orphelin."
    assert [item.quantity for item in character.items] == [2]
