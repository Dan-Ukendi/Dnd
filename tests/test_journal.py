import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.models import Character, CharacterArticle, CharacterJournalEntry, Role, WaArticle
from app.services.access import dm_render_context
from app.services.dossiers import DossierError, build_dossier, import_dossier
from tests.factories import bare_numbers, login, make_article, make_character, make_user

KHAETAR: dict[str, Any] = {
    "character": "Khaetar Voss",
    "journal": [
        {
            "episode": 2,
            "title": "La route barrée",
            "text": "Le troll nous attendait.",
        },
        {
            "episode": 1,
            "title": "L'amulette du marché noir",
            "days": "Jours 1 et 2",
            "text": "La voix m'a envoyé au marché noir.\n\nUn [i]vieux prêtre[/i] m'a parlé."
            " <script>alert(1)</script>",
            "encounters": ["[b]Gronk, fils de Brok[/b] : orc de la Guilde."],
            "items": ["Le Gage d'Arès, amulette bénie."],
            "quests": ["Déloger le troll de Sophie."],
            "leads": ["Pourquoi la voix veut-elle l'amulette ?"],
        },
    ],
}
ZILVRA: dict[str, Any] = {
    "character": "Zilvra Vhaelor",
    "journal": [
        {
            "episode": 1,
            "title": "Maelys signe le contrat",
            "text": "Le coffre de la naine était plein.",
            "items": ["Cinquante pièces d'or prises dans un coffre."],
        }
    ],
}


@dataclass
class Party:
    keator: Character
    zilvra: Character
    khaetar_article: WaArticle
    zilvra_article: WaArticle


@pytest.fixture
def party(db: Session) -> Party:
    make_user(db, "maitre", Role.DM)
    keator = make_user(db, "keator")
    zilvra = make_user(db, "zilvra")
    khaetar_article = make_article(db, "Khaetar Voss", content="[p]Occultiste.[/p]")
    zilvra_article = make_article(db, "Zilvra", content="[p]Paladine.[/p]")
    return Party(
        make_character(db, "Khaetar Voss", keator, khaetar_article),
        make_character(db, "Zilvra Vhaelor", zilvra, zilvra_article),
        khaetar_article,
        zilvra_article,
    )


def _import_both(db: Session) -> None:
    import_dossier(db, copy.deepcopy(KHAETAR))
    import_dossier(db, copy.deepcopy(ZILVRA))


def test_import_stores_the_journal_in_episode_order(db: Session, party: Party) -> None:
    character = import_dossier(db, copy.deepcopy(KHAETAR))
    assert [entry.episode for entry in character.journal] == [1, 2]
    first = character.journal[0]
    assert first.days == "Jours 1 et 2"
    assert first.lists == {
        "encounters": ["[b]Gronk, fils de Brok[/b] : orc de la Guilde."],
        "items": ["Le Gage d'Arès, amulette bénie."],
        "quests": ["Déloger le troll de Sophie."],
        "leads": ["Pourquoi la voix veut-elle l'amulette ?"],
    }
    assert character.journal[1].lists == {}
    assert character.journal[1].days is None


def test_reimport_replaces_the_journal(db: Session, party: Party) -> None:
    import_dossier(db, copy.deepcopy(KHAETAR))
    import_dossier(
        db,
        {"character": "Khaetar Voss", "journal": [{"episode": 1, "title": "Réécrit"}]},
    )
    db.expire_all()
    character = db.get(Character, party.keator.id)
    assert [(entry.episode, entry.title) for entry in character.journal] == [(1, "Réécrit")]
    assert db.scalar(select(func.count()).select_from(CharacterJournalEntry)) == 1
    import_dossier(db, {"character": "Khaetar Voss"})
    assert db.scalar(select(func.count()).select_from(CharacterJournalEntry)) == 0


@pytest.mark.parametrize(
    "journal",
    [
        {"title": "Sans numéro"},
        [{"episode": 0, "title": "Zéro"}],
        [{"episode": "1", "title": "Texte"}],
        [{"episode": True, "title": "Booléen"}],
        [{"episode": 1}],
        [{"episode": 1, "title": "A"}, {"episode": 1, "title": "B"}],
        [{"episode": 1, "title": "A", "leads": "pas une liste"}],
        [{"episode": 1, "title": "A", "items": ["", "vide"]}],
        [{"episode": 1, "title": "A", "quests": [3]}],
        [{"episode": 1, "title": "A", "encounters": ["x" * 2001]}],
        [{"episode": 1, "title": "A", "days": "x" * 101}],
    ],
)
def test_invalid_journals_are_rejected(db: Session, party: Party, journal: Any) -> None:
    with pytest.raises(DossierError):
        import_dossier(db, {"character": "Khaetar Voss", "journal": journal})


def test_journal_views_render_bbcode_safely(db: Session, party: Party) -> None:
    character = import_dossier(db, copy.deepcopy(KHAETAR))
    dossier = build_dossier(character, dm_render_context(db), lambda article: None)
    first = dossier.journal[0]
    assert "<em>vieux prêtre</em>" in first.html
    assert "<script" not in first.html
    assert [group.label for group in first.lists] == [
        "Rencontres",
        "Objets et argent",
        "Quêtes et engagements",
        "Pistes et questions",
    ]
    assert (
        str(first.lists[0].lines[0]) == "<strong>Gronk, fils de Brok</strong> : orc de la Guilde."
    )
    assert first.excerpt == "La voix m'a envoyé au marché noir."


