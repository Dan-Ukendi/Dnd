from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chronology import ERAS_BY_SLUG
from app.models import (
    Artefact,
    Character,
    CharacterItem,
    LoreEntry,
    Role,
    TimelineEvent,
    User,
    WaArticle,
)
from app.services import lore as service
from app.services.artefact_split import (
    AUTOMATIC_NOTE,
    QUICK_SHEET_ENTRY,
    artefact_row,
    entry_rows,
    split_item_article,
)
from tests.factories import login, make_article, make_character, make_user, post_form

JOUG_ID = "dcdedb4d-686e-483d-a35d-137823b77eb1"
GAGE_ID = "57249abe-74e2-4590-b489-7e388923051b"

ITEM = """[p][/p]
[h|t1]L'Anneau d'essai[/h]
[p][i]L'anneau qui sert aux tests[/i][/p]
[h1|a1]Fiche rapide[/h1]
[ul]
- [b]Type :[/b] anneau, objet d'essai
- [b]Autres noms :[/b] l'Essai, la Bague
- [b]Origine :[/b] une forge oubliée
- [b]État :[/b] intact
- [b]Porteur actuel :[/b] Alice
[/ul]
[h1|a2]Description[/h1]
[p]Un anneau de cuivre terni.[/p]
[h1|a3]Histoire[/h1]
[h2|a4]La forge[/h2]
[p]Il fut forgé dans le secret.[/p]
[h2|a5]La perte[/h2]
[p]Il fut perdu au fond d'un lac.[/p]
[h1|a6]Secrets[/h1]
[ul]
- Le lac est hanté.
[/ul]
[h1|a12]Légendes[/h1]
[p]On dit qu'il chante la nuit.[/p]
[h|a7]Statistiques (D&D 5e)[/h]
[p]L'anneau existe sous deux états.[/p]
[h1|a8]État terni[/h1]
[p][i]Objet merveilleux, peu commun (nécessite un lien)[/i][/p]
[ul]
- [b]Lueur :[/b] une fois par aube, l'anneau éclaire.
[/ul]
[h1|a9]État poli[/h1]
[p][i]Objet merveilleux (anneau), rare (nécessite un lien par un mage)[/i][/p]
[h2|a10]Charges[/h2]
[p]L'anneau possède 5 charges. Il en récupère 1d4 à chaque aube.[/p]
[h2|a11]Prix du pouvoir : la fatigue[/h2]
[p]Chaque charge dépensée fatigue le porteur.[/p]
[p][/p]"""

KNOWN = """[h|t]Le Joug de Malvaar[/h]
[p][i]L'amulette qui plie les volontés[/i][/p]
[h1|h]Histoire[/h1]
[h2|f]La forge[/h2]
[p]Récit de la forge.[/p]
[h2|r]La réapparition[/h2]
[p]Récit du marché.[/p]
[h1|p]Pouvoirs[/h1]
[p]Récit et règles mêlés.[/p]
[h1|c]Ceux qui la convoitent[/h1]
[p]Le patron secret.[/p]
[h1|s]Événements de la session 1[/h1]
[p]La dispute au marché.[/p]
[h|st]Statistiques (D&D 5e)[/h]
[p][i]Objet merveilleux, rare (nécessite un lien)[/i][/p]"""


def test_split_keeps_characteristics_on_the_fiche_and_the_story_in_the_lore() -> None:
    draft = split_item_article("00000000-0000-0000-0000-000000000001", "L'Anneau d'essai", ITEM)
    assert draft.tagline == "L'anneau qui sert aux tests"
    assert draft.object_type == "anneau, objet d'essai"
    assert draft.other_names == "l'Essai, la Bague"
    assert draft.status == "intact"
    assert draft.rarity == "peu commun (État terni) ; rare (État poli)"
    assert draft.attunement == "nécessite un lien ; nécessite un lien par un mage"
    assert draft.charges == "5 (État poli)"
    assert draft.recharge == "1d4 à chaque aube (État poli)"
    assert draft.curse == "Prix du pouvoir : la fatigue (État poli)"
    assert draft.properties.startswith("[h|a7]Statistiques (D&D 5e)[/h]")
    assert draft.properties.endswith("[p]Chaque charge dépensée fatigue le porteur.[/p]")
    assert "Légendes" not in draft.properties
    titles = [entry.title for entry in draft.entries]
    assert titles == [
        QUICK_SHEET_ENTRY,
        "Description",
        "La forge",
        "La perte",
        "Secrets",
        "Légendes",
    ]
    by_title = {entry.title: entry for entry in draft.entries}
    assert by_title[QUICK_SHEET_ENTRY].text == "[ul]\n- [b]Origine :[/b] une forge oubliée\n[/ul]"
    assert by_title["La perte"].text == "[p]Il fut perdu au fond d'un lac.[/p]"
    assert by_title["Secrets"].gm_only
    assert not by_title["La forge"].gm_only
    assert all(entry.start_year is None for entry in draft.entries)
    assert all(AUTOMATIC_NOTE in entry.notes for entry in draft.entries)
    assert draft.notes[0] == AUTOMATIC_NOTE
    assert any("Alice" in note for note in draft.notes)
    story = " ".join(entry.text for entry in draft.entries)
    assert "forgé dans le secret" in story
    assert "forgé dans le secret" not in draft.properties


