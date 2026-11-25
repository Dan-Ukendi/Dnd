import re
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Artefact,
    LoreEntry,
    LoreGroup,
    LoreStory,
    LoreType,
    Role,
    TimelineEvent,
    User,
    WaArticle,
)
from app.services import lore as service
from app.services.lore_sections import narrative_sections
from app.services.structure_excerpts import AnchorStatus, find_excerpt
from tests.factories import login, make_article, make_character, make_user, post_form
from tests.test_sync import FakeSource, run

JOUG_ID = "dcdedb4d-686e-483d-a35d-137823b77eb1"
GAGE_ID = "57249abe-74e2-4590-b489-7e388923051b"
SPECULUM_ID = "9a0ff719-7202-4be9-a52c-997e5af92751"
HELIOPOLIS_ID = "ea2b7304-a3e1-41ac-aa67-0ab4462a18fa"
FEUILLETS_ID = "b347541d-92e6-40ed-92e6-c6fdc691389b"

JOUG = """[h|t]Le Joug de Malvaar[/h]
[p][i]L'amulette qui plie les volontés[/i][/p]
[h1|q]Fiche rapide[/h1]
[ul]
- [b]Type :[/b] amulette (bijou)
- [b]Origine :[/b] un temps antérieur à l'Empire
[/ul]
[h1|d]Description[/h1]
[p]Une amulette de fer noir.[/p]
[h1|h]Histoire[/h1]
[h2|h1]Le sorcier[/h2]
[p]Récit du sorcier.[/p]
[h2|h2]La forge[/h2]
[p]Récit de la forge.[/p]
[h2|h3]Le règne du Dompteur[/h2]
[p]Récit du règne.[/p]
[h2|h4]La chute[/h2]
[p]Récit de la chute.[/p]
[h2|h5]Le long sommeil[/h2]
[p]Récit du sommeil.[/p]
[h2|h6]La réapparition[/h2]
[p]Récit du marché.[/p]
[h1|p]Pouvoirs[/h1]
[p]Elle plie les volontés.[/p]
[h1|c]Ceux qui la convoitent[/h1]
[p]Le patron secret.[/p]
[h1|l]Légendes et rumeurs[/h1]
[p]On murmure qu'elle chante.[/p]
[h1|s]Secrets[/h1]
[p]Le secret du Joug.[/p]
[h1|e]Événements de la session 1[/h1]
[p]La dispute au marché.[/p]
[h1|n]Pistes pour la suite[/h1]
[p]Une piste cachée.[/p]
[h|st]Statistiques (D&D 5e)[/h]
[p][i]Objet merveilleux, rare (nécessite un lien)[/i][/p]"""

GAGE = """[h|t]Le Gage d'Arès[/h]
[p][i]L'amulette de victoire[/i][/p]
[h1|d]Description[/h1]
[p]Un disque de bronze.[/p]
[h1|o]Origine[/h1]
[p]Bénie par Arès.[/p]
[h1|f]Fonctionnement[/h1]
[p]Elle se charge de gloire.[/p]
[h1|k]L'amulette de Khaetar[/h1]
[p]Khaetar la porte.[/p]
[h1|pd]Le patron démoniaque[/h1]
[p]Le démon guette.[/p]
[h1|s]Secrets[/h1]
[p]Le secret du Gage.[/p]
[h1|e]Événements de la session 1[/h1]
[p]Le combat du Gage.[/p]
[h|st]Statistiques (D&D 5e)[/h]
[p][i]Objet merveilleux, de peu commun à légendaire (nécessite un lien)[/i][/p]"""

SPECULUM = """[p][/p]
[p][b]L'Empire d'Ébène et les Doublés[/b][/p]
[p]Le village guerrier devint empire.[/p]
[p][b]Le Cataclysme : La Rébellion des Échos[/b][/p]
[p]Les Échos se révoltèrent ; naquit la République de Fer.[/p]"""

HELIOPOLIS = """[h1|f][b]Fondation et Philosophie[/b][/h1]
[p][b]Nom officiel[/b] : L'Héliopolis[/p]
[p][b]Histoire Fondatrice[/b] : Aetios et Eidōlos débattirent quarante jours.[/p]
[h1|g][b]Gouvernement[/b][/h1]
[p]Une république.[/p]"""

