import html
import json
import re
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chronology import (
    CONQUEST_END_YEAR,
    CREATION_YEAR,
    CURRENT_YEAR,
    EMPIRE_END_YEAR,
    era_of,
    format_duration,
    format_span,
    format_year,
)
from app.models import (
    Role,
    TimelineEra,
    TimelineEvent,
    TimelineEventFiche,
    TimelineEventLink,
    User,
)
from app.services.chronology import (
    DATASET_PATH,
    ChronologyError,
    Filters,
    build_frise,
    era_views,
    load_dataset,
    load_seed,
    to_views,
    validate_event_data,
)
from tests.factories import bare_numbers, login, make_article, make_user, post_form


def event_row(code: str, title: str, start: int, end: int | None = None, **extra: object) -> dict:
    return {
        "code": code,
        "title": title,
        "start_year": start,
        "end_year": end,
        "importance": "majeur",
        "origin": "canon",
        "place": "Ancien empire",
        "summary": f"Résumé de {title}.",
        "linked": [],
        **extra,
    }


SMALL_DATASET = [
    event_row("B1", "Création de l'Empire", 0, visibility="joueurs", status="etabli"),
    event_row("H1", "Complot des Ombres", 120, linked=["B1"]),
    event_row("B7", "Révolte des Échos", 749, visibility="joueurs", linked=["H1"]),
    event_row(
        "S1",
        "Dispute pour l'amulette",
        829,
        visibility="joueurs",
        chronicle=True,
        session=1,
        day="J1",
    ),
    event_row("S2", "Le secret du troll", 829, chronicle=True, session=1, day="J2"),
    event_row("A4", "Long sommeil du Joug", -485, 829, importance="notable"),
]


def seed(db: Session) -> dict[str, TimelineEvent]:
    load_seed(db, [validate_event_data(row) for row in SMALL_DATASET])
    return {event.code: event for event in db.scalars(select(TimelineEvent)).all()}


@pytest.mark.parametrize(
    ("year", "slug"),
    [
        (-1237, "aubes-anciennes"),
        (-501, "aubes-anciennes"),
        (-500, "siecles-oublies"),
        (-1, "siecles-oublies"),
        (0, "ascension"),
        (345, "ascension"),
        (346, "grand-midi"),
        (748, "grand-midi"),
        (749, "tumultes"),
        (829, "tumultes"),
    ],
)
def test_era_boundaries(year: int, slug: str) -> None:
    assert era_of(year).slug == slug


def test_calendar_constants() -> None:
    assert (CURRENT_YEAR, CREATION_YEAR, CONQUEST_END_YEAR, EMPIRE_END_YEAR) == (829, 0, 345, 749)


def test_year_display() -> None:
    assert format_year(-523) == "523 av. É."
    assert format_year(0) == "An 0"
    assert format_year(345) == "An 345"
    assert format_span(-4, -1) == "4 av. É. – 1 av. É."
    assert format_span(829, 829) == "An 829"
    assert format_duration(61, 74) == "14 ans"
    assert format_duration(-1, 0) == "2 ans"
    assert format_duration(345, None) == "1 an"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"end_year": -10, "start_year": 5}, "end_before_start"),
        ({"title": "  "}, "missing_title"),
        ({"start_year": None}, "missing_start_year"),
        ({"start_year": "an 5"}, "invalid_year"),
        ({"importance": "capital"}, "invalid_importance"),
        ({"visibility": "public"}, "invalid_visibility"),
        ({"status": "valide"}, "invalid_status"),
        ({"day": "J3"}, "invalid_day"),
        ({"order": "deux"}, "invalid_order"),
        ({"session": 0}, "invalid_session"),
    ],
)
def test_event_validation_rejects_invalid_data(changes: dict, code: str) -> None:
    with pytest.raises(ChronologyError) as error:
        validate_event_data({**event_row("X1", "Titre", 0), **changes})
    assert error.value.code == code


def test_event_validation_accepts_string_years() -> None:
    data = validate_event_data(event_row("X1", "Titre", "-523", "0"))
    assert (data.start_year, data.end_year) == (-523, 0)