def test_split_is_pure_and_deterministic() -> None:
    first = split_item_article(JOUG_ID, "Le Joug de Malvaar", KNOWN)
    second = split_item_article(JOUG_ID, "Le Joug de Malvaar", KNOWN)
    assert artefact_row(JOUG_ID, first) == artefact_row(JOUG_ID, second)
    assert entry_rows(JOUG_ID, first, {}) == entry_rows(JOUG_ID, second, {})


def test_known_articles_follow_the_decided_dates_and_visibility() -> None:
    draft = split_item_article(JOUG_ID, "Le Joug de Malvaar", KNOWN)
    rows = {row["title"]: row for row in entry_rows(JOUG_ID, draft, {"A1": 41})}
    assert rows["La forge"]["timeline_event_id"] == 41
    assert rows["La forge"]["start_year"] is None
    assert "A1" in rows["La forge"]["review_note"]
    assert rows["Événements de la session 1"]["start_year"] == 987
    assert rows["Ceux qui la convoitent"]["gm_only"]
    assert rows["Pouvoirs"]["review_note"]
    assert rows["La réapparition"]["review_note"]
    assert rows["La réapparition"]["start_year"] is None
    missing = {row["title"]: row for row in entry_rows(JOUG_ID, draft, {})}
    assert missing["La forge"]["timeline_event_id"] is None
    assert "introuvable" in missing["La forge"]["review_note"]
    assert AUTOMATIC_NOTE not in rows["La forge"]["review_note"]


def _item(db: Session, wa_id: str, title: str, content: str) -> WaArticle:
    article = WaArticle(
        wa_id=wa_id,
        title=title,
        template_type="item",
        entity_class="Item",
        state="public",
        content=content,
        content_text="",
        raw={"title": title},
        player_visible=True,
    )
    db.add(article)
    db.commit()
    return article


def test_new_item_articles_get_a_fiche_once_and_are_never_overwritten(db: Session) -> None:
    article = make_article(db, "L'Anneau d'essai", content=ITEM, template_type="item")
    created = service.ensure_artefacts(db)
    db.commit()
    assert len(created) == 1
    artefact = service.get_artefact(db, article.wa_id)
    assert artefact.object_type == "anneau, objet d'essai"
    count = db.scalar(select(func.count()).select_from(LoreEntry))
    assert count == 6
    artefact.status = "perdu"
    db.commit()
    assert service.ensure_artefacts(db) == []
    db.commit()
    db.expire_all()
    assert service.get_artefact(db, article.wa_id).status == "perdu"
    assert db.scalar(select(func.count()).select_from(LoreEntry)) == count
    assert db.get(WaArticle, article.wa_id).content == ITEM


@pytest.fixture
def rerun_migration(db: Session) -> Iterator[object]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False

    def rerun() -> None:
        db.rollback()
        command.downgrade(config, "0009")
        command.upgrade(config, "head")

    yield rerun
    command.upgrade(config, "head")


