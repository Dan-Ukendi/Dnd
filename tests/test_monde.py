import json
import re
from dataclasses import dataclass

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models import Place, Role, User, WaArticle, WorldCategory
from app.services import world as service
from app.services.structures import Viewer
from app.services.sync import sync_world
from tests.factories import bare_numbers, login, make_article, make_character, make_user, post_form
from tests.test_sync import WORLD, FakeSource

COUNTRY_RAW = {"articleMetadata": '{ "variant": "country" }'}
SECRET_TEXT = "Le cellier secret du Khan"


@dataclass
class MondeWorld:
    dm: User
    alice: User
    nation: WaArticle
    city: WaArticle
    house: WaArticle
    hidden_shop: WaArticle
    guild: WaArticle
    empire: WaArticle


def _place(
    db: Session,
    key: str,
    kind: str,
    parent: Place | None,
    article: WaArticle | None,
    visibility: str = "joueurs",
) -> Place:
    place = Place(
        key=key,
        name=key,
        kind=kind,
        parent_id=parent.id if parent else None,
        wa_article_id=article.wa_id if article else None,
        visibility=visibility,
    )
    db.add(place)
    db.commit()
    return place


def _categorise(db: Session, article: WaArticle, category: str) -> None:
    db.add(WorldCategory(wa_id=article.wa_id, category=category))
    db.commit()


@pytest.fixture
def world(db: Session) -> MondeWorld:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    nation = make_article(db, "Le Khaanat", template_type="organization", raw=COUNTRY_RAW)
    city = make_article(db, "La Vallée", template_type="settlement", content="[p]Une cité.[/p]")
    house = make_article(db, "La demeure", template_type="landmark")
    hidden_shop = make_article(
        db, "Le cellier", template_type="landmark", content=f"[p]{SECRET_TEXT}[/p]"
    )
    guild = make_article(db, "La Guilde", template_type="organization")
    empire = make_article(db, "L'Empire", template_type="organization", raw=COUNTRY_RAW)
    for article, category in (
        (nation, "pays"),
        (city, "ville"),
        (house, "batiment"),
        (hidden_shop, "batiment"),
    ):
        _categorise(db, article, category)
    root = _place(db, "khaanat", "territoire", None, nation)
    cite = _place(db, "vallee", "ville", root, city)
    district = _place(db, "quartier", "quartier", cite, None)
    _place(db, "demeure", "batiment", district, house)
    _place(db, "cellier", "batiment", cite, hidden_shop, visibility="mj")
    make_character(db, "Khaetar", alice, extras=[nation, city, house, guild])
    return MondeWorld(dm, alice, nation, city, house, hidden_shop, guild, empire)


def test_section_opens_on_three_tiles_with_counts(client: TestClient, world: MondeWorld) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=monde").text
    tiles = re.findall(r'class="world-tile world-tile--(\w+)', page)
    assert tiles == ["pays", "ville", "batiment"]
    assert ">Pays</span>" in page and ">Villes</span>" in page and ">Bâtiments</span>" in page
    assert "1 fiche<" in page and "2 fiches<" in page
    assert "La Guilde" not in page
    assert bare_numbers(page) == []


def test_category_list_has_bar_search_and_cards(client: TestClient, world: MondeWorld) -> None:
    login(client, "maitre")
    page = client.get("/mj/articles?section=monde&categorie=batiments").text
    assert 'aria-current="true">Bâtiments</a>' in page
    assert "categorie=pays" in page and "categorie=villes" in page
    assert "La demeure" in page and "Le cellier" in page and "La Vallée" not in page
    found = client.get("/mj/articles?section=monde&categorie=batiments&q=demeure").text
    assert "La demeure" in found and "Le cellier" not in found
    partial = client.get(
        "/mj/articles?section=monde&categorie=batiments&q=zzz",
        headers={"HX-Request": "true", "HX-Target": "monde-results"},
    ).text
    assert partial.lstrip().startswith("<section") and "Aucun bâtiment" in partial
    assert client.get("/mj/articles?section=monde&categorie=regions").status_code == 404