def test_dataset_is_coherent() -> None:
    events = load_dataset()
    assert len(events) == 56
    codes = {event.code for event in events}
    assert len(codes) == 56
    assert not any(re.fullmatch(r"E-\d+", code) for code in codes)
    for event in events:
        assert -523 <= event.start_year <= 829, event.code
        assert event.end_year is None or event.start_year <= event.end_year <= 829, event.code
        assert set(event.linked) <= codes, event.code
        assert event.wa_history_id, event.code
    assert len({event.wa_history_id for event in events}) == 56
    assert next(event for event in events if event.code == "B8").linked == ("B7",)
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    orders = [row["order"] for row in raw]
    assert orders == sorted(set(orders))
    chronicle = [event for event in events if event.chronicle]
    assert {event.code for event in chronicle} == {f"S{number}" for number in range(1, 21)}
    assert all(event.session == 1 and event.day in ("J1", "J2") for event in chronicle)


def test_initial_load_creates_every_event_and_is_idempotent(db: Session) -> None:
    first = load_seed(db)
    assert (first.created, first.updated) == (56, 0)
    assert db.scalar(select(func.count()).select_from(TimelineEvent)) == 56
    expected_links = sum(len(event.linked) for event in load_dataset())
    assert db.scalar(select(func.count()).select_from(TimelineEventLink)) == expected_links
    second = load_seed(db)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 56)
    assert db.scalar(select(func.count()).select_from(TimelineEvent)) == 56
    assert db.scalar(select(func.count()).select_from(TimelineEventLink)) == expected_links


def test_load_never_touches_rows_modified_by_the_dm(db: Session) -> None:
    load_seed(db)
    event = db.scalar(select(TimelineEvent).where(TimelineEvent.code == "B1"))
    event.title = "Titre retouché"
    event.visibility = "joueurs"
    event.modified_by_dm = True
    other = db.scalar(select(TimelineEvent).where(TimelineEvent.code == "B6"))
    other.title = "Titre perdu"
    db.commit()
    report = load_seed(db)
    assert report.skipped == 1
    assert report.updated == 1
    db.expire_all()
    kept = db.scalar(select(TimelineEvent).where(TimelineEvent.code == "B1"))
    assert (kept.title, kept.visibility) == ("Titre retouché", "joueurs")
    restored = db.scalar(select(TimelineEvent).where(TimelineEvent.code == "B6"))
    assert restored.title != "Titre perdu"


def test_frise_layout_clusters_same_year_events_and_keeps_bands_readable(db: Session) -> None:
    load_seed(db)
    eras = era_views(db)
    events = db.scalars(select(TimelineEvent)).all()
    views = to_views(events, eras)
    frise = build_frise(views, views, eras)
    assert [band.era.slug for band in frise.bands] == [era.slug for era in eras]
    assert all(band.width >= 300 for band in frise.bands)
    clusters = [marker for marker in frise.points if marker.kind == "cluster"]
    assert any(marker.year == 829 and len(marker.events) >= 12 for marker in clusters)
    sleep = next(marker for marker in frise.ranges if marker.first.event.code == "A4")
    assert sleep.x < frise.bands[1].x + frise.bands[1].width < sleep.x + sleep.width
    xs = [mark.x for mark in frise.milestones]
    assert xs == sorted(xs)


def test_frise_clusters_show_no_bare_count(client: TestClient, db: Session) -> None:
    load_seed(db)
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    page = client.get("/mj/chronologie").text
    assert 'class="frise__cluster' in page
    assert re.search(r'frise__tip-years">An 829</span>\d+ événements</span>', page)
    assert bare_numbers(page) == []


@dataclass
class Chrono:
    dm: User
    player: User
    events: dict[str, TimelineEvent]


@pytest.fixture
def chrono(db: Session) -> Chrono:
    dm = make_user(db, "maitre", Role.DM)
    player = make_user(db, "alice")
    return Chrono(dm, player, seed(db))


def event_id(chrono: Chrono, code: str) -> int:
    return chrono.events[code].id


def player_pages(chrono: Chrono) -> list[str]:
    return ["/joueur/chronologie", f"/joueur/chronologie/evenements/{event_id(chrono, 'B1')}"]


def dm_pages(chrono: Chrono) -> list[str]:
    return [
        "/mj/chronologie",
        "/mj/chronologie?vue=chronique",
        f"/mj/chronologie?evenement={event_id(chrono, 'H1')}",
        "/mj/chronologie?annee=829",
        f"/mj/chronologie/evenements/{event_id(chrono, 'H1')}",
        "/mj/chronologie/eres",
    ]