FEUILLETS = """[h]Les Feuillets du Dompteur[/h]
[h1]Fiche rapide[/h1]
[ul]
- [b]Type :[/b] recueil manuscrit
[/ul]
[h1]Description[/h1]
[p]Un petit volume de cuir sombre.[/p]
[h1]Avertissement du copiste[/h1]
[p]Je ne garantis rien.[/p]
[h1]Fragment I : le Dompteur[/h1]
[p]Fragment un.[/p]
[h1]Fragment II : la forge[/h1]
[p]Fragment deux.[/p]
[h1]Fragment III : les mots en cercle[/h1]
[p]Fragment trois.[/p]
[h1]Fragment IV : la chute[/h1]
[p]Fragment quatre.[/p]
[h1]Fragment V : le long silence[/h1]
[p]Fragment cinq.[/p]
[h1]On dit aussi[/h1]
[p]On dit aussi bien des choses.[/p]
[h1]Questions notées en marge[/h1]
[p]Qui l'a forgé ?[/p]"""

JOUG_PARTS = [
    "Le sorcier",
    "La forge",
    "Le règne du Dompteur",
    "La chute",
    "Le long sommeil",
    "La réapparition",
    "Légendes et rumeurs",
]
ARTICLES = {
    JOUG_ID: ("Le Joug de Malvaar", "item", JOUG),
    GAGE_ID: ("Le Gage d'Arès", "item", GAGE),
    SPECULUM_ID: ("Speculum Imperium", "organization", SPECULUM),
    HELIOPOLIS_ID: ("L'Héliopolis des Harmoniens", "organization", HELIOPOLIS),
    FEUILLETS_ID: ("Les Feuillets du Dompteur", "document", FEUILLETS),
}


def _article(db: Session, wa_id: str, title: str, template: str, content: str) -> WaArticle:
    article = WaArticle(
        wa_id=wa_id,
        title=title,
        template_type=template,
        entity_class=template.capitalize(),
        state="public",
        content=content,
        content_text="",
        raw={"title": title},
        player_visible=True,
    )
    db.add(article)
    return article


def _migrate(db: Session) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0009")
    command.upgrade(config, "head")


@pytest.fixture
def restore_head() -> Iterator[None]:
    yield
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


@dataclass
class World:
    dm: User
    alice: User
    bob: User
    joug: LoreStory
    empire: LoreStory
    heliopolis: LoreStory
    feuillets: LoreStory
    joug_artefact: Artefact
    gage_artefact: Artefact
    entry_count: int


def _story(db: Session, title: str) -> LoreStory:
    return db.scalar(select(LoreStory).where(LoreStory.title == title))


@pytest.fixture
def world(db: Session, restore_head: None) -> World:
    for wa_id, (title, template, content) in ARTICLES.items():
        _article(db, wa_id, title, template, content)
    db.add_all(
        [
            TimelineEvent(
                code="A1", title="Forge", start_year=-523, importance="majeur", origin="canon"
            ),
            TimelineEvent(
                code="A2",
                title="Règne",
                start_year=-522,
                end_year=-487,
                importance="majeur",
                origin="canon",
            ),
            TimelineEvent(
                code="A3", title="Chute", start_year=-486, importance="majeur", origin="canon"
            ),
            TimelineEvent(
                code="A4",
                title="Sommeil",
                start_year=-485,
                end_year=987,
                importance="majeur",
                origin="canon",
            ),
            TimelineEvent(
                code="B1", title="Création", start_year=0, importance="majeur", origin="canon"
            ),
            TimelineEvent(
                code="B7", title="Révolte", start_year=749, importance="majeur", origin="canon"
            ),
        ]
    )
    db.commit()
    _migrate(db)
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    bob = make_user(db, "bob")
    shared = [db.get(WaArticle, wa_id) for wa_id in ARTICLES]
    make_character(db, "Alice", alice, make_article(db, "Alice la voleuse"), shared)
    make_character(db, "Bob", bob, make_article(db, "Bob le barde"))
    return World(
        dm,
        alice,
        bob,
        _story(db, "Le Joug de Malvaar"),
        _story(db, "L'Empire d'Ébène et les Échos"),
        _story(db, "La fondation de l'Héliopolis"),
        _story(db, "Les Feuillets du Dompteur"),
        service.get_artefact(db, JOUG_ID),
        service.get_artefact(db, GAGE_ID),
        db.scalar(select(func.count()).select_from(LoreEntry)),
    )


