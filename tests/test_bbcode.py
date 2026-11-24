import pytest

from app.worldanvil.bbcode import (
    ImageRef,
    RenderContext,
    extract_image_ids,
    extract_links,
    render_bbcode,
    restricted_tags_in,
    to_plain_text,
)

KNOWN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UNKNOWN = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
WA_URL = "https://www.worldanvil.com/w/monde/a/la-taverne-landmark"


def make_context(*, player_mode: bool) -> RenderContext:
    images = {
        12: ImageRef("https://www.worldanvil.com/uploads/images/12.png", "Carte"),
        13: ImageRef("https://evil.example.com/13.png", "Piège"),
    }
    return RenderContext(
        resolve_article=lambda wa_id: f"/x/{wa_id}" if wa_id == KNOWN else None,
        resolve_article_url=lambda url: (
            f"/x/{KNOWN}" if url == "www.worldanvil.com/w/monde/a/la-taverne-landmark" else None
        ),
        resolve_image=images.get,
        player_mode=player_mode,
    )


@pytest.fixture
def dm() -> RenderContext:
    return make_context(player_mode=False)


@pytest.fixture
def player() -> RenderContext:
    return make_context(player_mode=True)


def test_headings_and_inline_formatting(player: RenderContext) -> None:
    html = render_bbcode(
        "[h|abc]Titre[/h]\n[h1|def]Section[/h1]\n[h2]Sous-section[/h2]\n"
        "[p][b]gras[/b], [i]italique[/i], [u]souligné[/u], [s]barré[/s][/p]",
        player,
    )
    assert "<h2>Titre</h2>" in html
    assert "<h2>Section</h2>" in html
    assert "<h3>Sous-section</h3>" in html
    assert "<strong>gras</strong>" in html
    assert "<em>italique</em>" in html
    assert "<u>souligné</u>" in html
    assert "<s>barré</s>" in html


def test_plutarch_dash_lists(player: RenderContext) -> None:
    html = render_bbcode(
        "[ul]\n- [b]Race :[/b] halfelin\n- [b]Âge :[/b] la trentaine\n[/ul]\n"
        "[ol]\n- Premier\n- Second\n[/ol]",
        player,
    )
    assert (
        "<ul><li><strong>Race :</strong> halfelin</li>"
        "<li><strong>Âge :</strong> la trentaine</li></ul>"
    ) in html
    assert "<ol><li>Premier</li><li>Second</li></ol>" in html


def test_classic_list_items(player: RenderContext) -> None:
    html = render_bbcode("[ul][li]un[/li][li]deux[/li][/ul][ul][*]trois[*]quatre[/ul]", player)
    assert "<li>un</li><li>deux</li>" in html
    assert "<li>trois</li><li>quatre</li>" in html


def test_bare_text_becomes_paragraphs(player: RenderContext) -> None:
    html = render_bbcode("Ligne une\nligne deux\n\nParagraphe deux", player)
    assert "<p>Ligne une<br>ligne deux</p>" in html
    assert "<p>Paragraphe deux</p>" in html


def test_mentions_link_only_to_resolvable_articles(player: RenderContext) -> None:
    html = render_bbcode(f"[p]@[Connu](person:{KNOWN}) et @[Inconnu](person:{UNKNOWN})[/p]", player)
    assert f'<a class="internal" href="/x/{KNOWN}"' in html
    assert ">Connu</a>" in html
    assert "Inconnu" in html
    assert UNKNOWN not in html


def test_world_anvil_url_links(dm: RenderContext, player: RenderContext) -> None:
    source = (
        f"[url:{WA_URL}]la taverne[/url] "
        "[url:https://www.worldanvil.com/w/monde/a/secret]le secret[/url]"
    )
    player_html = render_bbcode(source, player)
    assert f'href="/x/{KNOWN}"' in player_html
    assert "le secret" in player_html
    assert "/a/secret" not in player_html
    dm_html = render_bbcode(source, dm)
    assert 'href="https://www.worldanvil.com/w/monde/a/secret"' in dm_html


def test_external_links_are_hardened(player: RenderContext) -> None:
    html = render_bbcode(
        "[url=https://example.com/page]site[/url] [url]https://example.org[/url]", player
    )
    assert 'href="https://example.com/page"' in html
    assert 'href="https://example.org"' in html
    assert 'rel="noopener noreferrer nofollow"' in html