def test_nations_leave_organisation_and_show_as_pays(client: TestClient, world: MondeWorld) -> None:
    login(client, "maitre")
    organisation = client.get("/mj/articles?section=organisation").text
    assert "La Guilde" in organisation and "Le Khaanat" not in organisation
    fiche = client.get(f"/mj/articles/{world.nation.wa_id}").text
    assert '<span class="pill">Pays</span>' in fiche
    assert 'class="pill">Organisation<' not in fiche
    assert "<dd>Organisation</dd>" not in fiche and "<dd>Pays</dd>" in fiche
    assert "La Vallée" in fiche and "Établissement" not in fiche


def test_relations_are_links_only_when_written(client: TestClient, world: MondeWorld) -> None:
    login(client, "maitre")
    house = client.get(f"/mj/articles/{world.house.wa_id}").text
    assert f'href="/mj/articles/{world.city.wa_id}"' in house
    assert f'href="/mj/articles/{world.nation.wa_id}"' in house
    city = client.get(f"/mj/articles/{world.city.wa_id}").text
    assert f'href="/mj/articles/{world.house.wa_id}"' in city
    assert f'href="/mj/articles/{world.nation.wa_id}"' in city


def test_city_without_attachment_shows_no_country(db: Session, world: MondeWorld) -> None:
    lonely = make_article(db, "Le hameau", template_type="settlement")
    _categorise(db, lonely, "ville")
    fiche = service.world_fiche(db, lonely, Viewer(dm=True))
    assert fiche.country is None and fiche.buildings == []
    assert fiche.children_empty == "Aucun bâtiment"


def test_player_sees_only_shared_fiches_and_visible_relations(
    client: TestClient, db: Session, world: MondeWorld
) -> None:
    login(client, "alice")
    home = client.get("/joueur/articles?section=monde").text
    assert "Le cellier" not in home
    batiments = client.get("/joueur/articles?section=monde&categorie=batiments").text
    assert "La demeure" in batiments and "Le cellier" not in batiments
    city = client.get(f"/joueur/articles/{world.city.wa_id}").text
    assert f'href="/joueur/articles/{world.house.wa_id}"' in city
    assert "Le cellier" not in city and SECRET_TEXT not in city
    assert "categorie-monde" not in city and "Catégorie Monde" not in city
    assert client.get(f"/joueur/articles/{world.hidden_shop.wa_id}").status_code == 404
    assert post_form(
        client,
        f"/mj/articles/{world.city.wa_id}/categorie-monde",
        {"category": "pays"},
        "/joueur",
    ).status_code in (303, 403, 404)
    db.expire_all()
    assert db.get(WaArticle, world.city.wa_id).world_category == "ville"


def test_player_relation_hidden_when_place_is_dm_only(db: Session, world: MondeWorld) -> None:
    place = db.scalar(select(Place).where(Place.key == "demeure"))
    place.visibility = "mj"
    db.commit()
    viewer = Viewer(dm=False, accessible=frozenset({world.city.wa_id, world.house.wa_id}))
    fiche = service.world_fiche(db, world.house, viewer)
    assert fiche.city is None and fiche.country is None
    assert service.world_fiche(db, world.city, viewer).buildings == []


def test_dm_changes_the_category_and_sync_never_overwrites_it(db: Session) -> None:
    source = FakeSource()
    nation = "bbbbbbbb-0000-0000-0000-00000000000a"
    city = "bbbbbbbb-0000-0000-0000-00000000000b"
    guild = "bbbbbbbb-0000-0000-0000-00000000000c"
    source.put(
        nation,
        "Le Khaanat",
        "[p]Un pays.[/p]",
        "2026-11-22 13:45:47",
        templateType="organization",
        entityClass="Organization",
        articleMetadata='{ "variant": "country" }',
    )
    source.put(
        city,
        "Une cité",
        "[p]Une ville.[/p]",
        "2026-11-22 13:45:47",
        templateType="settlement",
        entityClass="Settlement",
    )
    source.put(
        guild,
        "La Guilde",
        "[p]Une guilde.[/p]",
        "2026-11-22 13:45:47",
        templateType="organization",
        entityClass="Organization",
    )
    sync_world(db, source, WORLD, full=False, trigger="cli")
    categories = {row.wa_id: row.category for row in db.scalars(select(WorldCategory))}
    assert categories == {nation: "pays", city: "ville"}

    service.set_category(db, db.get(WaArticle, city), "batiment")
    service.set_category(db, db.get(WaArticle, nation), "")
    source.put(
        city,
        "Une cité",
        "[p]Une grande ville.[/p]",
        "2026-11-23 10:00:00",
        templateType="settlement",
        entityClass="Settlement",
    )
    sync_world(db, source, WORLD, full=True, trigger="cli")
    db.expire_all()
    categories = {row.wa_id: row.category for row in db.scalars(select(WorldCategory))}
    assert categories == {nation: None, city: "batiment"}
    assert db.get(WaArticle, nation).world_category is None
    with pytest.raises(service.WorldCategoryError):
        service.set_category(db, db.get(WaArticle, city), "region")