def _parts(db: Session, story: LoreStory) -> list[LoreEntry]:
    return list(
        db.scalars(
            select(LoreEntry)
            .where(LoreEntry.story_id == story.id)
            .order_by(LoreEntry.position, LoreEntry.id)
        ).all()
    )


def _entry(db: Session, wa_id: str, title: str) -> LoreEntry:
    return db.scalar(
        select(LoreEntry).where(LoreEntry.wa_article_id == wa_id, LoreEntry.title == title)
    )


def _share(db: Session, *stories: LoreStory) -> None:
    for story in stories:
        db.get(LoreStory, story.id).gm_only = False
    db.commit()


def test_migration_builds_the_validated_lore_without_losing_entries(
    db: Session, world: World
) -> None:
    assert [item.label for item in service.lore_types(db)] == [
        "Mythe",
        "Conte",
        "Légende",
        "Histoire",
    ]
    joug = world.joug
    assert (joug.kind, joug.type.label, joug.group.title) == ("legende", "Légende", "Malvaar")
    assert joug.artefact_id == world.joug_artefact.id
    assert joug.gm_only
    parts = _parts(db, joug)
    assert [part.title for part in parts] == JOUG_PARTS
    assert all(part.text for part in parts)
    assert not any(part.gm_only for part in parts)
    assert [part.event.code if part.event else None for part in parts] == [
        None,
        "A1",
        "A2",
        "A3",
        "A4",
        None,
        None,
    ]
    placements = {
        (entry.wa_article_id, entry.title): entry.placement
        for entry in db.scalars(select(LoreEntry).where(LoreEntry.story_id.is_(None)))
    }
    assert placements == {
        (JOUG_ID, "Fiche rapide (origine)"): "dm_note",
        (JOUG_ID, "Description"): "physical",
        (JOUG_ID, "Pouvoirs"): "characteristic",
        (JOUG_ID, "Ceux qui la convoitent"): "dm_note",
        (JOUG_ID, "Secrets"): "dm_note",
        (JOUG_ID, "Événements de la session 1"): "dm_note",
        (JOUG_ID, "Pistes pour la suite"): "dm_note",
        (GAGE_ID, "Description"): "physical",
        (GAGE_ID, "Origine"): "characteristic",
        (GAGE_ID, "Fonctionnement"): "characteristic",
        (GAGE_ID, "L'amulette de Khaetar"): "dm_note",
        (GAGE_ID, "Le patron démoniaque"): "dm_note",
        (GAGE_ID, "Secrets"): "dm_note",
        (GAGE_ID, "Événements de la session 1"): "dm_note",
    }
    assert _entry(db, GAGE_ID, "Événements de la session 1").start_year == 987
    assert not _entry(db, GAGE_ID, "Le patron démoniaque").gm_only
    assert db.scalar(select(LoreStory).where(LoreStory.wa_article_id == GAGE_ID)) is None
    empire = world.empire
    assert (empire.type.label, empire.group_id, empire.gm_only) == ("Histoire", None, True)
    assert "Khaanat des Cinq Lances" in empire.review_note
    assert [(part.title, part.event.code, part.text) for part in _parts(db, empire)] == [
        ("L'Empire d'Ébène et les Doublés", "B1", ""),
        ("Le Cataclysme : La Rébellion des Échos", "B7", ""),
    ]
    heliopolis = world.heliopolis
    assert (heliopolis.era, heliopolis.approximate, heliopolis.start_year) == (
        "tumultes",
        True,
        None,
    )
    assert [part.source_section for part in _parts(db, heliopolis)] == ["Histoire Fondatrice"]
    book = world.feuillets
    assert (book.kind, book.legend_id, book.artefact_id) == (
        "ouvrage",
        joug.id,
        world.joug_artefact.id,
    )
    assert book.intro_sections == "Fiche rapide\nDescription"
    assert len(_parts(db, book)) == 8
    assert all(part.gm_only for part in _parts(db, book))
    assert not world.joug_artefact.gm_only
    assert db.get(WaArticle, JOUG_ID).content == JOUG


