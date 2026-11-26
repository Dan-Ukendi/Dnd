import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.credits import credit_for
from app.models import (
    CharacterJournalEntry,
    CharacterRelation,
    LinkExclusion,
    Role,
    TimelineEvent,
)
from app.services.structure_import import import_structures
from app.worldanvil.linking import Matcher, Name, Target
from tests.factories import login, make_article, make_character, make_user

MIGRATION = Path("migrations/versions/0020_dm_corrections_renames.py")
_spec = importlib.util.spec_from_file_location("migration_0020", MIGRATION)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)
rename = migration.rename

RENAMED = [
    ("Le temple d'Arès (le Temple Blanc)", "Lux Victrix"),
    ("Le temple d'Arès le Temple Blanc", "Lux Victrix"),
    (
        "un éclat de la pierre blanche du Temple Blanc.",
        "un éclat de la pierre blanche de la Lux Victrix.",
    ),
    ("Khaetar au Temple Blanc : Kratéros le coupe", "Khaetar à la Lux Victrix : Kratos le coupe"),
    ("Au Temple Blanc, un prêtre d'Arès", "À la Lux Victrix, un prêtre d'Arès"),
    ("que les habitants appellent le Temple Blanc,", "que les habitants appellent la Lux Victrix,"),
    (
        "Le Temple Blanc d'Arès est bâti dans les galeries",
        "La Lux Victrix d'Arès est bâtie dans les galeries",
    ),
    ("Lieu : Temple Blanc d'Arès", "Lieu : Lux Victrix d'Arès"),
    ("Elle connaît le vrai rang de Kratéros.", "Elle connaît le vrai rang de Kratos."),
    ("Naissance de la Nuit et du marché noir", "Naissance de Shönö et du marché noir"),
    ("vendre les deux voleurs à la Nuit", "vendre les deux voleurs à Shönö"),
    ("Pour la Nuit, le vol de l'amulette", "Pour Shönö, le vol de l'amulette"),
    ("La Nuit lance la traque", "Shönö lance la traque"),
    ("Gérard, le majordome de Sophie, leur sert", "Gérard, le barman de Sophie, leur sert"),
    ("servie par son majordome Gérard.", "servie par son barman Gérard."),
    ("- [b]Gérard[/b] : majordome de Sophie.", "- [b]Gérard[/b] : barman de Sophie."),
    (
        "A pour les plus puissants, puis B, C et D.",
        "A pour les plus puissants, puis B, C, D, E et F.",
    ),
    (
        "- [b]La République de Fer[/b] (territoires",
        "- [b]Le Khaanat des Cinq Lances[/b] (territoires",
    ),
    ("- [b]La Concordance Logique[/b] : Un État", "- [b]L'Héliopolis des Harmoniens[/b] : Un État"),
    (
        "- [b]Le Dernier Bastion[/b] (ou Royaume de l'Aube) :",
        "- [b]Le Royaume de Sanguis Coronae[/b] (Secundum Imperium Sanguicor) :",
    ),
    ("Lié à : Long sommeil du Joug", "Lié à : Perte du Joug"),
    (
        "Jean-Charles ouvre la taverne Le Troll qui Rit",
        "Jean-Charles hérite de la taverne Le Troll qui Rit",
    ),
]
UNTOUCHED = [
    "Süüdriin shönö (La Nuit des Ombres) : une fois par mois, crimes nocturnes impunis.",
    "Nyx tōn Phōtōn (Nuit des Lumières) : chaque citoyen présente une découverte.",
    "La nuit du lynchage, il était à la tête de la foule.",
    "Ce qu'elle sait de cette nuit. Khaetar est parti une nuit, sans éclat.",
    "La prime de nuit : une récompense. Première nuit au Troll qui Rit.",
    "La Garde Blanche, la milice sacrée, vêtue de blanc et de bronze.",
    "Le temple d'Arès de Khüchniin Khöndii ; le prêtre d'Arès.",
    "Le long sommeil",
    "Gérard, barman personnel de Sophie.",
    "clerge-krateros krateros-person la-nuit-organization",
]


@pytest.mark.parametrize(("before", "after"), RENAMED)
def test_rename_rules(before: str, after: str) -> None:
    assert rename(before) == after
    assert rename(after) == after


@pytest.mark.parametrize("phrase", UNTOUCHED)
def test_rename_rules_leave_common_nouns_and_rituals(phrase: str) -> None:
    assert rename(phrase) == phrase