@pytest.mark.parametrize(
    "source",
    [
        "[url:javascript:alert(1)]clic[/url]",
        "[url=JaVaScRiPt:alert(1)]clic[/url]",
        "[url:data:text/html;base64,PHNjcmlwdD4=]clic[/url]",
        "[url]javascript:alert(1)[/url]",
    ],
)
def test_dangerous_link_schemes_are_dropped(player: RenderContext, source: str) -> None:
    html = render_bbcode(source, player)
    assert "<a" not in html


def test_attribute_injection_is_neutralised(player: RenderContext) -> None:
    html = render_bbcode('[url:https://example.com/" onmouseover="alert(1)]x[/url]', player)
    assert ' onmouseover="' not in html
    assert "<a " in html


def test_html_in_source_is_escaped(player: RenderContext) -> None:
    html = render_bbcode('[p]<script>alert("x")</script><img src=x onerror=alert(1)>[/p]', player)
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_unknown_tags_are_displayed_as_text(player: RenderContext) -> None:
    html = render_bbcode("[p][foo:bar]contenu[/foo] [/zzz][/p]", player)
    assert "[foo:bar]contenu[/foo]" in html
    assert "[/zzz]" in html


def test_unclosed_tags_produce_valid_markup(player: RenderContext) -> None:
    html = render_bbcode("[p][b]gras [i]et italique", player)
    assert html.count("<strong>") == html.count("</strong>")
    assert html.count("<em>") == html.count("</em>")


def test_images_from_world_anvil_only(dm: RenderContext, player: RenderContext) -> None:
    html = render_bbcode("[img:12|left|300] [img:13] [img:99]", player)
    assert 'src="https://www.worldanvil.com/uploads/images/12.png"' in html
    assert 'alt="Carte"' in html
    assert "evil.example.com" not in html
    assert "[image]" not in html
    assert "[image]" in render_bbcode("[img:99]", dm)


def test_restricted_content_is_removed_for_players(
    dm: RenderContext, player: RenderContext
) -> None:
    source = (
        "[p]Public.[/p][subcontainer:group-1][p]Réservé aux abonnés.[/p][/subcontainer]"
        "[secret:abc-123][p]Fin.[/p]"
    )
    player_html = render_bbcode(source, player)
    assert "Public." in player_html
    assert "Fin." in player_html
    assert "Réservé" not in player_html
    assert "secret" not in player_html
    dm_html = render_bbcode(source, dm)
    assert '<div class="gm-only">' in dm_html
    assert "Réservé aux abonnés." in dm_html
    assert "[secret:abc-123]" in dm_html


def test_unterminated_restricted_block_hides_the_rest_for_players(player: RenderContext) -> None:
    html = render_bbcode("[p]Début[/p][gmnotes][p]Révélation[/p][/gmnotes][p]Suite", player)
    assert "Début" in html
    assert "Révélation" not in html
    html = render_bbcode("[p]Début[/p][subcontainer:x][p]Révélation[/p]", player)
    assert "Révélation" not in html


def test_plain_text_extraction() -> None:
    text = to_plain_text("[h1|x]Titre[/h1]\n[p]Un [b]texte[/b].[/p]\n[ul]\n- point un\n[/ul]")
    assert text.splitlines()[0] == "Titre"
    assert "Un texte." in text
    assert "point un" in text
    assert "[" not in text


def test_link_and_image_extraction() -> None:
    source = f"@[A](person:{KNOWN}) [url:{WA_URL}]B[/url] [url:https://example.com]C[/url] [img:42]"
    links = extract_links(source)
    assert links.article_ids == {KNOWN}
    assert links.world_anvil_urls == {"www.worldanvil.com/w/monde/a/la-taverne-landmark"}
    assert extract_image_ids(source) == {42}
    assert restricted_tags_in("[p]x[/p][secret:1][subcontainer:2]y[/subcontainer]") == {
        "secret",
        "subcontainer",
    }


def test_empty_content(player: RenderContext) -> None:
    assert render_bbcode(None, player) == ""
    assert render_bbcode("", player) == ""
    assert to_plain_text(None) == ""