def test_downgrade_keeps_every_split_entry(db: Session, world: World) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0014")
    count = db.scalar(select(func.count()).select_from(LoreEntry))
    db.rollback()
    assert count == world.entry_count - 2 - 1 - 8
    command.upgrade(config, "head")
    assert db.scalar(select(func.count()).select_from(LoreEntry)) == world.entry_count


def test_lore_home_has_two_halves_and_books_for_dm_and_player(
    client: TestClient, other_client: TestClient, world: World, db: Session
) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=lore")
    assert page.status_code == 200
    for text in (
        "lore-half--legendes",
        "lore-half--artefacts",
        "lore-books",
        "Frise chronologique",
        "Autres fiches",
        "3 récits",
        "2 artefacts",
        "1 ouvrage",
    ):
        assert text in page.text, text
    login(other_client, "alice")
    player = other_client.get("/joueur/articles?section=lore")
    assert player.status_code == 200
    for text in ("lore-half--legendes", "lore-half--artefacts", "Aucun récit pour l'instant"):
        assert text in player.text, text
    assert "2 artefacts" in player.text
    assert "Le Joug de Malvaar" in player.text or "lore-gem" in player.text
    _share(db, world.joug)
    assert "1 récit" in other_client.get("/joueur/articles?section=lore").text


def test_malvaar_group_holds_the_joug_legend_with_seven_parts_and_navigation(
    client: TestClient, world: World, db: Session
) -> None:
    login(client, "maitre")
    legends = client.get("/mj/articles?section=lore&vue=legendes")
    assert legends.status_code == 200
    malvaar = legends.text.index("Malvaar</h2>")
    assert malvaar < legends.text.index('lore-story__title">Le Joug de Malvaar')
    base = f"/mj/lore/recits/{world.joug.id}"
    first = client.get(base)
    assert first.status_code == 200
    titles = re.findall(r'class="lore-parts__title">([^<]+)<', first.text)
    assert [title.replace("&#39;", "'") for title in titles] == JOUG_PARTS
    assert "Récit du sorcier." in first.text
    assert f'href="{base}/parties/2" rel="next"' in first.text
    assert 'rel="prev"' not in first.text
    assert "523 av. É. – An 987" in first.text
    middle = client.get(f"{base}/parties/4")
    assert "Récit de la chute." in middle.text
    assert f'href="{base}/parties/3" rel="prev"' in middle.text
    assert f'href="{base}/parties/5" rel="next"' in middle.text
    last = client.get(f"{base}/parties/7")
    assert "elle chante." in last.text
    assert 'rel="next"' not in last.text
    assert client.get(f"{base}/parties/8").status_code == 404
    assert client.get(f"/mj/lore/ouvrages/{world.joug.id}").status_code == 404


def test_parts_render_world_anvil_live_and_sync_never_touches_curation(
    client: TestClient, world: World, db: Session
) -> None:
    source = FakeSource()
    for wa_id, (title, template, content) in ARTICLES.items():
        updated = content.replace("Récit du sorcier.", "Le sorcier réécrit.")
        source.put(
            wa_id,
            title,
            updated,
            "2026-11-26 10:00:00",
            templateType=template,
            entityClass=template.capitalize(),
        )
    before = {
        story.id: (story.type_id, story.group_id, story.position, story.artefact_id)
        for story in db.scalars(select(LoreStory))
    }
    orders = [(part.id, part.position, part.story_id) for part in _parts(db, world.joug)]
    stories = db.scalar(select(func.count()).select_from(LoreStory))
    artefacts = db.scalar(select(func.count()).select_from(Artefact))
    entries = db.scalar(select(func.count()).select_from(LoreEntry))
    assert run(db, source).status == "success"
    db.expire_all()
    after = {
        story.id: (story.type_id, story.group_id, story.position, story.artefact_id)
        for story in db.scalars(select(LoreStory))
    }
    assert after == before
    assert [(part.id, part.position, part.story_id) for part in _parts(db, world.joug)] == orders
    assert db.scalar(select(func.count()).select_from(LoreStory)) == stories
    assert db.scalar(select(func.count()).select_from(Artefact)) == artefacts
    assert db.scalar(select(func.count()).select_from(LoreEntry)) == entries
    login(client, "maitre")
    page = client.get(f"/mj/lore/recits/{world.joug.id}")
    assert "Le sorcier réécrit." in page.text
    assert "Récit du sorcier." not in page.text


