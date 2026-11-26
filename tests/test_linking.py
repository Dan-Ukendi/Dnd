import json
import logging
from html.parser import HTMLParser

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CharacterProfile,
    LinkAlias,
    LinkExclusion,
    Role,
    TimelineEvent,
    WaArticle,
)
from app.presentation import fiche_body
from app.services import linking
from app.services.access import dm_render_context, player_render_context
from app.worldanvil.bbcode import RenderContext, render_bbcode, render_plain_text
from app.worldanvil.linking import Matcher, Name, Target
from tests.factories import login, make_article, make_character, make_user, new_id, post_form

KHAANAT = "k-1"
KHAANAT_SHORT = "k-2"
ARES = "a-1"
CLERGE = "c-1"
EMPIRE = "e-1"
KRATEROS = "kr-1"
JOUG = "j-1"
BOB = "bbbbbbbb-0000-0000-0000-000000000001"
SOPHIE = "s-1"
SOPHIE_TWIN = "s-2"
OX = "o-1"

TARGETS = [
    Target(KHAANAT, "Le Khaanat des Cinq Lances", "organisation", "Organisation"),
    Target(KHAANAT_SHORT, "Khaanat", "lieu", "Lieu"),
    Target(ARES, "Arès", "personnage", "Personnage"),
    Target(CLERGE, "Clergé d'Arès", "organisation", "Organisation"),
    Target(EMPIRE, "L'Empire d'Ébène", "lieu", "Lieu"),
    Target(KRATEROS, "Kratéros", "personnage", "Personnage"),
    Target(JOUG, "Le Joug de Malvaar", "objet", "Objet"),
    Target(BOB, "Bob", "personnage", "Personnage"),
    Target(SOPHIE, "Sophie", "personnage", "Personnage"),
    Target(SOPHIE_TWIN, "Sophie", "lieu", "Lieu"),
    Target(OX, "Ox", "personnage", "Personnage"),
]


def matcher(exclusions: tuple[str, ...] = ()) -> Matcher:
    return Matcher.build(
        TARGETS, [Name(target.title, target.wa_id) for target in TARGETS], exclusions
    )


def context(
    *,
    accessible: set[str] | None = None,
    current: str | None = None,
    exclusions: tuple[str, ...] = (),
) -> RenderContext:
    def resolve(wa_id: str) -> str | None:
        if accessible is not None and wa_id not in accessible:
            return None
        return f"/x/{wa_id}"

    return RenderContext(
        resolve_article=resolve,
        resolve_article_url=lambda url: None,
        resolve_image=lambda image_id: None,
        player_mode=accessible is not None,
        matcher=matcher(exclusions),
        current=current,
    )


