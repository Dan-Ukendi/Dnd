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
from tests.factories import (
    csrf_token,
    login,
    make_article,
    make_character,
    make_user,
    post_form,
)

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
        "Mentors",
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


COMBAT: dict[str, Any] = {
    "character": "Khaetar Voss",
    "combat": {
        "armor_class": 11,
        "hit_points": 24,
        "speed": "9 m",
        "spell_save_dc": 13,
        "resources": [
            {"name": "Emplacements de pacte (niv. 2)", "uses": 2, "recharge": "Repos court"}
        ],
    },
    "actions": [
        {
            "type": "reaction",
            "name": "Représailles infernales",
            "save": "DEX 13",
            "uses": "1 / repos long",
        },
        {
            "type": "action",
            "name": "Trait de feu",
            "hit": "+5",
            "damage": "1d10 feu",
            "range": "36 m",
        },
        {"type": "Action bonus", "name": "Arme de pacte"},
        {"type": "passif", "name": "Bénédiction du Ténébreux", "text": "6 PV temporaires."},
        {"type": "action", "name": "Suggestion", "save": "SAG 13", "concentration": True},
    ],
}


def test_combat_actions_are_grouped_by_action_type(db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(COMBAT))
    dossier = build_dossier(character, dm_render_context(db), lambda article: None)
    assert [group.label for group in dossier.action_groups] == [
        "Action",
        "Action bonus",
        "Réaction",
        "Sans action",
    ]
    assert [action.name for action in dossier.action_groups[0].actions] == [
        "Trait de feu",
        "Suggestion",
    ]
    assert dossier.action_groups[0].actions[1].concentration
    assert dossier.action_count == 5
    assert [(stat.label, stat.value) for stat in dossier.combat_stats] == [
        ("Classe d'armure", "11"),
        ("Points de vie", "24"),
        ("Vitesse", "9 m"),
        ("DD des sorts", "13"),
    ]
    assert dossier.resources[0].uses == "2"


@pytest.mark.parametrize(
    "actions",
    [
        [{"type": "minute", "name": "Rituel"}],
        [{"type": "action"}],
        [{"type": "action", "name": "Sort", "concentration": "oui"}],
        [{"type": "action", "name": "Sort", "hit": ["+5"]}],
    ],
)
def test_invalid_actions_are_rejected(
    db: Session, table: Table, actions: list[dict[str, Any]]
) -> None:
    with pytest.raises(DossierError):
        import_dossier(db, {**copy.deepcopy(COMBAT), "actions": actions})


def test_actions_tab_is_shown_to_owner_and_dm_only(
    client: TestClient, other_client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(COMBAT))
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "keator")
    page = client.get("/joueur").text
    assert ">Actions<" in page
    assert "Trait de feu" in page
    assert "Emplacements de pacte (niv. 2)" in page
    login(other_client, "zilvra")
    shared = other_client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    assert "Trait de feu" not in shared
    client.post("/deconnexion", data={"csrf_token": csrf_token(client, "/joueur")})
    login(client, "maitre")
    assert "Représailles infernales" in client.get(f"/mj/articles/{table.khaetar.wa_id}").text


SECRETS: dict[str, Any] = {
    "character": "Khaetar Voss",
    "aliases": [{"name": "Maelys", "note": "faux nom"}, "Le Cornu"],
    "relations": [
        {
            "category": "Fidèles",
            "name": "Jhorvyn",
            "role": "Intendant",
            "text": "Vieux et discret.",
            "gm_notes": "Vend ses secrets à Shaelith.",
        },
        {"category": "Couvent de Lolth", "name": "Mhyriss", "role": "Grande prêtresse"},
        {"category": "famille", "name": "Orzhan Veldrynn", "role": "Père"},
    ],
    "inventory": [{"name": "Amulette", "gm_notes": "Murmure la nuit."}],
}