def test_sync_creates_no_lore_entry_or_artefact_for_new_articles(db: Session) -> None:
    source = FakeSource()
    source.put(
        "aaaaaaaa-0000-0000-0000-0000000000a1",
        "L'Épée neuve",
        "[h1]Description[/h1][p]Une lame.[/p][h1]Histoire[/h1][p]Forgée hier.[/p]",
        "2026-11-26 10:00:00",
        templateType="item",
        entityClass="Item",
    )
    source.put(
        "aaaaaaaa-0000-0000-0000-0000000000a2",
        "Session 2 : la route",
        "[h2]Résumé[/h2][p]La route.[/p][h2]Butin et objets[/h2][p]Une épée.[/p]",
        "2026-11-26 10:00:00",
        templateType="report",
        entityClass="Report",
    )
    assert run(db, source).status == "success"
    for model in (Artefact, LoreEntry, LoreStory):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model


def test_legend_and_artefact_show_each_other_as_cards(
    client: TestClient, world: World, db: Session
) -> None:
    login(client, "maitre")
    legend = client.get(f"/mj/lore/recits/{world.joug.id}")
    assert "Artefact associé" in legend.text
    assert f'href="/mj/articles/{JOUG_ID}"' in legend.text
    assert "Ouvrage associé" in legend.text
    assert f'href="/mj/lore/ouvrages/{world.feuillets.id}"' in legend.text
    fiche = client.get(f"/mj/articles/{JOUG_ID}")
    assert "Légende associée" in fiche.text
    assert f'href="/mj/lore/recits/{world.joug.id}"' in fiche.text
    assert "Ouvrage associé" in fiche.text
    assert "Récit du sorcier." not in fiche.text
    assert "Une amulette de fer noir." in fiche.text
    assert "Elle plie les volontés." in fiche.text
    book = client.get(f"/mj/lore/ouvrages/{world.feuillets.id}")
    assert "Légende associée" in book.text
    assert "Artefact associé" in book.text
    assert "Un petit volume de cuir sombre." in book.text
    assert "recueil manuscrit" in book.text
    assert "Je ne garantis rien." in book.text


def test_gage_is_an_artefact_without_legend(client: TestClient, world: World) -> None:
    login(client, "maitre")
    grid = client.get("/mj/articles?section=lore&vue=artefacts")
    assert "Le Gage d&#39;Arès" in grid.text
    assert "Le Joug de Malvaar" in grid.text
    fiche = client.get(f"/mj/articles/{GAGE_ID}")
    assert "Légende associée" not in fiche.text
    assert "Bénie par Arès." in fiche.text
    assert "Un disque de bronze." in fiche.text
    assert "Le démon guette." in fiche.text
    filtered = client.get("/mj/articles?section=lore&vue=artefacts&rarete=legendaire")
    assert "Le Gage d&#39;Arès" in filtered.text
    assert f"/mj/articles/{JOUG_ID}" not in filtered.text
    search = client.get("/mj/articles?section=lore&vue=artefacts&q=joug")
    assert f"/mj/articles/{JOUG_ID}" in search.text
    assert f"/mj/articles/{GAGE_ID}" not in search.text


def test_session_one_events_left_the_lore_and_the_frise(
    client: TestClient, other_client: TestClient, world: World, db: Session
) -> None:
    _share(db, world.joug, world.empire)
    login(client, "maitre")
    for view in ("legendes", "frise", "ouvrages", "artefacts"):
        page = client.get(f"/mj/articles?section=lore&vue={view}")
        assert "Événements de la session 1" not in page.text, view
        assert "La dispute au marché" not in page.text, view
    frise = client.get("/mj/articles?section=lore&vue=frise")
    assert "Le Joug de Malvaar" in frise.text
    assert "L&#39;Empire d&#39;Ébène et les Échos" in frise.text
    assert "An 0 – An 749" in frise.text
    assert "La fondation de l&#39;Héliopolis" in frise.text
    fiche = client.get(f"/mj/articles/{JOUG_ID}")
    assert "La dispute au marché." in fiche.text
    login(other_client, "alice")
    player_fiche = other_client.get(f"/joueur/articles/{JOUG_ID}")
    assert "La dispute au marché" not in player_fiche.text
    assert "Le combat du Gage" not in other_client.get(f"/joueur/articles/{GAGE_ID}").text
    player_frise = other_client.get("/joueur/articles?section=lore&vue=frise")
    assert "La dispute au marché" not in player_frise.text


