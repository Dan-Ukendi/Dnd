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


def test_double_dash_lines_become_nested_lists(player: RenderContext) -> None:
    html = render_bbcode(
        "[ul]\n- [b]Spécialité[/b] : Droit\n- [b]Monuments[/b] :\n-- L'Académie\n"
        "-- Le Théâtre\n--- La scène\n- [b]Particularité[/b] : débats\n[/ul]",
        player,
    )
    assert html == (
        "<ul><li><strong>Spécialité</strong> : Droit</li>"
        "<li><strong>Monuments</strong> :<ul><li>L'Académie</li>"
        "<li>Le Théâtre<ul><li>La scène</li></ul></li></ul></li>"
        "<li><strong>Particularité</strong> : débats</li></ul>"
    )
    assert "--" not in html


def test_double_dash_lines_nest_in_ordered_lists(player: RenderContext) -> None:
    html = render_bbcode(
        "[ol]\n- Le Rex (1)\n- Les Duces (4) :\n-- Dux Hastae\n-- Dux Legis\n---- Trop profond\n"
        "- Les Comites (50)\n[/ol]",
        player,
    )
    assert html == (
        "<ol><li>Le Rex (1)</li><li>Les Duces (4) :<ol><li>Dux Hastae</li>"
        "<li>Dux Legis<ol><li>Trop profond</li></ol></li></ol></li>"
        "<li>Les Comites (50)</li></ol>"
    )


def test_nested_list_without_parent_starts_at_the_first_level(player: RenderContext) -> None:
    html = render_bbcode("[ul]\n-- orphelin\n- suite\n[/ul][ul]\n\n[/ul]", player)
    assert html == "<ul><li>orphelin</li><li>suite</li></ul>"


def test_nested_list_items_stay_sanitised_and_hide_secrets(
    dm: RenderContext, player: RenderContext
) -> None:
    source = (
        "[ul]\n- Parent\n-- <script>alert(1)</script> [secret]Le traître[/secret]\n"
        f"-- [url:javascript:alert(1)]clic[/url] @[Taverne](location:{KNOWN})\n[/ul]"
    )
    html = render_bbcode(source, player)
    assert "<script" not in html and "&lt;script&gt;" in html
    assert "javascript" not in html
    assert "Le traître" not in html
    assert f'<a class="internal" href="/x/{KNOWN}"' in html
    assert html.startswith("<ul><li>Parent<ul><li>")
    assert "Le traître" in render_bbcode(source, dm)


def test_headings_drop_redundant_bold_and_empty_paragraphs_vanish(player: RenderContext) -> None:
    html = render_bbcode(
        "[h2|x][b]Le Principe[/b][/h2]\n[h3][b]1. KHAN (Pluriel : [/b][i][b]Khanlar[/b][/i]"
        "[b]) | ~5 titres[/b][/h3]\n[p][b]Gras[/b] conservé[/p][p][br][/p][p] [/p]\n\n[br]\n",
        player,
    )
    assert "<h3>Le Principe</h3>" in html
    assert "<h4>1. KHAN (Pluriel : <em>Khanlar</em>) | ~5 titres</h4>" in html
    assert "<strong>Gras</strong> conservé" in html
    assert "<p><br></p>" not in html and "<p></p>" not in html
    assert html.count("<p>") == 1


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
    nested = to_plain_text("[ul]\n- parent\n-- enfant\n[/ul]")
    assert nested.splitlines() == ["parent", "enfant"]


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


def test_tables_render_with_header_and_scroll_wrapper(player: RenderContext) -> None:
    html = render_bbcode(
        "[table]\n[tr][th]Nom[/th][th]Toucher[/th][/tr]\n"
        "[tr][td][b]Trait de feu[/b][/td][td]+5[/td][/tr]\n"
        "[tr][th]Total[/th][td]2[/td][/tr]\n[/table]",
        player,
    )
    assert html.startswith('<div class="table-scroll"><table>')
    assert '<thead><tr><th scope="col">Nom</th><th scope="col">Toucher</th></tr></thead>' in html
    assert "<td><strong>Trait de feu</strong></td><td>+5</td>" in html
    assert '<th scope="row">Total</th><td>2</td>' in html
    assert html.endswith("</tbody></table></div>")


def test_tables_close_unterminated_cells(player: RenderContext) -> None:
    html = render_bbcode("[table][tr][td]a[td]b[tr][td]c[/table]", player)
    assert "<tbody><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></tbody>" in html


def test_tables_are_sanitised(player: RenderContext, dm: RenderContext) -> None:
    source = (
        "[table][tr][td]<script>alert(1)</script>[/td][td][foo]x[/foo][/td]"
        f"[td]@[Lieu](location:{UNKNOWN})[/td][td][secret]caché[/secret][/td][/tr][/table]"
    )
    html = render_bbcode(source, player)
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "[foo]x[/foo]" in html
    assert "<a" not in html
    assert "caché" not in html
    assert "caché" in render_bbcode(source, dm)


def test_cell_tags_outside_tables_render_as_text_content(player: RenderContext) -> None:
    html = render_bbcode("[p]avant [td]cellule[/td] après[/p]", player)
    assert html == "<p>avant cellule après</p>"


def test_ability_table_becomes_stat_block(player: RenderContext) -> None:
    rows = "".join(
        f"[tr][td]{name}[/td][td]10[/td][td]+0[/td][/tr]"
        for name in ("Force", "Dextérité", "Constitution", "Intelligence", "Sagesse", "Charisme")
    )
    html = render_bbcode(
        f"[table][tr][th]Caractéristique[/th][th]Valeur[/th][th]Mod.[/th][/tr]{rows}[/table]",
        player,
    )
    assert '<table class="stat-block">' in html
    assert '<td data-label="Caractéristique">Force</td><td data-label="Valeur">10</td>' in html
    horizontal = render_bbcode(
        "[table][tr][th]FOR[/th][th]DEX[/th][th]CON[/th][th]INT[/th][th]SAG[/th][th]CHA[/th][/tr]"
        "[tr][td]8[/td][td]14[/td][td]12[/td][td]10[/td][td]13[/td][td]16[/td][/tr][/table]",
        player,
    )
    assert '<table class="stat-row">' in horizontal
    plain = render_bbcode("[table][tr][th]A[/th][/tr][tr][td]1[/td][/tr][/table]", player)
    assert "class=" not in plain.replace('class="table-scroll"', "")


def test_table_plain_text_separates_cells() -> None:
    assert to_plain_text(
        "[table][tr][th]A[/th][th]B[/th][/tr][tr][td]1[/td][td]2[/td][/tr][/table]"
    ) == ("A B \n1 2")