def test_dm_form_sets_the_category(client: TestClient, db: Session, world: MondeWorld) -> None:
    login(client, "maitre")
    fiche = client.get(f"/mj/articles/{world.empire.wa_id}").text
    assert "Catégorie Monde" in fiche
    response = post_form(
        client, f"/mj/articles/{world.empire.wa_id}/categorie-monde", {"category": "pays"}, "/mj"
    )
    assert response.status_code == 303
    db.expire_all()
    assert db.get(WaArticle, world.empire.wa_id).world_category == "pays"
    page = client.get("/mj/articles?section=monde&categorie=pays").text
    assert "L&#39;Empire" in page or "L'Empire" in page
    person = make_article(db, "Octave")
    post_form(client, f"/mj/articles/{person.wa_id}/categorie-monde", {"category": "ville"}, "/mj")
    assert db.get(WorldCategory, person.wa_id) is None


def test_migration_classifies_fiches_and_removes_status_texts(db: Session) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    db.rollback()
    command.downgrade(config, "0016")
    try:
        connection = db.connection()
        articles = [
            ("cccccccc-0000-0000-0000-00000000000a", "Le Khaanat", "organization", COUNTRY_RAW),
            ("9a0ff719-7202-4be9-a52c-997e5af92751", "Speculum", "organization", COUNTRY_RAW),
            ("cccccccc-0000-0000-0000-00000000000b", "La Vallée", "settlement", {}),
            ("cccccccc-0000-0000-0000-00000000000c", "La Guilde", "organization", {}),
        ]
        for wa_id, title, template, raw in articles:
            connection.execute(
                text(
                    "INSERT INTO wa_articles (wa_id, title, template_type, raw, synced_at) "
                    "VALUES (:wa_id, :title, :template, CAST(:raw AS jsonb), now())"
                ),
                {"wa_id": wa_id, "title": title, "template": template, "raw": json.dumps(raw)},
            )
        connection.execute(
            text(
                "INSERT INTO places (key, name, kind, gm_notes, status) VALUES "
                "('vallee', 'La Vallée', 'ville', 'Incohérence : deux noms.', 'a_valider')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO timeline_events (code, title, start_year, importance, origin, status) "
                "VALUES ('A1', 'Forge', 1, 'majeur', 'canon', 'a_valider')"
            )
        )
        db.commit()
        command.upgrade(config, "head")
        rows = dict(db.execute(text("SELECT wa_id, category FROM world_categories")).all())
        assert rows == {
            "cccccccc-0000-0000-0000-00000000000a": "pays",
            "cccccccc-0000-0000-0000-00000000000b": "ville",
        }
        assert db.execute(text("SELECT gm_notes FROM places")).scalar() == ""
        inspector = inspect(db.get_bind())
        for table in ("places", "positions", "timeline_events"):
            assert "status" not in {column["name"] for column in inspector.get_columns(table)}
        for table in ("artefacts", "lore_stories", "lore_entries"):
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert "review_note" not in columns
        db.rollback()
        command.downgrade(config, "0016")
        inspector = inspect(db.get_bind())
        assert "world_categories" not in inspector.get_table_names()
        assert db.execute(text("SELECT status FROM places")).scalar() == "etabli"
    finally:
        db.rollback()
        command.upgrade(config, "head")
