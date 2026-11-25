from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chronology import ERAS_BY_SLUG
from app.models import (
    Artefact,
    LoreEntry,
    TimelineEvent,
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
from tests.factories import make_article

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
    assert rows["Événements de la session 1"]["start_year"] == 829
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
        entry(1, start_year=829, end_year=None, era=None),
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