def test_unknown_categories_keep_their_spelling(db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    assert [relation.category for relation in character.relations] == [
        "fideles",
        "Couvent de Lolth",
        "famille",
    ]
    dossier = build_dossier(character, dm_render_context(db), lambda article: None)
    assert [group.label for group in dossier.relation_groups] == [
        "Famille",
        "Fidèles",
        "Couvent de Lolth",
    ]


@pytest.mark.parametrize(
    "aliases",
    ["Maelys", [3], [{"note": "faux nom"}], [{"name": "Maelys", "note": ["faux"]}]],
)
def test_invalid_aliases_are_rejected(db: Session, table: Table, aliases: Any) -> None:
    with pytest.raises(DossierError):
        import_dossier(db, {**copy.deepcopy(SECRETS), "aliases": aliases})


def test_aliases_are_shown_to_the_dm_and_the_owner_only(
    client: TestClient, other_client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(SECRETS))
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "keator")
    assert "Maelys (faux nom), Le Cornu" in client.get("/joueur").text
    assert "Maelys (faux nom)" in client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    login(other_client, "zilvra")
    assert "Maelys" not in other_client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    client.post("/deconnexion", data={"csrf_token": csrf_token(client, "/joueur")})
    login(client, "maitre")
    assert "Maelys (faux nom)" in client.get(f"/mj/articles/{table.khaetar.wa_id}").text


def test_gm_notes_never_reach_players(
    client: TestClient, other_client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(SECRETS))
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "keator")
    for path in ("/joueur", f"/joueur/articles/{table.khaetar.wa_id}"):
        page = client.get(path).text
        assert "Vieux et discret." in page
        assert "Vend ses secrets" not in page
        assert "Murmure la nuit." not in page
        assert "Notes du MJ" not in page
    login(other_client, "zilvra")
    shared = other_client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    assert "Vend ses secrets" not in shared
    assert (
        build_dossier(
            db.get(Character, table.keator.id), dm_render_context(db), lambda article: None
        )
        .relation_groups[1]
        .people[0]
        .gm_html
        is None
    )


def test_dm_sees_gm_notes_and_edit_forms(client: TestClient, db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(SECRETS))
    login(client, "maitre")
    page = client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert "Notes du MJ" in page
    assert "Vend ses secrets à Shaelith." in page
    assert "Murmure la nuit." in page
    assert f'action="/mj/personnages/{table.keator.id}/relations"' in page
    assert ">Récit<" in page


def test_players_get_no_edit_forms(client: TestClient, db: Session, table: Table) -> None:
    import_dossier(db, copy.deepcopy(SECRETS))
    login(client, "keator")
    for path in ("/joueur", f"/joueur/articles/{table.khaetar.wa_id}"):
        page = client.get(path).text
        assert "/relations" not in page
        assert "gm_notes" not in page
        assert "Ajouter une relation" not in page


def _relation_form(**values: str) -> dict[str, str]:
    return {
        "name": "Jhorvyn",
        "category": "Fidèles",
        "role": "Intendant",
        "status": "",
        "article": "",
        "text": "",
        "gm_notes": "",
        **values,
    }


def test_dm_edits_a_relation(client: TestClient, db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    relation_id = character.relations[0].id
    login(client, "maitre")
    response = post_form(
        client,
        f"/mj/personnages/{table.keator.id}/relations/{relation_id}",
        _relation_form(
            name="Jhorvyn l'Intendant",
            category="Amis",
            status="En poste",
            article="Kratéros",
            text="Ligne un.\r\n\r\nLigne deux.",
            gm_notes="Doute de Zilvra.",
        ),
        f"/mj/articles/{table.khaetar.wa_id}",
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/mj/articles/{table.khaetar.wa_id}#relations"
    db.expire_all()
    relation = db.get(CharacterRelation, relation_id)
    assert relation.name == "Jhorvyn l'Intendant"
    assert relation.category == "amis"
    assert relation.status == "En poste"
    assert relation.wa_article_id == table.krateros.wa_id
    assert relation.text == "Ligne un.\n\nLigne deux."
    assert relation.gm_notes == "Doute de Zilvra."
    assert db.get(Character, table.keator.id).profile.relations_edited_at is not None
    page = client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert "Relations modifiées dans l'application" in page


def test_dm_adds_and_deletes_relations(client: TestClient, db: Session, table: Table) -> None:
    login(client, "maitre")
    page = f"/mj/articles/{table.khaetar.wa_id}"
    response = post_form(
        client,
        f"/mj/personnages/{table.keator.id}/relations",
        _relation_form(name="Tazhira", category="Rivaux", role="Rivale"),
        page,
    )
    assert response.status_code == 303
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert [(item.name, item.category, item.position) for item in character.relations] == [
        ("Tazhira", "rivaux", 0)
    ]
    assert character.profile.relations_edited_at is not None
    relation_id = character.relations[0].id
    response = post_form(
        client, f"/mj/personnages/{table.keator.id}/relations/{relation_id}/supprimer", {}, page
    )
    assert response.status_code == 303
    assert db.scalar(select(func.count()).select_from(CharacterRelation)) == 0


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"name": " "}, "le nom est obligatoire"),
        ({"category": ""}, "la catégorie est obligatoire"),
        ({"article": "Fiche inconnue"}, "aucune fiche World Anvil"),
        ({"category": "x" * 41}, "la catégorie est obligatoire"),
    ],
)
def test_invalid_relation_edits_are_refused(
    client: TestClient, db: Session, table: Table, values: dict[str, str], message: str
) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    relation_id = character.relations[0].id
    login(client, "maitre")
    page = f"/mj/articles/{table.khaetar.wa_id}"
    post_form(
        client,
        f"/mj/personnages/{table.keator.id}/relations/{relation_id}",
        _relation_form(**values),
        page,
    )
    assert f"Relation non enregistrée : {message}" in client.get(page).text
    db.expire_all()
    assert db.get(CharacterRelation, relation_id).name == "Jhorvyn"
    assert db.get(Character, table.keator.id).profile.relations_edited_at is None