def test_gm_only_content_and_dm_notes_never_reach_players(
    client: TestClient, other_client: TestClient, world: World, db: Session
) -> None:
    login(client, "alice")
    assert client.get(f"/joueur/lore/recits/{world.joug.id}").status_code == 404
    hidden = client.get("/joueur/articles?section=lore&vue=legendes")
    assert "Le Joug de Malvaar</span>" not in hidden.text
    _share(db, world.joug)
    secret_part = _parts(db, world.joug)[2]
    secret_part.gm_only = True
    db.commit()
    legends = client.get("/joueur/articles?section=lore&vue=legendes")
    assert "Le Joug de Malvaar" in legends.text
    assert "Malvaar</h2>" in legends.text
    story = client.get(f"/joueur/lore/recits/{world.joug.id}")
    assert story.status_code == 200
    titles = re.findall(r'class="lore-parts__title">([^<]+)<', story.text)
    assert len(titles) == 6
    assert "Le règne du Dompteur" not in story.text
    assert "Non daté" in story.text
    assert "523" not in story.text
    for text in (
        "À vérifier",
        "MJ seulement",
        "Modifier",
        world.joug.review_note[:40],
        "Ajouter une partie",
    ):
        assert text not in story.text, text
    assert client.get(f"/joueur/lore/recits/{world.joug.id}/parties/7").status_code == 404
    assert client.get(f"/joueur/lore/{secret_part.id}").status_code == 404
    redirect = client.get(f"/joueur/lore/{_parts(db, world.joug)[0].id}", follow_redirects=False)
    assert redirect.headers["location"] == f"/joueur/lore/recits/{world.joug.id}/parties/1"
    assert "Artefact associé" in story.text
    assert "Ouvrage associé" not in story.text
    assert client.get(f"/joueur/lore/ouvrages/{world.feuillets.id}").status_code == 404
    assert client.get(f"/joueur/lore/recits/{world.empire.id}").status_code == 404
    fiche = client.get(f"/joueur/articles/{JOUG_ID}")
    assert fiche.status_code == 200
    for text in (
        "Le secret du Joug",
        "Le patron secret",
        "Une piste cachée",
        "La dispute au marché",
        "un temps antérieur",
        "Ajouter au Lore",
        "Modifier les caractéristiques",
    ):
        assert text not in fiche.text, text
    assert "Une amulette de fer noir." in fiche.text
    assert "Légende associée" in fiche.text
    gage = client.get(f"/joueur/articles/{GAGE_ID}")
    for text in ("Khaetar la porte", "Le démon guette", "Le secret du Gage"):
        assert text not in gage.text, text
    assert "Bénie par Arès." in gage.text
    login(other_client, "bob")
    assert other_client.get(f"/joueur/lore/recits/{world.joug.id}").status_code == 404
    assert "Le Joug de Malvaar" not in other_client.get("/joueur/articles?section=lore").text