def links_in(html: str) -> list[tuple[str, str]]:
    class Links(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[tuple[str, str]] = []
            self.href: str | None = None
            self.text = ""

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = dict(attrs)
            if tag == "a" and "autolink" in (values.get("class") or ""):
                self.href = values.get("href")
                self.text = ""

        def handle_data(self, data: str) -> None:
            if self.href is not None:
                self.text += data

        def handle_endtag(self, tag: str) -> None:
            if tag == "a" and self.href is not None:
                self.found.append((self.text, self.href))
                self.href = None

    parser = Links()
    parser.feed(html)
    return parser.found


def render(source: str, **options: object) -> str:
    return str(render_bbcode(source, context(**options)))


def test_whole_words_only() -> None:
    html = render("[p]Bobine et Bobby croisent Bob.[/p]")
    assert links_in(html) == [("Bob", f"/x/{BOB}")]


def test_longest_match_wins() -> None:
    html = render("[p]Le Khaanat des Cinq Lances.[/p][h2]Ailleurs[/h2][p]Le Khaanat.[/p]")
    assert links_in(html) == [
        ("Le Khaanat des Cinq Lances", f"/x/{KHAANAT}"),
        ("Khaanat", f"/x/{KHAANAT_SHORT}"),
    ]


def test_french_elisions_and_contractions() -> None:
    html = render(
        "[p]Les terres de l'Empire d'Ébène, le prêtre d’Arès, au clergé d'Arès,"
        " le chef du Khaanat des Cinq Lances et le porteur du Joug de Malvaar.[/p]"
    )
    assert links_in(html) == [
        ("l'Empire d'Ébène", f"/x/{EMPIRE}"),
        ("Arès", f"/x/{ARES}"),
        ("clergé d'Arès", f"/x/{CLERGE}"),
        ("Khaanat des Cinq Lances", f"/x/{KHAANAT}"),
        ("Joug de Malvaar", f"/x/{JOUG}"),
    ]


def test_case_and_accent_insensitive() -> None:
    assert links_in(render("[p]KRATEROS parle.[/p]")) == [("KRATEROS", f"/x/{KRATEROS}")]
    assert links_in(render("[p]l'empire d'ebene[/p]")) == [("l'empire d'ebene", f"/x/{EMPIRE}")]


def test_duplicate_names_are_not_linked() -> None:
    current = matcher()
    assert current.conflicts == {"sophie": (SOPHIE, SOPHIE_TWIN)}
    assert links_in(render("[p]Sophie et Bob.[/p]")) == [("Bob", f"/x/{BOB}")]


def test_current_fiche_is_never_linked() -> None:
    html = render("[p]Bob parle à Kratéros.[/p]", current=BOB)
    assert links_in(html) == [("Kratéros", f"/x/{KRATEROS}")]


def test_only_the_first_occurrence_per_section() -> None:
    html = render(
        "[p]Bob, encore Bob.[/p][p]Toujours Bob.[/p][h2]Plus tard[/h2][p]Bob revient.[/p]"
    )
    assert links_in(html) == [("Bob", f"/x/{BOB}"), ("Bob", f"/x/{BOB}")]
    assert links_in(render("[p]Bob.[/p]")) == [("Bob", f"/x/{BOB}")]


def test_inaccessible_fiche_stays_plain_text_for_players() -> None:
    html = render("[p]Bob parle à Kratéros.[/p]", accessible={BOB})
    assert links_in(html) == [("Bob", f"/x/{BOB}")]
    assert "Kratéros" in html
    assert "Kratéros, fiche" not in html
    assert html.count("aria-label") == 1


def test_names_in_nested_list_items_are_linked() -> None:
    html = render("[ul]\n- La cour\n-- Kratéros sert Arès\n-- Bob\n[/ul]", accessible={BOB, ARES})
    assert links_in(html) == [("Arès", f"/x/{ARES}"), ("Bob", f"/x/{BOB}")]
    assert html.startswith("<ul><li>La cour<ul><li>Kratéros sert <a")
    assert "<li>--" not in html


def test_bold_in_headings_never_receives_links() -> None:
    html = render("[h2][b]Bob et Arès[/b][/h2][p]Bob[/p]")
    assert "<h3>Bob et Arès</h3>" in html
    assert links_in(html) == [("Bob", f"/x/{BOB}")]


def test_existing_links_headings_and_code_are_untouched() -> None:
    html = render(
        f"[h2]Bob[/h2][p]Voir @[Bob le voleur](person:{new_id()}) et "
        "[url:https://example.com]Kratéros[/url].[/p][code]Arès[/code]"
    )
    assert links_in(html) == []
    assert "<h3>Bob</h3>" in html
    assert "<pre>Arès</pre>" in html


def test_a_mentioned_fiche_is_not_linked_again() -> None:
    source = f"[p]@[le voleur](person:{BOB}) et Bob.[/p]"
    html = str(
        render_bbcode(
            source,
            RenderContext(
                resolve_article=lambda wa_id: f"/x/{wa_id}",
                resolve_article_url=lambda url: None,
                resolve_image=lambda image_id: None,
                player_mode=False,
                matcher=matcher(),
            ),
        )
    )
    assert links_in(html) == []
    assert f'<a class="internal" href="/x/{BOB}" hx-boost="true"' in html


def test_short_names_and_exclusions_are_ignored() -> None:
    assert links_in(render("[p]Ox et Bob.[/p]")) == [("Bob", f"/x/{BOB}")]
    assert links_in(render("[p]Bob et Arès.[/p]", exclusions=("bob",))) == [("Arès", f"/x/{ARES}")]


def test_link_markup_is_sanitised_and_accessible() -> None:
    html = render("[p]<b>Bob</b> & Kratéros[/p]")
    assert "&lt;b&gt;" in html
    assert (
        f'<a class="autolink autolink--personnage" href="/x/{BOB}" '
        'aria-label="Bob, fiche personnage" hx-boost="true"'
    ) in html


def test_plain_text_fields_are_linked_in_one_section() -> None:
    html = str(render_plain_text("Bob part.\n\nBob revient avec Arès.", context(), paragraphs=True))
    assert html.startswith("<p>")
    assert links_in(html) == [("Bob", f"/x/{BOB}"), ("Arès", f"/x/{ARES}")]
    assert render_plain_text("", context()) == ""


def test_tagline_with_a_link_is_still_removed_from_the_body() -> None:
    body = fiche_body(
        f'<p><em>Ami de <a class="autolink" href="/x/{BOB}">Bob</a></em></p><p>Texte</p>', "X"
    )
    assert str(body) == "<p>Texte</p>"


@pytest.fixture
def world(db: Session) -> dict[str, object]:
    dm = make_user(db, "maitre", Role.DM)
    player = make_user(db, "keator")
    main = make_article(
        db,
        "Khaetar Voss",
        content="[p]Khaetar Voss a croisé Sophie, Octave et Maelys au Temple Blanc.[/p]",
    )
    sophie = make_article(db, "Sophie", content="[p][i]La dame du quartier[/i][/p]")
    octave = make_article(
        db, "Octave", content="[p][i]Un archiviste secret[/i][/p]", player_visible=False
    )
    temple = make_article(db, "Le Temple Blanc", template_type="landmark")
    character = make_character(db, "Khaetar", player, main, extras=[sophie, octave])
    db.add(CharacterProfile(character_id=character.id, aliases=[{"name": "Maelys", "note": "x"}]))
    db.commit()
    return {
        "dm": dm,
        "player": player,
        "main": main,
        "sophie": sophie,
        "octave": octave,
        "temple": temple,
        "character": character,
    }


def test_player_fiche_links_only_to_accessible_fiches(
    client: TestClient, world: dict[str, WaArticle]
) -> None:
    login(client, "keator")
    html = client.get(f"/joueur/articles/{world['main'].wa_id}").text
    found = links_in(html)
    assert ("Sophie", f"/joueur/articles/{world['sophie'].wa_id}") in found
    assert all("Octave" not in text and "Temple" not in text for text, _ in found)
    assert all("Khaetar" not in text for text, _ in found)
    assert "fiche personnage" in html
    assert "Octave, fiche" not in html


def test_dm_sees_every_link_but_never_a_self_link(
    client: TestClient, world: dict[str, WaArticle]
) -> None:
    login(client, "maitre")
    html = client.get(f"/mj/articles/{world['main'].wa_id}").text
    found = links_in(html)
    assert ("Octave", f"/mj/articles/{world['octave'].wa_id}") in found
    assert ("Temple Blanc", f"/mj/articles/{world['temple'].wa_id}") in found
    assert all("Khaetar" not in text for text, _ in found)


def test_maelys_is_never_linked(
    client: TestClient, db: Session, world: dict[str, WaArticle]
) -> None:
    db.add(LinkAlias(alias="Maelys", wa_article_id=world["sophie"].wa_id))
    db.commit()
    for user, base in (("maitre", "/mj"), ("keator", "/joueur")):
        login(client, user)
        html = client.get(f"{base}/articles/{world['main'].wa_id}").text
        assert "Maelys" in html
        assert all(text != "Maelys" for text, _ in links_in(html))
    login(client, "maitre")
    response = post_form(
        client, "/mj/liens/alias", {"alias": "maelys", "article": "Sophie"}, "/mj/liens"
    )
    assert response.status_code == 303
    assert db.scalars(select(LinkAlias.alias)).all() == ["Maelys"]


def test_player_preview_index_never_contains_private_titles(
    client: TestClient, world: dict[str, WaArticle]
) -> None:
    login(client, "keator")
    response = client.get("/joueur/liens/apercus")
    assert response.status_code == 200
    data = response.json()["fiches"]
    assert set(data) == {world["main"].wa_id, world["sophie"].wa_id}
    assert data[world["sophie"].wa_id] == {
        "excerpt": "La dame du quartier",
        "kind": "personnage",
        "title": "Sophie",
        "type": "Personnage",
    }
    assert "Octave" not in response.text
    assert "archiviste" not in response.text
    assert "Temple" not in response.text
    cached = client.get(
        "/joueur/liens/apercus", headers={"If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304
    assert client.get("/mj/liens/apercus").status_code in {303, 403, 404}


def test_dm_preview_index_lists_every_fiche(
    client: TestClient, world: dict[str, WaArticle]
) -> None:
    login(client, "maitre")
    data = client.get("/mj/liens/apercus").json()["fiches"]
    assert data[world["octave"].wa_id]["title"] == "Octave"
    assert data[world["temple"].wa_id]["kind"] == "lieu"


def boosted_tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    class Boosted(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.found: list[tuple[str, dict[str, str | None]]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = dict(attrs)
            if "hx-boost" in values:
                self.found.append((tag, values))

    parser = Boosted()
    parser.feed(html)
    return parser.found


def test_boost_applies_to_fiche_links_and_never_to_forms(
    client: TestClient, db: Session, world: dict[str, WaArticle]
) -> None:
    login(client, "maitre")
    html = client.get(f"/mj/articles/{world['main'].wa_id}").text
    assert "<form" in html
    tags = boosted_tags(html)
    assert tags
    assert {tag for tag, _ in tags} == {"a"}
    assert all(values["hx-boost"] == "true" for _, values in tags)
    assert all(values["href"].startswith("/mj/articles/") for _, values in tags)
    assert "?" not in "".join(values["href"] for _, values in tags)


def test_alias_screen_adds_and_removes_names(
    client: TestClient, db: Session, world: dict[str, WaArticle]
) -> None:
    login(client, "maitre")
    page = "/mj/liens"
    assert client.get(page).status_code == 200
    response = post_form(
        client, "/mj/liens/alias", {"alias": "Khaetar", "article": "Khaetar Voss"}, page
    )
    assert response.status_code == 303
    sophie = world["sophie"].wa_id
    text = "[p]Khaetar et la Dame.[/p]"
    assert (
        links_in(str(render_bbcode(text, dm_render_context(db, current=sophie))))[0][0] == "Khaetar"
    )
    post_form(client, "/mj/liens/exclusions", {"term": "Khaetar"}, page)
    assert links_in(str(render_bbcode(text, dm_render_context(db, current=sophie)))) == []
    exclusion = db.scalar(select(LinkExclusion))
    post_form(client, f"/mj/liens/exclusions/{exclusion.id}/supprimer", {}, page)
    alias = db.scalar(select(LinkAlias))
    post_form(client, f"/mj/liens/alias/{alias.id}/supprimer", {}, page)
    db.expire_all()
    assert db.scalars(select(LinkAlias)).all() == []
    assert db.scalars(select(LinkExclusion)).all() == []
    assert links_in(str(render_bbcode(text, dm_render_context(db, current=sophie)))) == []
    login(client, "keator")
    assert client.get(page).status_code in {303, 403, 404}


def test_name_conflicts_are_logged_and_listed(
    client: TestClient,
    db: Session,
    world: dict[str, WaArticle],
    caplog: pytest.LogCaptureFixture,
) -> None:
    make_article(db, "Sophie", template_type="landmark")
    with caplog.at_level(logging.WARNING, logger="app.linking"):
        linking.refresh(db)
    assert any("'sophie'" in record.getMessage() for record in caplog.records)
    login(client, "maitre")
    html = client.get("/mj/liens").text
    assert "Conflits de noms" in html
    assert html.count(">Sophie</a>") == 2


def test_index_follows_new_fiches(db: Session, world: dict[str, WaArticle]) -> None:
    player = world["player"]
    text = "[p]Le Gage d'Arès brille.[/p]"
    assert links_in(str(render_bbcode(text, dm_render_context(db)))) == []
    gage = make_article(db, "Le Gage d'Arès", template_type="item")
    assert links_in(str(render_bbcode(text, dm_render_context(db)))) == [
        ("Le Gage d'Arès", f"/mj/articles/{gage.wa_id}")
    ]
    assert links_in(str(render_bbcode(text, player_render_context(db, player)))) == []
    assert "autolink--objet" in str(render_bbcode(text, dm_render_context(db)))


def test_chronology_summaries_are_linked_for_players(
    client: TestClient, db: Session, world: dict[str, WaArticle]
) -> None:
    event = TimelineEvent(
        code="T1",
        title="Rencontre",
        summary="Sophie croise Octave.",
        start_year=829,
        importance="notable",
        origin="canon",
        visibility="joueurs",
    )
    db.add(event)
    db.commit()
    login(client, "keator")
    html = client.get(f"/joueur/chronologie/evenements/{event.id}").text
    assert links_in(html) == [("Sophie", f"/joueur/articles/{world['sophie'].wa_id}")]
    assert "Octave" in html


def test_preview_json_is_valid_utf8(client: TestClient, world: dict[str, WaArticle]) -> None:
    login(client, "maitre")
    body = client.get("/mj/liens/apercus").content
    assert json.loads(body.decode("utf-8"))["fiches"]


@pytest.fixture
def config() -> Config:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    return config


def test_migration_prefills_the_dm_aliases(db: Session, config: Config) -> None:
    khaetar = make_article(db, "Khaetar Voss")
    gronk = make_article(db, "Gronk, fils de Brok")
    city = make_article(
        db, "1. KHÜCHNIIN KHÖNDII La Vallée de la Volonté", template_type="settlement"
    )
    make_article(db, "Zilvra")
    db.rollback()
    try:
        command.downgrade(config, "0013")
        command.upgrade(config, "head")
    finally:
        command.upgrade(config, "head")
    rows = {(row.alias, row.wa_article_id) for row in db.scalars(select(LinkAlias))}
    assert rows == {
        ("Khaetar", khaetar.wa_id),
        ("Gronk", gronk.wa_id),
        ("Khüchniin Khöndii", city.wa_id),
    }
    assert db.scalars(select(LinkExclusion.term)).all() == ["Süüdriin shönö"]
