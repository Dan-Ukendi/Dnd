from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ArticleCategory,
    Character,
    CharacterProfile,
    CharacterRelation,
    MediaAsset,
    Role,
    User,
    WaArticle,
)
from app.services import person_tags as service
from app.services.sync import sync_world
from tests.factories import login, make_article, make_character, make_user, post_form
from tests.test_sync import WORLD, FakeSource

SECRET_TEXT = "Tuya cache le sceau du conseil"
SECRET_URL = "https://www.worldanvil.com/w/monde/a/tuya-person"
SECRET_IMAGE = "https://www.worldanvil.com/uploads/images/tuya-secret.png"


@dataclass
class PersonWorld:
    dm: User
    alice: User
    bruno: User
    khaetar_article: WaArticle
    zilvra_article: WaArticle
    krateros: WaArticle
    tuya: WaArticle
    gerard: WaArticle
    octave: WaArticle
    city: WaArticle
    khaetar: Character
    zilvra: Character
    tuya_portrait: MediaAsset


def _relation(
    character: Character, article: WaArticle, name: str, position: int, **fields: str
) -> CharacterRelation:
    return CharacterRelation(
        character_id=character.id,
        position=position,
        category=fields.pop("category", "connaissances"),
        name=name,
        wa_article_id=article.wa_id,
        text=fields.pop("text", ""),
        gm_notes=fields.pop("gm_notes", ""),
        role=fields.pop("role", None),
    )


@pytest.fixture
def world(db: Session) -> PersonWorld:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    bruno = make_user(db, "bruno")
    khaetar_article = make_article(db, "Khaetar Voss", content="[p]Un moine.[/p]")
    zilvra_article = make_article(db, "Zilvra", content="[p]Une paladine.[/p]")
    krateros = make_article(
        db,
        "Kratéros",
        content="[p][i]Évêque du Temple Blanc[/i][/p][p]Le prêtre.[/p]",
        raw={
            "title": "Kratéros",
            "portrait": {
                "id": 3,
                "url": "https://www.worldanvil.com/uploads/images/krateros.png",
                "state": "public",
            },
        },
    )
    tuya = make_article(
        db,
        "Tuya",
        content=f"[p][i]{SECRET_TEXT}[/i][/p]",
        player_visible=False,
        url=SECRET_URL,
        raw={"title": "Tuya", "portrait": {"id": 9, "url": SECRET_IMAGE, "state": "public"}},
    )
    gerard = make_article(db, "Gérard", content="[p]Le barman.[/p]")
    octave = make_article(db, "Octave", content="[p]Le cousin.[/p]")
    city = make_article(db, "Khüchniin Khöndii", template_type="settlement")
    khaetar = make_character(
        db, "Khaetar Voss", alice, khaetar_article, extras=[krateros, gerard, tuya]
    )
    zilvra = make_character(db, "Zilvra Vhaelor", bruno, zilvra_article, extras=[krateros])
    portrait = MediaAsset(
        wa_article_id=tuya.wa_id,
        role="portrait",
        title="Portrait de Tuya",
        original_filename="tuya.png",
        content_type="image/png",
        size=10,
        sha256="f" * 64,
    )
    db.add(portrait)
    db.add_all(
        [
            _relation(khaetar, tuya, "Tuya", 0, role="amie du marché", text="Elle l'a nourri."),
            _relation(khaetar, krateros, "Kratéros", 1),
            _relation(khaetar, zilvra_article, "Zilvra", 2, category="allies"),
            _relation(zilvra, gerard, "Gérard", 0),
        ]
    )
    db.commit()
    service.ensure_categories(db)
    service.set_category(db, khaetar_article, "character")
    service.set_category(db, zilvra_article, "character")
    db.commit()
    return PersonWorld(
        dm,
        alice,
        bruno,
        khaetar_article,
        zilvra_article,
        krateros,
        tuya,
        gerard,
        octave,
        city,
        khaetar,
        zilvra,
        portrait,
    )


def _categories(db: Session) -> dict[str, str]:
    db.expire_all()
    return {row.wa_id: row.category for row in db.scalars(select(ArticleCategory))}