def test_add_to_lore_is_dm_only_and_creates_gm_only_entries(
    client: TestClient, other_client: TestClient, world: World, db: Session
) -> None:
    clergy = make_article(
        db,
        "Clergé d'Arès",
        template_type="organization",
        content=(
            "[h1]Présentation[/h1][p]Le culte.[/p][h1]Histoire[/h1][p]Fondé jadis.[/p]"
            "[h1]Secrets[/h1][p]Caché.[/p]"
        ),
    )
    sword = make_article(
        db,
        "L'Épée neuve",
        template_type="item",
        content="[h1]Description[/h1][p]Une lame.[/p][h1]Secrets[/h1][p]Maudite.[/p]",
    )
    login(other_client, "alice")
    for genre in ("legende", "artefact", "ouvrage"):
        response = post_form(
            other_client, f"/mj/lore/ajouter/{clergy.wa_id}", {"genre": genre}, "/connexion/mj"
        )
        assert response.status_code == 404
    assert (
        db.scalar(
            select(func.count())
            .select_from(LoreStory)
            .where(LoreStory.wa_article_id == clergy.wa_id)
        )
        == 0
    )
    login(client, "maitre")
    page = client.get(f"/mj/articles/{clergy.wa_id}")
    assert "Ajouter au Lore" in page.text
    response = post_form(client, f"/mj/lore/ajouter/{clergy.wa_id}", {"genre": "legende"}, "/mj")
    assert response.status_code == 303
    story = db.scalar(select(LoreStory).where(LoreStory.wa_article_id == clergy.wa_id))
    assert (story.kind, story.gm_only, story.type.label) == ("legende", True, "Légende")
    parts = _parts(db, story)
    assert [part.title for part in parts] == ["Présentation", "Histoire"]
    assert all(part.gm_only and part.text == "" for part in parts)
    again = post_form(client, f"/mj/lore/ajouter/{clergy.wa_id}", {"genre": "legende"}, "/mj")
    assert again.status_code == 303
    assert (
        db.scalar(
            select(func.count())
            .select_from(LoreStory)
            .where(LoreStory.wa_article_id == clergy.wa_id)
        )
        == 1
    )
    post_form(client, f"/mj/lore/ajouter/{clergy.wa_id}", {"genre": "ouvrage"}, "/mj")
    book = db.scalar(
        select(LoreStory).where(
            LoreStory.wa_article_id == clergy.wa_id, LoreStory.kind == "ouvrage"
        )
    )
    assert book.gm_only
    post_form(client, f"/mj/lore/ajouter/{sword.wa_id}", {"genre": "artefact"}, "/mj")
    artefact = service.get_artefact(db, sword.wa_id)
    assert artefact.gm_only
    sections = db.scalars(select(LoreEntry).where(LoreEntry.wa_article_id == sword.wa_id)).all()
    assert {(entry.title, entry.placement, entry.gm_only) for entry in sections} == {
        ("Description", "physical", True),
        ("Secrets", "dm_note", True),
    }
    fiche = client.get(f"/mj/articles/{sword.wa_id}")
    assert "Une lame." in fiche.text
    assert "Maudite." in fiche.text
    db.add(
        make_character(db, "Alice 2", world.alice, make_article(db, "Alice bis"), [sword, clergy])
    )
    db.commit()
    assert other_client.get(f"/joueur/lore/recits/{story.id}").status_code == 404
    grid = other_client.get("/joueur/articles?section=lore&vue=artefacts")
    assert f"/joueur/articles/{sword.wa_id}" not in grid.text
    player_fiche = other_client.get(f"/joueur/articles/{sword.wa_id}")
    assert "Maudite." not in player_fiche.text
    assert "Une lame." not in player_fiche.text