def test_ritual_name_is_not_linked_to_the_organisation() -> None:
    targets = [Target("shono", "Shönö", "organisation", "Organisation")]
    matcher = Matcher.build(targets, [Name("Shönö", "shono")], [migration.NEW_EXCLUSION])
    text_with_ritual = "Le Süüdriin shönö (La Nuit des Ombres) ; Shönö contrôle le marché noir."
    found = matcher.find(text_with_ritual)
    assert [(text_with_ritual[f.start : f.end], f.wa_id) for f in found] == [
        ("Süüdriin shönö", None),
        ("Shönö", "shono"),
    ]


def test_adapted_kratos_stat_block_is_credited() -> None:
    credit = credit_for("D'après War Priest (MotM p. 254), adapté au niveau 12")
    assert credit is not None and credit.key == "monsters-of-the-multiverse"


@pytest.fixture
def alembic_config(db: Session) -> Iterator[Config]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    yield config
    db.rollback()
    command.upgrade(config, "head")


def _insert(db: Session, table: str, **values: Any) -> None:
    columns = ", ".join(values)
    marks = ", ".join(
        f"CAST(:{key} AS JSONB)" if isinstance(value, list | dict) else f":{key}"
        for key, value in values.items()
    )
    params = {
        key: json.dumps(value) if isinstance(value, list | dict) else value
        for key, value in values.items()
    }
    db.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"), params)  # noqa: S608


@pytest.fixture
def legacy(db: Session, alembic_config: Config) -> dict[str, int]:
    db.rollback()
    command.downgrade(alembic_config, "0019")

    def event(code: str, title: str, start: int, end: int | None = None, summary: str = "") -> None:
        db.add(
            TimelineEvent(
                code=code,
                title=title,
                start_year=start,
                end_year=end,
                summary=summary,
                importance="notable",
                origin="canon",
            )
        )

    event("A4", "Long sommeil du Joug", -485, 829, "Le Joug passe de main en main.")
    event("B8", "Éclatement de l'ancien empire en trois nations", 757, 760)
    event("C8", "Le Khan Tömörzorig devient maître de la Voie de la Volonté", 800)
    event("C11", "Jean-Charles ouvre la taverne Le Troll qui Rit", 806, None, "Il ouvre.")
    event(
        "S5",
        "La Nuit lance la traque des voleurs de l'amulette",
        829,
        None,
        "Pour la Nuit, le vol est une insulte.\n\nÀ valider : le moment exact est déduit.",
    )
    event("S15", "Première nuit au Troll qui Rit", 829)
    player = make_user(db, "joueur", Role.PLAYER)
    khaetar = make_character(db, "Khaetar Voss", player)
    db.add(
        CharacterRelation(
            character_id=khaetar.id,
            position=0,
            category="allies",
            name="Kratéros",
            role="Vieux prêtre d'Arès du Temple Blanc",
            text="Il l'a coupé de la voix. Cette nuit-là, la Garde Blanche veillait.",
        )
    )
    db.add(
        CharacterJournalEntry(
            character_id=khaetar.id,
            episode=1,
            title="Séance 1",
            text="Je suis allé seul au Temple Blanc d'Arès.",
            lists={"ouverts": ["Les agents de la Nuit qui nous cherchent."]},
        )
    )
    db.flush()
    _insert(
        db,
        "places",
        key="temple-ares",
        name="Le temple d'Arès (le Temple Blanc)",
        kind="batiment",
        description="",
        gm_notes="",
        visibility="mj",
        position=0,
    )
    place_id = db.scalar(text("SELECT id FROM places WHERE key = 'temple-ares'"))
    _insert(
        db,
        "positions",
        key="khuchniin-khondii-tomorzorig",
        place_id=place_id,
        name="Tömörzorig",
        title="Khan, la Volonté de Fer",
        is_head=True,
        in_hierarchy=True,
        is_group=False,
        gm_notes="",
        visibility="mj",
        position=0,
    )
    _insert(
        db,
        "stat_blocks",
        key="lame",
        name="Lame",
        abilities=[10, 10, 10, 10, 10, 10],
        traits=[{"name": "Serment", "text": "Fidèle à la Nuit jusqu'à la mort."}],
        actions=[],
        bonus_actions=[],
        reactions=[],
        source="SRD 5.1",
    )
    db.commit()
    return {"character": khaetar.id}