def dm_actions(chrono: Chrono) -> list[tuple[str, dict[str, str]]]:
    hidden = event_id(chrono, "H1")
    return [
        (f"/mj/chronologie/evenements/{hidden}/statut", {"status": "etabli"}),
        (f"/mj/chronologie/evenements/{hidden}/visibilite", {"visibility": "joueurs"}),
        (
            f"/mj/chronologie/evenements/{hidden}/chronique",
            {"chronicle": "1", "session": "2", "day": "J2", "order": "7"},
        ),
        (
            f"/mj/chronologie/evenements/{hidden}/liens",
            {"target_id": str(event_id(chrono, "S2"))},
        ),
        (f"/mj/chronologie/evenements/{hidden}/fiches", {"title": "Le Collège des Ombres"}),
        (
            "/mj/chronologie/eres/tumultes",
            {"name": "Les Temps Brisés", "subtitle": "Trois nations.", "color": "#336655"},
        ),
    ]


def test_anonymous_is_redirected_and_cannot_act(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    for path in player_pages(chrono) + dm_pages(chrono):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion", path
    for path, data in dm_actions(chrono):
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion", path
    db.expire_all()
    hidden = db.get(TimelineEvent, event_id(chrono, "H1"))
    assert (hidden.status, hidden.visibility, hidden.modified_by_dm) == ("a_valider", "mj", False)
    assert db.scalar(select(func.count()).select_from(TimelineEra)) == 0


def test_player_only_receives_revealed_events(client: TestClient, chrono: Chrono) -> None:
    login(client, "alice")
    hidden_titles = ["Complot des Ombres", "Le secret du troll", "Long sommeil du Joug"]
    pages = [
        "/joueur/chronologie",
        "/joueur/chronologie?vue=chronique",
        "/joueur/chronologie?q=ombres",
        "/joueur/chronologie?q=troll",
        "/joueur/chronologie?ere=ascension&importance=majeur",
        "/joueur/chronologie?statut=a_valider&origine=canon",
        "/joueur/chronologie?annee=829",
        f"/joueur/chronologie?evenement={event_id(chrono, 'B1')}",
        f"/joueur/chronologie?evenement={event_id(chrono, 'B7')}",
        f"/joueur/chronologie/evenements/{event_id(chrono, 'B7')}",
    ]
    for path in pages:
        response = client.get(path)
        assert response.status_code == 200, path
        for title in hidden_titles:
            assert title not in html.unescape(response.text), (path, title)
    listing = html.unescape(client.get("/joueur/chronologie").text)
    for title in ("Création de l'Empire", "Révolte des Échos", "Dispute pour l'amulette"):
        assert title in listing
    assert "3 événements" in listing
    assert "Gestion du MJ" not in listing
    chronicle = html.unescape(client.get("/joueur/chronologie?vue=chronique").text)
    assert "Dispute pour l'amulette" in chronicle


def test_player_gets_the_standard_404_for_hidden_events(client: TestClient, chrono: Chrono) -> None:
    login(client, "alice")
    missing = client.get("/joueur/chronologie/evenements/999999")
    assert missing.status_code == 404
    for code in ("H1", "S2", "A4"):
        response = client.get(f"/joueur/chronologie/evenements/{event_id(chrono, code)}")
        assert response.status_code == 404, code
        assert response.text == missing.text
    panel = client.get(
        f"/joueur/chronologie?evenement={event_id(chrono, 'H1')}",
        headers={"HX-Request": "true", "HX-Target": "chrono-panel"},
    )
    assert panel.status_code == 200
    assert "Complot des Ombres" not in panel.text


def test_player_sees_a_sober_empty_state_until_something_is_revealed(
    client: TestClient, db: Session
) -> None:
    make_user(db, "alice")
    load_seed(db, [validate_event_data(event_row("H1", "Complot des Ombres", 120))])
    login(client, "alice")
    page = client.get("/joueur/chronologie").text
    assert "Les annales sont encore scellées" in page
    assert "Complot des Ombres" not in page


def test_player_cannot_use_dm_actions(client: TestClient, chrono: Chrono, db: Session) -> None:
    removable = TimelineEventLink(
        source_id=event_id(chrono, "B1"), target_id=event_id(chrono, "S1"), origin="mj"
    )
    fiche = TimelineEventFiche(event_id=event_id(chrono, "B1"), title="Khaanat")
    db.add_all([removable, fiche])
    db.commit()
    login(client, "alice")
    for path in dm_pages(chrono):
        assert client.get(path).status_code == 404, path
    actions = [
        *dm_actions(chrono),
        (
            f"/mj/chronologie/evenements/{event_id(chrono, 'B1')}/liens/"
            f"{event_id(chrono, 'S1')}/supprimer",
            {},
        ),
        (f"/mj/chronologie/evenements/{event_id(chrono, 'B1')}/fiches/{fiche.id}/supprimer", {}),
        ("/mj/chronologie/eres/tumultes/reinitialiser", {}),
    ]
    for path, data in actions:
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code == 404, path
    db.expire_all()
    hidden = db.get(TimelineEvent, event_id(chrono, "H1"))
    assert (hidden.status, hidden.visibility, hidden.chronicle) == ("a_valider", "mj", False)
    assert not hidden.modified_by_dm
    assert db.scalar(select(func.count()).select_from(TimelineEventLink)) == 3
    assert db.scalar(select(func.count()).select_from(TimelineEventFiche)) == 1
    assert db.scalar(select(func.count()).select_from(TimelineEra)) == 0


def test_dm_can_reach_every_page_and_use_every_action(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    article = make_article(db, "Khaanat", template_type="settlement")
    login(client, "maitre")
    for path in dm_pages(chrono):
        response = client.get(path)
        assert response.status_code == 200, path
        assert bare_numbers(response.text) == [], path
    for path, data in dm_actions(chrono):
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code == 303, path
    hidden_id = event_id(chrono, "H1")
    response = post_form(
        client,
        f"/mj/chronologie/evenements/{hidden_id}/fiches",
        {"article_id": article.wa_id},
        "/connexion/mj",
    )
    assert response.status_code == 303
    db.expire_all()
    hidden = db.get(TimelineEvent, hidden_id)
    assert (hidden.status, hidden.visibility) == ("etabli", "joueurs")
    assert (hidden.chronicle, hidden.session, hidden.day, hidden.sort_order) == (True, 2, "J2", 7)
    assert hidden.modified_by_dm
    titles = set(
        db.scalars(
            select(TimelineEventFiche.title).where(TimelineEventFiche.event_id == hidden_id)
        ).all()
    )
    assert titles == {"Le Collège des Ombres", "Khaanat"}
    era = db.get(TimelineEra, "tumultes")
    assert (era.name, era.color) == ("Les Temps Brisés", "#336655")
    page = client.get(f"/mj/chronologie/evenements/{hidden_id}").text
    assert "Le secret du troll" in page
    assert f'href="/mj/articles/{article.wa_id}"' in page
    assert "Modifier sur World Anvil" not in page
    link_target = event_id(chrono, "S2")
    response = post_form(
        client,
        f"/mj/chronologie/evenements/{hidden_id}/liens/{link_target}/supprimer",
        {},
        "/connexion/mj",
    )
    assert response.status_code == 303
    fiche_ids = db.scalars(
        select(TimelineEventFiche.id).where(TimelineEventFiche.event_id == hidden_id)
    ).all()
    for fiche_id in fiche_ids:
        post_form(
            client,
            f"/mj/chronologie/evenements/{hidden_id}/fiches/{fiche_id}/supprimer",
            {},
            "/connexion/mj",
        )
    post_form(client, "/mj/chronologie/eres/tumultes/reinitialiser", {}, "/connexion/mj")
    db.expire_all()
    assert (
        db.scalar(
            select(func.count())
            .select_from(TimelineEventLink)
            .where(TimelineEventLink.target_id == link_target)
        )
        == 0
    )
    assert db.scalar(select(func.count()).select_from(TimelineEventFiche)) == 0
    assert db.get(TimelineEra, "tumultes") is None


def test_dm_sees_hidden_events_and_world_anvil_link(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    chrono.events["H1"].wa_history_id = "68065f12-ac60-4e14-b568-a3e211b3741a"
    db.commit()
    login(client, "maitre")
    page = client.get(f"/mj/chronologie?evenement={event_id(chrono, 'H1')}").text
    assert "Complot des Ombres" in page
    assert "Long sommeil du Joug" in page
    assert "à valider" in page
    assert (
        "https://www.worldanvil.com/world/timeline/history/"
        "68065f12-ac60-4e14-b568-a3e211b3741a/edit"
    ) in page
    search = client.get("/mj/chronologie?q=joug").text
    assert "1 événement sur 6" in search


def test_world_anvil_links_cannot_be_removed_in_the_app(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    login(client, "maitre")
    source, target = event_id(chrono, "H1"), event_id(chrono, "B1")
    post_form(
        client, f"/mj/chronologie/evenements/{source}/liens/{target}/supprimer", {}, "/connexion/mj"
    )
    assert db.get(TimelineEventLink, (source, target)) is not None


@pytest.mark.parametrize(
    "data",
    [
        {"chronicle": "1", "day": "J3", "order": "1"},
        {"chronicle": "1", "day": "J1", "order": "un"},
        {"chronicle": "1", "session": "-2", "order": "1"},
    ],
)
def test_dm_chronicle_form_is_validated(
    client: TestClient, chrono: Chrono, db: Session, data: dict[str, str]
) -> None:
    login(client, "maitre")
    hidden = event_id(chrono, "H1")
    response = post_form(
        client, f"/mj/chronologie/evenements/{hidden}/chronique", data, "/connexion/mj"
    )
    assert response.status_code == 303
    db.expire_all()
    event = db.get(TimelineEvent, hidden)
    assert (event.chronicle, event.day, event.modified_by_dm) == (False, None, False)


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/statut", {"status": "valide"}),
        ("/visibilite", {"visibility": "tous"}),
        ("/liens", {"target_id": "0"}),
        ("/fiches", {"title": ""}),
    ],
)
def test_dm_actions_reject_invalid_values(
    client: TestClient, chrono: Chrono, db: Session, path: str, data: dict[str, str]
) -> None:
    login(client, "maitre")
    hidden = event_id(chrono, "H1")
    post_form(client, f"/mj/chronologie/evenements/{hidden}{path}", data, "/connexion/mj")
    db.expire_all()
    event = db.get(TimelineEvent, hidden)
    assert (event.status, event.visibility, event.modified_by_dm) == ("a_valider", "mj", False)


def test_era_settings_reject_invalid_colors_and_unknown_eras(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    login(client, "maitre")
    post_form(
        client,
        "/mj/chronologie/eres/ascension",
        {"name": "L'Ascension", "subtitle": "", "color": "red"},
        "/connexion/mj",
    )
    assert db.get(TimelineEra, "ascension") is None
    response = post_form(
        client,
        "/mj/chronologie/eres/inconnue",
        {"name": "X", "subtitle": "", "color": "#000000"},
        "/connexion/mj",
    )
    assert response.status_code == 404


def test_redirect_target_stays_inside_the_chronology(client: TestClient, chrono: Chrono) -> None:
    login(client, "maitre")
    hidden = event_id(chrono, "H1")
    response = post_form(
        client,
        f"/mj/chronologie/evenements/{hidden}/statut",
        {"status": "etabli", "retour": "//evil.example/"},
        "/connexion/mj",
    )
    assert response.headers["location"] == f"/mj/chronologie/evenements/{hidden}"


def test_csrf_is_required_on_chronology_posts(
    client: TestClient, chrono: Chrono, db: Session
) -> None:
    login(client, "maitre")
    hidden = event_id(chrono, "H1")
    response = client.post(
        f"/mj/chronologie/evenements/{hidden}/visibilite", data={"visibility": "joueurs"}
    )
    assert response.status_code == 403
    db.expire_all()
    assert db.get(TimelineEvent, hidden).visibility == "mj"


def test_filters_ignore_unknown_values_and_hide_dm_filters_from_players() -> None:
    params = {"ere": "inconnue", "importance": "majeur", "origine": "canon", "statut": "etabli"}
    assert Filters.parse(params, dm=False) == Filters(importance="majeur")
    assert Filters.parse(params, dm=True) == Filters(
        importance="majeur", origin="canon", status="etabli"
    )