@pytest.fixture
def rerun_migration(db: Session) -> Iterator[object]:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False

    def rerun() -> None:
        db.rollback()
        command.downgrade(config, "0010")
        command.upgrade(config, "head")

    yield rerun
    command.upgrade(config, "head")


def test_migration_classifies_played_characters_and_encounters(
    db: Session, rerun_migration: object
) -> None:
    player = make_user(db, "alice")
    former = make_user(db, "demo", active=False)
    pc = make_article(db, "Khaetar Voss")
    demo = make_article(db, "Bob")
    npc = make_article(db, "Octave")
    place = make_article(db, "Une cité", template_type="settlement")
    make_character(db, "Khaetar Voss", player, pc)
    make_character(db, "Bob", former, demo)
    rerun_migration()
    assert _categories(db) == {
        pc.wa_id: "character",
        demo.wa_id: "encounter",
        npc.wa_id: "encounter",
    }
    assert place.wa_id not in _categories(db)


def test_sync_tags_new_person_fiches_as_encounter_and_never_overwrites(db: Session) -> None:
    source = FakeSource()
    person = "aaaaaaaa-0000-0000-0000-00000000000a"
    place = "aaaaaaaa-0000-0000-0000-00000000000b"
    source.put(person, "Octave", "[p]Historien.[/p]", "2026-11-22 13:45:47")
    source.put(
        place,
        "Une cité",
        "[p]Une ville.[/p]",
        "2026-11-22 13:45:47",
        templateType="settlement",
        entityClass="Settlement",
    )
    sync_world(db, source, WORLD, full=False, trigger="cli")
    assert _categories(db) == {person: "encounter"}

    service.set_category(db, db.get(WaArticle, person), "lore")
    db.commit()
    source.put(person, "Octave", "[p]Historien, cousin.[/p]", "2026-11-23 10:00:00")
    sync_world(db, source, WORLD, full=True, trigger="cli")
    assert _categories(db) == {person: "lore"}

    del source.articles[person]
    sync_world(db, source, WORLD, full=True, trigger="cli")
    assert db.get(WaArticle, person).removed_at is not None
    assert _categories(db) == {person: "lore"}