def test_upgrade_renames_app_texts_and_corrects_the_chronology(
    db: Session, legacy: dict[str, int], alembic_config: Config
) -> None:
    db.rollback()
    command.upgrade(alembic_config, "0020")
    events = {
        row.code: (row.title, row.start_year, row.end_year, row.summary)
        for row in db.scalars(select(TimelineEvent))
    }
    assert events["A4"] == ("Perte du Joug", -485, None, migration.PERTE_SUMMARY)
    assert events["B8"][1:3] == (754, 760)
    assert events["C8"][0] == "Le Khan Tömörzorig devient maître de la Voie de la Volonté"
    assert events["C11"][0] == "Jean-Charles hérite de la taverne Le Troll qui Rit"
    assert events["C11"][3] == migration.HERITAGE_SUMMARY
    assert events["S5"][0] == "Shönö lance la traque des voleurs de l'amulette"
    assert events["S5"][3] == "Pour Shönö, le vol est une insulte."
    assert events["S15"][0] == "Première nuit au Troll qui Rit"
    relation = db.scalar(select(CharacterRelation))
    assert (relation.name, relation.role) == ("Kratos", "Vieux prêtre d'Arès de la Lux Victrix")
    assert relation.text == "Il l'a coupé de la voix. Cette nuit-là, la Garde Blanche veillait."
    journal = db.scalar(select(CharacterJournalEntry))
    assert journal.text == "Je suis allé seul à la Lux Victrix d'Arès."
    assert journal.lists == {"ouverts": ["Les agents de Shönö qui nous cherchent."]}
    assert db.scalar(text("SELECT name FROM places WHERE key = 'temple-ares'")) == "Lux Victrix"
    assert db.scalar(text("SELECT count(*) FROM positions")) == 0
    traits = db.scalar(text("SELECT traits FROM stat_blocks WHERE key = 'lame'"))
    assert traits == [{"name": "Serment", "text": "Fidèle à Shönö jusqu'à la mort."}]
    assert db.scalars(select(LinkExclusion.term)).all() == ["Süüdriin shönö"]


def test_upgrade_is_idempotent_and_downgrade_restores_the_exclusion(
    db: Session, legacy: dict[str, int], alembic_config: Config
) -> None:
    db.rollback()
    command.upgrade(alembic_config, "0020")
    first = db.execute(text("SELECT code, title, summary FROM timeline_events ORDER BY code")).all()
    db.rollback()
    command.downgrade(alembic_config, "0019")
    assert db.scalars(select(LinkExclusion.term)).all() == ["La Nuit"]
    db.rollback()
    command.upgrade(alembic_config, "0020")
    again = db.execute(text("SELECT code, title, summary FROM timeline_events ORDER BY code")).all()
    assert again == first


def test_secret_guild_rank_is_shown_to_the_dm_only(client: TestClient, db: Session) -> None:
    building = make_article(
        db, "La Guilde des Aventuriers de Khüchniin Khöndii", template_type="landmark"
    )
    player = make_user(db, "alice")
    make_character(db, "Zilvra", player, extras=[building])
    make_user(db, "maitre", Role.DM)
    place = {
        "key": "guilde-khuchniin-khondii",
        "name": building.title,
        "kind": "batiment",
        "article": building.title,
        "source": building.title,
        "visibility": "joueurs",
        "sections": [
            {"title": "La grande salle", "text": "Le tableau d'honneur.", "visibility": "joueurs"},
            {
                "title": "Secret (MJ)",
                "text": "En plus des six rangs de F à A, la Guilde a un rang S, tenu secret.",
                "visibility": "joueurs",
            },
        ],
    }
    lines = import_structures(db, [("guilde.toml", {"places": [place]})])
    assert not [line for line in lines if line.failed]
    login(client, "alice")
    place_page = client.get("/joueur/lieux/guilde-khuchniin-khondii")
    assert place_page.status_code == 200
    assert "La grande salle" in place_page.text
    fiche = client.get(f"/joueur/articles/{building.wa_id}")
    assert fiche.status_code == 200
    for page in (place_page.text, fiche.text):
        assert "rang S" not in page and "Secret (MJ)" not in page
    client.cookies.clear()
    login(client, "maitre")
    for path in (f"/mj/articles/{building.wa_id}", "/mj/lieux/guilde-khuchniin-khondii"):
        page = client.get(path).text
        assert "rang S" in page and "Secret (MJ)" in page, path