def test_player_adventure_page_lists_only_their_journal(
    client: TestClient, db: Session, party: Party
) -> None:
    _import_both(db)
    login(client, "keator")
    page = client.get("/joueur/articles?section=aventure").text
    assert "Mon journal" in page
    assert f'href="/joueur/journal/{party.keator.id}/1"' in page
    assert f'href="/joueur/journal/{party.keator.id}/2"' in page
    assert page.index("La route barrée") < page.index("L&#39;amulette du marché noir")
    assert "Aucun compte rendu de session n'a encore été partagé avec vous." in page
    assert "Maelys signe le contrat" not in page
    assert f"/joueur/journal/{party.zilvra.id}/" not in page
    assert "Mon journal" not in client.get("/joueur/articles").text


def test_player_without_journal_keeps_the_empty_adventure_page(
    client: TestClient, db: Session, party: Party
) -> None:
    import_dossier(db, copy.deepcopy(KHAETAR))
    login(client, "zilvra")
    page = client.get("/joueur/articles?section=aventure").text
    assert "Mon journal" not in page
    assert "Le journal est encore vierge" in page


def test_owner_reads_a_journal_entry(client: TestClient, db: Session, party: Party) -> None:
    _import_both(db)
    login(client, "keator")
    response = client.get(f"/joueur/journal/{party.keator.id}/1")
    assert response.status_code == 200
    page = response.text
    assert "Journal de Khaetar Voss" in page
    assert "Jours 1 et 2" in page
    assert "<strong>Gronk, fils de Brok</strong>" in page
    assert "Pistes et questions</h2>" in page
    assert bare_numbers(page) == []
    assert "<script>alert" not in page
    assert f'href="/joueur/journal/{party.keator.id}/2"' in page
    assert f'href="/joueur/articles/{party.khaetar_article.wa_id}"' in page
    assert "Visible par" not in page


def test_other_player_never_reads_a_journal(client: TestClient, db: Session, party: Party) -> None:
    _import_both(db)
    db.add(CharacterArticle(character_id=party.zilvra.id, article_id=party.khaetar_article.wa_id))
    db.commit()
    login(client, "zilvra")
    assert client.get(f"/joueur/journal/{party.keator.id}/1").status_code == 404
    assert client.get(f"/joueur/journal/{party.zilvra.id}/9").status_code == 404
    fiche = client.get(f"/joueur/articles/{party.khaetar_article.wa_id}")
    assert fiche.status_code == 200
    assert "Occultiste." in fiche.text
    assert "L&#39;amulette du marché noir" not in fiche.text
    assert 'data-tab="journal"' not in fiche.text
    adventure = client.get("/joueur/articles?section=aventure").text
    assert "Maelys signe le contrat" in adventure
    assert "La route barrée" not in adventure


def test_owner_sees_the_journal_in_the_dossier(
    client: TestClient, db: Session, party: Party
) -> None:
    _import_both(db)
    login(client, "keator")
    for path in ("/joueur", f"/joueur/articles/{party.khaetar_article.wa_id}"):
        page = client.get(path).text
        assert 'data-tab="journal"' in page
        assert f'href="/joueur/journal/{party.keator.id}/1"' in page
        assert "Maelys signe le contrat" not in page


def test_dm_reads_every_journal(client: TestClient, db: Session, party: Party) -> None:
    _import_both(db)
    login(client, "maitre")
    adventure = client.get("/mj/articles?section=aventure").text
    assert "Journaux des personnages" in adventure
    assert f'href="/mj/journal/{party.keator.id}/1"' in adventure
    assert f'href="/mj/journal/{party.zilvra.id}/1"' in adventure
    entry = client.get(f"/mj/journal/{party.zilvra.id}/1")
    assert entry.status_code == 200
    assert "Visible par Zilvra et le MJ" in entry.text
    assert f'href="/mj/articles/{party.zilvra_article.wa_id}"' in entry.text
    fiche = client.get(f"/mj/articles/{party.khaetar_article.wa_id}").text
    assert 'data-tab="journal"' in fiche
    assert f'href="/mj/journal/{party.keator.id}/2"' in fiche
    assert client.get(f"/mj/journal/{party.keator.id}/7").status_code == 404
    assert client.get("/mj/journal/999/1").status_code == 404
    assert "Journaux des personnages" not in client.get("/mj/articles?section=lore").text


def test_journal_routes_need_the_right_role(client: TestClient, db: Session, party: Party) -> None:
    _import_both(db)
    for path in (f"/joueur/journal/{party.keator.id}/1", f"/mj/journal/{party.keator.id}/1"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion", path
    login(client, "keator")
    assert client.get(f"/mj/journal/{party.keator.id}/1").status_code == 404
    login(client, "maitre")
    assert client.get(f"/joueur/journal/{party.keator.id}/1").status_code == 404


def test_cli_imports_journal_entries(tmp_path: Path, db: Session, party: Party) -> None:
    (tmp_path / "khaetar.toml").write_text(
        """character = "Khaetar Voss"

[[journal]]
episode = 1
title = "L'amulette du marché noir"
days = "Jours 1 et 2"
text = \"\"\"
Premier paragraphe.

Second paragraphe.
\"\"\"
encounters = [
  "[b]Jean-Charles[/b] : patron du Troll qui Rit.",
]
leads = ["Où l'amulette a-t-elle été forgée ?"]
""",
        encoding="utf-8",
    )
    assert cli_main(["import-dossiers", str(tmp_path)]) == 0
    db.expire_all()
    character = db.get(Character, party.keator.id)
    assert [entry.title for entry in character.journal] == ["L'amulette du marché noir"]
    assert character.journal[0].lists["leads"] == ["Où l'amulette a-t-elle été forgée ?"]
