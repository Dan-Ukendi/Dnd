from app.models import WaArticle
from app.presentation import article_image, article_tagline, fiche_body, in_section
from app.sections import ARTICLE_SECTION_OVERRIDES, section_key


def article(title: str, content: str, template_type: str = "person", **raw: object) -> WaArticle:
    return WaArticle(
        wa_id="00000000-0000-0000-0000-000000000001",
        title=title,
        content=content,
        template_type=template_type,
        raw=raw,
    )


def test_sections_follow_template_types_and_overrides() -> None:
    assert section_key("x", "settlement") == "monde"
    assert section_key("x", "person") == "personnage"
    assert section_key("x", "organization") == "organisation"
    assert section_key("x", "item") == "lore"
    assert section_key("x", None) == "lore"
    override_id, override_section = next(iter(ARTICLE_SECTION_OVERRIDES.items()))
    assert section_key(override_id, "organization") == override_section


def test_in_section_filters_articles() -> None:
    town = article("Ville", "", "settlement")
    hero = article("Héros", "", "person")
    assert in_section([town, hero], "monde") == [town]
    assert in_section([town, hero], "") == [town, hero]


def test_tagline_from_italic_lead() -> None:
    item = article(
        "Bob", "[p][/p]\n[h|x]Bob[/h]\n[p][i]Pickpocket halfelin[/i][/p]\n[h1]Fiche[/h1]"
    )
    assert article_tagline(item) == "Pickpocket halfelin"


def test_tagline_from_title_heading_with_subtitle() -> None:
    nation = article(
        "Le Khaanat", "[h2][/h2][h|y][b]Le Khaanat : Société et Secrets[/b][/h][p]Texte[/p]"
    )
    assert article_tagline(nation) == "Société et Secrets"


def test_tagline_ignores_unrelated_headings() -> None:
    report = article("Session 1", "[h2]Résumé[/h2][p][i]Pas une accroche[/i][/p]")
    assert article_tagline(report) == ""


def test_fiche_body_strips_repeated_title_and_tagline() -> None:
    body = fiche_body("<h2>Le Bob</h2>\n<p><em>Accroche</em></p>\n<h2>Fiche</h2>", "Bob")
    assert str(body) == "<h2>Fiche</h2>"


def test_player_images_require_public_state() -> None:
    url = "https://www.worldanvil.com/uploads/images/1.png"
    public = article(
        "A", "", cover={"id": 1, "url": url, "state": "public", "subscribergroups": []}
    )
    private = article("B", "", cover={"id": 2, "url": url, "state": "private"})
    foreign = article(
        "C", "", cover={"id": 3, "url": "https://evil.example/x.png", "state": "public"}
    )
    default = article("D", "", cover={"id": -1, "url": url, "state": "public"})
    assert article_image(public, True) == url
    assert article_image(private, True) is None
    assert article_image(private, False) == url
    assert article_image(foreign, False) is None
    assert article_image(default, False) is None