def test_legend_types_are_configurable(
    client: TestClient, other_client: TestClient, world: World, db: Session
) -> None:
    login(other_client, "alice")
    assert (
        post_form(other_client, "/mj/lore/types", {"label": "Intrus"}, "/connexion/mj").status_code
        == 404
    )
    assert other_client.get("/mj/lore/reglages").status_code == 404
    login(client, "maitre")
    settings = client.get("/mj/lore/reglages")
    assert settings.status_code == 200
    assert "Mythe" in settings.text and "Malvaar" in settings.text
    post_form(client, "/mj/lore/types", {"label": "Chronique"}, "/mj/lore/reglages")
    chronicle = db.scalar(select(LoreType).where(LoreType.label == "Chronique"))
    assert chronicle is not None
    history = db.scalar(select(LoreType).where(LoreType.label == "Histoire"))
    post_form(client, f"/mj/lore/types/{history.id}/supprimer", {}, "/mj/lore/reglages")
    db.expire_all()
    assert db.get(LoreType, history.id) is not None
    post_form(
        client,
        f"/mj/lore/types/{history.id}",
        {"label": "Histoire ancienne", "position": "9"},
        "/mj/lore/reglages",
    )
    db.expire_all()
    assert db.get(LoreType, history.id).label == "Histoire ancienne"
    legends = client.get(f"/mj/articles?section=lore&vue=legendes&type={history.id}")
    assert "Histoire ancienne" in legends.text
    assert "L&#39;Empire d&#39;Ébène et les Échos" in legends.text
    assert f"/mj/lore/recits/{world.joug.id}" not in legends.text
    db.get(LoreStory, world.joug.id).group_id = None
    db.commit()
    titles = ("L&#39;Empire d&#39;Ébène", "La fondation de l&#39;Héliopolis", "Le Joug de Malvaar")
    alpha = client.get("/mj/articles?section=lore&vue=legendes&tri=alpha").text
    order = [alpha.index(f'lore-story__title">{title}') for title in titles]
    assert order == sorted(order)
    chrono = client.get("/mj/articles?section=lore&vue=legendes").text
    order = [chrono.index(f'lore-story__title">{title}') for title in titles]
    assert order[2] < order[0] < order[1]
    search = client.get("/mj/articles?section=lore&vue=legendes&q=heliopolis")
    assert "La fondation de l&#39;Héliopolis" in search.text
    assert "Le Joug de Malvaar</span>" not in search.text
    chronicle_id = chronicle.id
    post_form(client, f"/mj/lore/types/{chronicle_id}/supprimer", {}, "/mj/lore/reglages")
    db.expire_all()
    assert db.get(LoreType, chronicle_id) is None
    response = post_form(
        client,
        f"/mj/lore/recits/{world.empire.id}",
        {
            "title": "L'Empire d'Ébène et les Échos",
            "type_id": "",
            "new_group": "Empire",
            "gm_only": "1",
        },
        "/mj/lore/reglages",
    )
    assert response.status_code == 303
    db.expire_all()
    empire = db.get(LoreStory, world.empire.id)
    assert empire.type_id is None
    assert db.get(LoreGroup, empire.group_id).title == "Empire"


def test_dm_reorders_and_adds_parts(client: TestClient, world: World, db: Session) -> None:
    login(client, "maitre")
    parts = _parts(db, world.joug)
    post_form(
        client,
        f"/mj/lore/{parts[1].id}/deplacer",
        {"sens": "haut"},
        f"/mj/lore/recits/{world.joug.id}",
    )
    db.expire_all()
    assert [part.title for part in _parts(db, world.joug)][:2] == ["La forge", "Le sorcier"]
    response = post_form(
        client,
        f"/mj/lore/recits/{world.joug.id}/parties",
        {"source_article_id": FEUILLETS_ID, "source_section": "On dit aussi", "title": ""},
        f"/mj/lore/recits/{world.joug.id}",
    )
    assert response.status_code == 303
    added = _parts(db, world.joug)[-1]
    assert (added.title, added.gm_only, added.position) == ("On dit aussi", True, 7)
    page = client.get(f"/mj/lore/recits/{world.joug.id}/parties/8")
    assert "On dit aussi bien des choses." in page.text


def test_excerpts_read_bold_label_blocks_and_narrative_sections() -> None:
    first = find_excerpt(SPECULUM, "L'Empire d'Ébène et les Doublés", blocks=True)
    assert first.status == AnchorStatus.FOUND
    assert first.source == "[p]Le village guerrier devint empire.[/p]"
    assert find_excerpt(SPECULUM, "L'Empire d'Ébène et les Doublés").source == (
        "[p][b]L'Empire d'Ébène et les Doublés[/b][/p]"
    )
    founding = find_excerpt(HELIOPOLIS, "Histoire Fondatrice", blocks=True)
    assert "Aetios" in founding.source
    assert "république" not in founding.source
    assert find_excerpt(JOUG, "Secrets").status == AnchorStatus.FORBIDDEN
    assert find_excerpt(JOUG, "Secrets", forbidden=()).status == AnchorStatus.FOUND
    article = WaArticle(wa_id="x", title="Le Joug de Malvaar", content=JOUG)
    assert narrative_sections(article) == [
        "Description",
        *JOUG_PARTS[:6],
        "Pouvoirs",
        "Ceux qui la convoitent",
        "Légendes et rumeurs",
    ]