def test_migration_splits_the_cached_item_articles_without_network(
    db: Session, rerun_migration: object
) -> None:
    joug = _item(db, JOUG_ID, "Le Joug de Malvaar", KNOWN)
    other = make_article(db, "Une personne", content="[p]Texte.[/p]")
    event = TimelineEvent(
        code="A1", title="Forge", start_year=-523, importance="majeur", origin="canon"
    )
    db.add(event)
    db.commit()
    event_id = event.id
    rerun_migration()
    fiches = db.scalars(select(Artefact)).all()
    assert [fiche.wa_article_id for fiche in fiches] == [joug.wa_id]
    assert fiches[0].rarity == "rare"
    assert fiches[0].tagline == "L'amulette qui plie les volontés"
    entries = {entry.title: entry for entry in db.scalars(select(LoreEntry)).all()}
    assert set(entries) == {
        "La forge",
        "La réapparition",
        "Pouvoirs",
        "Ceux qui la convoitent",
        "Événements de la session 1",
    }
    assert entries["La forge"].timeline_event_id == event_id
    assert entries["La forge"].text == "[p]Récit de la forge.[/p]"
    assert entries["Ceux qui la convoitent"].gm_only
    assert all(entry.wa_article_id == JOUG_ID for entry in entries.values())
    assert db.get(WaArticle, JOUG_ID).content == KNOWN
    assert db.get(WaArticle, other.wa_id) is not None


def test_sort_key_orders_eras_before_year_zero_and_keeps_undated_last() -> None:
    def entry(entry_id: int, **fields: object) -> LoreEntry:
        return LoreEntry(id=entry_id, sort_order=0, position=0, approximate=False, **fields)

    rows = [
        entry(1, start_year=987, end_year=None, era=None),
        entry(2, start_year=None, end_year=None, era=None),
        entry(3, start_year=-120, end_year=None, era=None),
        entry(4, start_year=None, end_year=None, era="aubes-anciennes"),
        entry(5, start_year=-600, end_year=-550, era=None),
        entry(6, start_year=None, end_year=None, era="siecles-oublies"),
        entry(7, start_year=0, end_year=None, era=None),
        entry(8, start_year=None, end_year=None, era="tumultes"),
    ]
    ordered = sorted(rows, key=lambda row: service.sort_key(service.entry_date(row, None), row))
    assert [row.id for row in ordered] == [4, 5, 6, 3, 7, 8, 1, 2]
    assert service.entry_date(rows[0], None).era_slug == "tumultes"
    assert service.entry_date(rows[3], None).era_slug == "aubes-anciennes"
    assert ERAS_BY_SLUG["siecles-oublies"].start == -500


@dataclass
class LoreWorld:
    dm: User
    alice: User
    bob: User
    joug: WaArticle
    hidden_item: WaArticle
    alice_character: Character
    public_entry: LoreEntry
    secret_entry: LoreEntry
    forge_entry: LoreEntry
    hidden_entry: LoreEntry
    unlinked_entry: LoreEntry
    revealed_event_entry: LoreEntry


@pytest.fixture
def world(db: Session) -> LoreWorld:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    bob = make_user(db, "bob")
    joug = _item(db, JOUG_ID, "Le Joug de Malvaar", KNOWN)
    hidden_item = make_article(db, "L'Épée cachée", content=ITEM, template_type="item")
    alice_main = make_article(db, "Alice la voleuse")
    alice_character = make_character(db, "Alice", alice, alice_main, [joug])
    make_character(db, "Bob", bob, make_article(db, "Bob le barde"))
    db.add(
        CharacterItem(
            character_id=alice_character.id, position=1, name="Joug", wa_article_id=joug.wa_id
        )
    )
    forge = TimelineEvent(
        code="A1", title="Forge du Joug", start_year=-523, importance="majeur", origin="canon"
    )
    revealed = TimelineEvent(
        code="B1",
        title="Fondation",
        start_year=0,
        importance="majeur",
        origin="canon",
        visibility="joueurs",
    )
    db.add_all([forge, revealed])
    db.commit()
    service.ensure_artefacts(db)
    db.commit()
    entries = {entry.title: entry for entry in service.entries_for(db, dm=True, article_id=JOUG_ID)}
    hidden_entry = next(iter(service.entries_for(db, dm=True, article_id=hidden_item.wa_id)))
    unlinked = LoreEntry(
        title="Le culte oublié", element_type="divinite", text="[p]Un dieu sans nom.[/p]"
    )
    revealed_entry = LoreEntry(
        title="La fondation racontée",
        element_type="evenement",
        text="[p]Le jour de la fondation.[/p]",
        timeline_event_id=revealed.id,
    )
    db.add_all([unlinked, revealed_entry])
    db.commit()
    return LoreWorld(
        dm,
        alice,
        bob,
        joug,
        hidden_item,
        alice_character,
        entries["La réapparition"],
        entries["Ceux qui la convoitent"],
        entries["La forge"],
        hidden_entry,
        unlinked,
        revealed_entry,
    )