def test_relation_routes_are_dm_only(client: TestClient, db: Session, table: Table) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    relation_id = character.relations[0].id
    base = f"/mj/personnages/{table.keator.id}/relations"
    login(client, "keator")
    for path in (base, f"{base}/{relation_id}", f"{base}/{relation_id}/supprimer"):
        response = post_form(client, path, _relation_form(name="Pirate"), "/joueur")
        assert response.status_code == 404
    db.expire_all()
    assert db.get(CharacterRelation, relation_id).name == "Jhorvyn"


def test_relation_edits_require_a_csrf_token(client: TestClient, db: Session, table: Table) -> None:
    login(client, "maitre")
    response = client.post(
        f"/mj/personnages/{table.keator.id}/relations",
        data=_relation_form(),
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert db.scalar(select(func.count()).select_from(CharacterRelation)) == 0


def test_relation_of_another_character_is_not_found(
    client: TestClient, db: Session, table: Table
) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    relation_id = character.relations[0].id
    login(client, "maitre")
    response = post_form(
        client,
        f"/mj/personnages/{table.zilvra.id}/relations/{relation_id}/supprimer",
        {},
        f"/mj/articles/{table.khaetar.wa_id}",
    )
    assert response.status_code == 404
    assert db.scalar(select(func.count()).select_from(CharacterRelation)) == 3


def test_reimport_keeps_relations_edited_in_the_app(
    client: TestClient, db: Session, table: Table
) -> None:
    import_dossier(db, copy.deepcopy(SECRETS))
    login(client, "maitre")
    post_form(
        client,
        f"/mj/personnages/{table.keator.id}/relations",
        _relation_form(name="Tazhira", category="Rivaux"),
        f"/mj/articles/{table.khaetar.wa_id}",
    )
    updated = {**copy.deepcopy(SECRETS), "history": {"backstory": "Nouvelle version."}}
    updated["relations"] = [{"category": "amis", "name": "Bob"}]
    db.expire_all()
    import_dossier(db, updated)
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert character.profile.backstory == "Nouvelle version."
    assert [relation.name for relation in character.relations] == [
        "Jhorvyn",
        "Mhyriss",
        "Orzhan Veldrynn",
        "Tazhira",
    ]
    import_dossier(db, updated, replace_relations=True)
    db.expire_all()
    character = db.get(Character, table.keator.id)
    assert [relation.name for relation in character.relations] == ["Bob"]
    assert character.profile.relations_edited_at is None


def test_cli_can_replace_relations_edited_in_the_app(
    tmp_path: Path, db: Session, table: Table
) -> None:
    character = import_dossier(db, copy.deepcopy(SECRETS))
    character.profile.relations_edited_at = character.profile.updated_at
    db.commit()
    (tmp_path / "khaetar.toml").write_text(
        'character = "Khaetar Voss"\n\n[[relations]]\ncategory = "amis"\nname = "Bob"\n',
        encoding="utf-8",
    )
    assert cli_main(["import-dossiers", str(tmp_path)]) == 0
    db.expire_all()
    assert len(db.get(Character, table.keator.id).relations) == 3
    assert cli_main(["import-dossiers", str(tmp_path), "--replace-relations"]) == 0
    db.expire_all()
    assert [item.name for item in db.get(Character, table.keator.id).relations] == ["Bob"]