def test_dm_personnage_page_opens_on_one_tile_per_tag(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=personnage").text
    titles = [part.split("</span>")[0] for part in page.split('<span class="tag-tile__title">')[1:]]
    assert titles == ["Lore", "Character", "Liens", "Rencontre campagne"]
    assert "Aucune fiche pour l'instant" in page
    assert "4 fiches" in page
    assert "2 fiches" in page
    assert "krateros.png" in page
    assert 'href="/mj/articles?section=personnage&amp;tag=encounter"' in page
    assert "Khüchniin Khöndii" not in page


def test_dm_tag_list_filters_cards_and_switches_tags(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=personnage&tag=encounter").text
    assert 'class="lore-filter tag-chip tag-chip--encounter"' in page
    assert 'aria-current="true">Rencontre campagne</a>' in page
    for title in ("Kratéros", "Tuya", "Gérard", "Octave"):
        assert f'<h3 class="card__title">{title}</h3>' in page
    assert '<h3 class="card__title">Khaetar Voss</h3>' not in page
    assert '<input type="hidden" name="tag" value="encounter">' in page

    partial = client.get(
        "/mj/articles?section=personnage&tag=character&q=Voss",
        headers={"HX-Request": "true", "HX-Target": "article-results"},
    ).text
    assert '<h3 class="card__title">Khaetar Voss</h3>' in partial
    assert "Kratéros" not in partial
    assert client.get("/mj/articles?section=personnage&tag=autre").status_code == 404


def test_dm_cards_show_tag_badges_instead_of_the_duplicate_pill(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=personnage&tag=encounter").text
    assert '<span class="pill pill--encounter">Rencontre campagne</span>' in page
    assert '<span class="pill pill--lien">Lien Khaetar Voss</span>' in page
    assert '<span class="pill pill--lien">Lien Zilvra Vhaelor</span>' in page
    assert '<span class="pill">Personnage</span>' not in page
    assert '<span class="pill pill--muted">Personnage</span>' not in page
    everything = client.get("/mj/articles").text
    assert '<span class="pill pill--character">Character</span>' in everything


def test_dm_liens_lists_played_characters_then_their_links(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "maitre")
    groups = client.get("/mj/articles?section=personnage&tag=liens").text
    assert "Khaetar Voss" in groups
    assert "Zilvra Vhaelor" in groups
    assert "2 liens" in groups
    assert "1 lien<" in groups
    page = client.get(f"/mj/articles?section=personnage&tag=liens&pj={world.khaetar.id}").text
    assert '<h3 class="card__title">Tuya</h3>' in page
    assert '<h3 class="card__title">Kratéros</h3>' in page
    assert '<h3 class="card__title">Zilvra</h3>' not in page
    assert '<h3 class="card__title">Gérard</h3>' not in page
    assert "2 liens pour Khaetar Voss" in page
    assert client.get("/mj/articles?section=personnage&tag=liens&pj=999").status_code == 404


def test_dm_fiche_shows_badges_and_the_tag_form(client: TestClient, world: PersonWorld) -> None:
    login(client, "maitre")
    page = client.get(f"/mj/articles/{world.tuya.wa_id}").text
    assert '<span class="pill pill--encounter">Rencontre campagne</span>' in page
    assert '<span class="pill pill--lien">Lien Khaetar Voss</span>' in page
    assert f'action="/mj/articles/{world.tuya.wa_id}/tags"' in page
    assert '<option value="encounter" selected>Rencontre campagne</option>' in page
    assert f'name="pj" value="{world.khaetar.id}" checked disabled' in page
    assert "à modifier dans le dossier" in page
    assert f'name="pj" value="{world.zilvra.id}">' in page
    pc_page = client.get(f"/mj/articles/{world.khaetar_article.wa_id}").text
    assert 'class="person-tags__links" disabled' in pc_page
    assert f'name="pj" value="{world.khaetar.id}"' not in pc_page
    city_page = client.get(f"/mj/articles/{world.city.wa_id}").text
    assert "person-tags" not in city_page


def _post_tags(client: TestClient, article: WaArticle, category: str, pjs: list[int]) -> None:
    data: dict[str, object] = {"category": category, "pj": [str(pj) for pj in pjs]}
    response = post_form(client, f"/mj/articles/{article.wa_id}/tags", data, "/mj")
    assert response.status_code == 303


def test_dm_links_create_minimal_relations_and_only_remove_empty_ones(
    client: TestClient, db: Session, world: PersonWorld
) -> None:
    login(client, "maitre")
    _post_tags(client, world.octave, "lore", [world.zilvra.id, world.khaetar.id])
    db.expire_all()
    assert _categories(db)[world.octave.wa_id] == "lore"
    created = db.scalars(
        select(CharacterRelation).where(CharacterRelation.wa_article_id == world.octave.wa_id)
    ).all()
    assert {(r.character_id, r.category, r.name, r.text, r.gm_notes) for r in created} == {
        (world.zilvra.id, "autres", "Octave", "", ""),
        (world.khaetar.id, "autres", "Octave", "", ""),
    }
    assert db.get(CharacterProfile, world.zilvra.id).relations_edited_at is not None
    assert max(r.position for r in db.get(Character, world.khaetar.id).relations) == 3

    _post_tags(client, world.octave, "lore", [world.khaetar.id])
    _post_tags(client, world.tuya, "encounter", [])
    db.expire_all()
    remaining = db.scalars(
        select(CharacterRelation.character_id).where(
            CharacterRelation.wa_article_id == world.octave.wa_id
        )
    ).all()
    assert remaining == [world.khaetar.id]
    tuya = db.scalars(
        select(CharacterRelation).where(CharacterRelation.wa_article_id == world.tuya.wa_id)
    ).all()
    assert [relation.text for relation in tuya] == ["Elle l'a nourri."]


def test_character_category_keeps_relations_but_leaves_liens(
    client: TestClient, db: Session, world: PersonWorld
) -> None:
    login(client, "maitre")
    _post_tags(client, world.krateros, "character", [])
    db.expire_all()
    assert _categories(db)[world.krateros.wa_id] == "character"
    assert db.scalars(
        select(CharacterRelation).where(CharacterRelation.wa_article_id == world.krateros.wa_id)
    ).all()
    page = client.get(f"/mj/articles?section=personnage&tag=liens&pj={world.khaetar.id}").text
    assert '<h3 class="card__title">Kratéros</h3>' not in page
    fiche = client.get(f"/mj/articles/{world.krateros.wa_id}").text
    assert "Lien Khaetar Voss" not in fiche


def test_invalid_tags_are_refused(client: TestClient, db: Session, world: PersonWorld) -> None:
    login(client, "maitre")
    post_form(client, f"/mj/articles/{world.octave.wa_id}/tags", {"category": "pnj"}, "/mj")
    post_form(client, f"/mj/articles/{world.city.wa_id}/tags", {"category": "lore"}, "/mj")
    categories = _categories(db)
    assert categories[world.octave.wa_id] == "encounter"
    assert world.city.wa_id not in categories


def test_players_cannot_change_tags(client: TestClient, db: Session, world: PersonWorld) -> None:
    login(client, "alice")
    response = post_form(
        client,
        f"/mj/articles/{world.octave.wa_id}/tags",
        {"category": "lore", "pj": str(world.khaetar.id)},
        "/joueur",
    )
    assert response.status_code == 404
    assert _categories(db)[world.octave.wa_id] == "encounter"
    assert client.get("/mj/articles?section=personnage").status_code == 404


def test_player_personnage_section_has_character_and_liens_only(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "alice")
    page = client.get("/joueur/articles?section=personnage").text
    titles = [part.split("</span>")[0] for part in page.split('<span class="tag-tile__title">')[1:]]
    assert titles == ["Character", "Liens"]
    assert "Rencontre campagne" not in page
    assert "1 fiche<" in page
    assert "2 fiches" in page
    assert 'href="/joueur/articles?section=personnage"' in page
    for tag in ("lore", "encounter", "autre"):
        assert client.get(f"/joueur/articles?section=personnage&tag={tag}").status_code == 404


def test_player_character_list_shows_only_their_own_character(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "alice")
    page = client.get("/joueur/articles?section=personnage&tag=character").text
    assert '<h3 class="card__title">Khaetar Voss</h3>' in page
    assert "Zilvra" not in page
    assert '<span class="pill pill--character">Character</span>' in page


def test_player_liens_show_private_npcs_as_names_without_any_private_data(
    client: TestClient, world: PersonWorld
) -> None:
    login(client, "alice")
    pages = [
        client.get("/joueur/articles?section=personnage").text,
        client.get("/joueur/articles?section=personnage&tag=liens").text,
        client.get(f"/joueur/articles?section=personnage&tag=liens&pj={world.khaetar.id}").text,
    ]
    links = pages[2]
    assert '<h3 class="card__title">Tuya</h3>' in links
    assert "amie du marché" in links
    assert '<span class="pill pill--lien">Lien Khaetar Voss</span>' in links
    assert f'href="/joueur/articles/{world.krateros.wa_id}"' in links
    assert '<div class="card card--stub" data-section="personnage">' in links
    for page in pages:
        assert world.tuya.wa_id not in page
        assert SECRET_URL not in page
        assert SECRET_IMAGE not in page
        assert SECRET_TEXT not in page
        assert f"/media/{world.tuya_portrait.id}/" not in page
        assert "Gérard" not in page
        assert "Zilvra" not in page
    assert (
        client.get(
            f"/joueur/articles?section=personnage&tag=liens&pj={world.zilvra.id}"
        ).status_code
        == 404
    )
    assert client.get(f"/joueur/articles/{world.tuya.wa_id}").status_code == 404


def test_player_cards_only_carry_badges_of_their_own_character(
    client: TestClient, other_client: TestClient, world: PersonWorld
) -> None:
    login(client, "alice")
    alice = client.get("/joueur/articles").text
    assert '<span class="pill pill--lien">Lien Khaetar Voss</span>' in alice
    assert "Rencontre campagne" not in alice
    login(other_client, "bruno")
    bruno = other_client.get("/joueur/articles").text
    assert '<h3 class="card__title">Kratéros</h3>' in bruno
    assert "Lien Khaetar Voss" not in bruno
    assert "Rencontre campagne" not in bruno
    fiche = other_client.get(f"/joueur/articles/{world.krateros.wa_id}").text
    assert "Lien Khaetar Voss" not in fiche
    assert "pill--encounter" not in fiche