def test_dm_lore_page_has_the_frise_and_artefact_tabs(client: TestClient, world: LoreWorld) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=lore")
    assert page.status_code == 200
    for text in (
        'data-tab="frise"',
        'data-tab="artefacts"',
        "Les Aubes Anciennes",
        "Les Tumultes",
        "Non daté",
        "Le Joug de Malvaar",
        "L&#39;Épée cachée",
        "Le culte oublié",
        "MJ seulement",
        "À vérifier",
    ):
        assert text in page.text, text
    filtered = client.get("/mj/articles?section=lore&element=divinite")
    assert "Le culte oublié" in filtered.text
    assert "La réapparition" not in filtered.text
    review = client.get("/mj/articles?section=lore&a_verifier=1")
    assert "La réapparition" in review.text
    assert "La fondation racontée" not in review.text


def test_artefact_fiche_shows_characteristics_and_linked_lore_only(
    client: TestClient, world: LoreWorld
) -> None:
    login(client, "maitre")
    page = client.get(f"/mj/articles/{JOUG_ID}")
    assert page.status_code == 200
    assert "Caractéristiques" in page.text
    assert "Lore associé" in page.text
    assert f'href="/mj/lore/{world.public_entry.id}"' in page.text
    assert "Récit du marché." not in page.text
    assert "Récit de la forge." not in page.text
    assert "Alice" in page.text
    assert "Modifier les caractéristiques" in page.text


def test_lore_entry_links_back_to_its_artefact(client: TestClient, world: LoreWorld) -> None:
    login(client, "maitre")
    page = client.get(f"/mj/lore/{world.forge_entry.id}")
    assert page.status_code == 200
    assert f'href="/mj/articles/{JOUG_ID}"' in page.text
    assert "Récit de la forge." in page.text
    assert "523 av. É." in page.text
    assert "Forge du Joug" in page.text


def test_player_only_sees_lore_linked_to_accessible_elements(
    client: TestClient, world: LoreWorld
) -> None:
    login(client, "alice")
    page = client.get("/joueur/articles?section=lore")
    assert page.status_code == 200
    assert "La réapparition" in page.text
    assert "La fondation racontée" in page.text
    for hidden in (
        "Ceux qui la convoitent",
        "Secrets",
        "Le culte oublié",
        "L&#39;Épée cachée",
        "Le patron secret",
    ):
        assert hidden not in page.text, hidden
    assert "À vérifier" not in page.text
    assert "MJ seulement" not in page.text
    assert client.get(f"/joueur/lore/{world.secret_entry.id}").status_code == 404
    assert client.get(f"/joueur/lore/{world.hidden_entry.id}").status_code == 404
    assert client.get(f"/joueur/lore/{world.unlinked_entry.id}").status_code == 404
    entry = client.get(f"/joueur/lore/{world.public_entry.id}")
    assert entry.status_code == 200
    assert "Récit du marché." in entry.text
    assert world.public_entry.review_note not in entry.text
    assert f'href="/joueur/articles/{JOUG_ID}"' in entry.text


def test_player_sees_entries_of_hidden_events_without_date_or_link(
    client: TestClient, world: LoreWorld
) -> None:
    login(client, "alice")
    entry = client.get(f"/joueur/lore/{world.forge_entry.id}")
    assert entry.status_code == 200
    assert "Non daté" in entry.text
    assert "523" not in entry.text
    assert "Forge du Joug" not in entry.text
    assert "/joueur/chronologie/evenements" not in entry.text


def test_player_artefact_fiche_hides_dm_lore_and_review_notes(
    client: TestClient, other_client: TestClient, world: LoreWorld
) -> None:
    login(client, "alice")
    page = client.get(f"/joueur/articles/{JOUG_ID}")
    assert page.status_code == 200
    assert "Lore associé" in page.text
    assert "La réapparition" in page.text
    assert "Ceux qui la convoitent" not in page.text
    assert "Le patron secret" not in page.text
    assert "Porteur actuel" not in page.text
    assert "Marquer vérifié" not in page.text
    assert "Modifier les caractéristiques" not in page.text
    login(other_client, "bob")
    assert other_client.get(f"/joueur/articles/{JOUG_ID}").status_code == 404
    assert other_client.get(f"/joueur/lore/{world.public_entry.id}").status_code == 404
    bob_page = other_client.get("/joueur/articles?section=lore")
    assert "La réapparition" not in bob_page.text
    assert "Le Joug de Malvaar" not in bob_page.text


def test_player_cannot_edit_lore_or_artefacts(
    client: TestClient, world: LoreWorld, db: Session
) -> None:
    login(client, "alice")
    actions = [
        ("/mj/lore", {"title": "Intrus", "element_type": "lieu"}),
        (f"/mj/lore/{world.public_entry.id}", {"title": "Changé", "element_type": "lieu"}),
        (f"/mj/lore/{world.public_entry.id}/verifie", {}),
        (f"/mj/lore/{world.public_entry.id}/supprimer", {}),
        (f"/mj/artefacts/{JOUG_ID}", {"status": "détruit"}),
        (f"/mj/artefacts/{JOUG_ID}/verifie", {}),
        (f"/mj/artefacts/{world.hidden_item.wa_id}/creer", {}),
    ]
    for path, data in actions:
        assert post_form(client, path, data, "/connexion/mj").status_code == 404, path
    assert client.get(f"/mj/lore/{world.public_entry.id}").status_code == 404
    db.expire_all()
    assert db.get(LoreEntry, world.public_entry.id).title == "La réapparition"
    assert db.get(LoreEntry, world.public_entry.id).review_note
    assert service.get_artefact(db, JOUG_ID).status == ""


def test_dm_creates_edits_verifies_and_deletes_entries(
    client: TestClient, world: LoreWorld, db: Session
) -> None:
    login(client, "maitre")
    page = "/mj/articles?section=lore"
    response = post_form(
        client,
        "/mj/lore",
        {
            "title": "La cité d'avant",
            "element_type": "lieu",
            "text": "[p]Une cité engloutie.[/p]",
            "era": "siecles-oublies",
            "approximate": "1",
        },
        page,
    )
    assert response.status_code == 303
    entry = db.scalar(select(LoreEntry).where(LoreEntry.title == "La cité d'avant"))
    assert (entry.era, entry.approximate, entry.start_year) == ("siecles-oublies", True, None)
    detail = client.get(f"/mj/lore/{entry.id}")
    assert "≈ Les Siècles Oubliés" in detail.text
    for data, field in (
        ({"start_year": "100", "era": "tumultes"}, "era_mismatch"),
        ({"start_year": "10", "end_year": "5"}, "end_year"),
        (
            {"start_year": "10", "event_id": str(world.forge_entry.timeline_event_id)},
            "date_conflict",
        ),
    ):
        post_form(
            client,
            f"/mj/lore/{entry.id}",
            {"title": "La cité d'avant", "element_type": "lieu", **data},
            page,
        )
        db.expire_all()
        assert db.get(LoreEntry, entry.id).era == "siecles-oublies", field
    post_form(
        client,
        f"/mj/lore/{entry.id}",
        {
            "title": "La cité d'avant",
            "element_type": "lieu",
            "start_year": "-320",
            "end_year": "-300",
            "review_note": "Vérifier la date.",
        },
        page,
    )
    db.expire_all()
    entry = db.get(LoreEntry, entry.id)
    assert (entry.start_year, entry.end_year, entry.era) == (-320, -300, None)
    post_form(client, f"/mj/lore/{entry.id}/verifie", {}, page)
    db.expire_all()
    assert db.get(LoreEntry, entry.id).review_note == ""
    entry_id = entry.id
    post_form(client, f"/mj/lore/{entry_id}/supprimer", {}, page)
    db.expire_all()
    assert db.get(LoreEntry, entry_id) is None


def test_dm_edits_artefact_characteristics(
    client: TestClient, world: LoreWorld, db: Session
) -> None:
    login(client, "maitre")
    response = post_form(
        client,
        f"/mj/artefacts/{JOUG_ID}",
        {"object_type": "amulette", "status": "en circulation", "location": "Khüchniin Khöndii"},
        f"/mj/articles/{JOUG_ID}",
    )
    assert response.status_code == 303
    db.expire_all()
    artefact = service.get_artefact(db, JOUG_ID)
    assert (artefact.object_type, artefact.status) == ("amulette", "en circulation")
    post_form(client, f"/mj/artefacts/{JOUG_ID}/verifie", {}, f"/mj/articles/{JOUG_ID}")
    db.expire_all()
    assert service.get_artefact(db, JOUG_ID).review_note == ""
