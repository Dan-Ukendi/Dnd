from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Character, Role, SyncRun, User, WaArticle
from app.routes import dm as dm_routes
from tests.factories import (
    csrf_token,
    login,
    make_article,
    make_character,
    make_user,
    post_form,
)


@dataclass
class World:
    dm: User
    alice: User
    bob: User
    alice_main: WaArticle
    alice_extra: WaArticle
    bob_main: WaArticle
    unassigned: WaArticle
    hidden_assigned: WaArticle
    removed_assigned: WaArticle
    alice_character: Character
    bob_character: Character


@pytest.fixture
def world(db: Session) -> World:
    dm = make_user(db, "maitre", Role.DM)
    alice = make_user(db, "alice")
    bob = make_user(db, "bob")
    unassigned = make_article(db, "Le Grand Secret", content="[p]Le traître est le roi.[/p]")
    alice_extra = make_article(db, "La Taverne", template_type="landmark")
    hidden = make_article(db, "Plan du MJ", template_type="plot", player_visible=False)
    removed = make_article(db, "Ancien lieu", removed=True)
    alice_main = make_article(
        db,
        "Alice la voleuse",
        content=(
            f"[p]Elle fréquente @[La Taverne](landmark:{alice_extra.wa_id}) "
            f"et cache @[Le Grand Secret](article:{unassigned.wa_id}).[/p]"
        ),
        linked_ids=sorted([alice_extra.wa_id, unassigned.wa_id]),
    )
    bob_main = make_article(db, "Bob le barde", content="[p]Bob chante.[/p]")
    alice_character = make_character(db, "Alice", alice, alice_main, [alice_extra, hidden, removed])
    bob_character = make_character(db, "Bob", bob, bob_main)
    return World(
        dm,
        alice,
        bob,
        alice_main,
        alice_extra,
        bob_main,
        unassigned,
        hidden,
        removed,
        alice_character,
        bob_character,
    )


def dm_get_routes(world: World) -> list[str]:
    return [
        "/mj",
        "/mj/synchronisation",
        "/mj/articles",
        "/mj/articles?q=traître",
        f"/mj/articles/{world.unassigned.wa_id}",
        "/mj/joueurs",
        "/mj/personnages",
        f"/mj/personnages/{world.alice_character.id}",
    ]


def dm_post_routes(world: World) -> list[tuple[str, dict[str, str]]]:
    character = world.alice_character.id
    return [
        ("/mj/synchroniser", {"mode": "incremental"}),
        ("/mj/joueurs", {"display_name": "Mallory", "login": "mallory"}),
        (f"/mj/joueurs/{world.bob.id}/invitation", {}),
        (f"/mj/joueurs/{world.bob.id}/desactiver", {}),
        (f"/mj/joueurs/{world.bob.id}/reactiver", {}),
        ("/mj/personnages", {"name": "Intrus", "player_id": str(world.alice.id)}),
        (f"/mj/personnages/{character}", {"name": "Renommé", "player_id": str(world.alice.id)}),
        (
            f"/mj/personnages/{character}/articles",
            {"article_id": world.unassigned.wa_id, "shared": "1"},
        ),
        (f"/mj/personnages/{character}/supprimer", {}),
    ]


def player_get_routes(world: World) -> list[str]:
    return ["/joueur", "/joueur/articles", f"/joueur/articles/{world.alice_main.wa_id}"]


@pytest.fixture(autouse=True)
def no_real_sync(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(dm_routes, "run_sync_job", calls.append)
    return calls


def test_anonymous_is_redirected_to_login(client: TestClient, world: World) -> None:
    for path in dm_get_routes(world) + player_get_routes(world):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion", path


def test_anonymous_posts_are_redirected_without_side_effects(
    client: TestClient, world: World, db: Session
) -> None:
    for path, data in dm_post_routes(world):
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code == 303, path
        assert response.headers["location"] == "/connexion", path
    assert db.scalar(select(func.count()).select_from(SyncRun)) == 0
    assert db.scalar(select(User).where(User.login == "mallory")) is None


def test_dm_can_reach_every_dm_page(client: TestClient, world: World) -> None:
    assert login(client, "maitre").status_code == 303
    for path in dm_get_routes(world):
        response = client.get(path)
        assert response.status_code == 200, path


def test_dm_can_use_every_dm_action(
    client: TestClient, world: World, db: Session, no_real_sync: list[int]
) -> None:
    login(client, "maitre")
    for path, data in dm_post_routes(world):
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code in (200, 303), path
    assert len(no_real_sync) == 1
    assert db.scalar(select(User).where(User.login == "mallory")) is not None


def test_dm_cannot_open_player_pages(client: TestClient, world: World) -> None:
    login(client, "maitre")
    for path in player_get_routes(world):
        assert client.get(path).status_code == 404, path


def test_player_gets_404_on_every_dm_page(client: TestClient, world: World) -> None:
    login(client, "alice")
    for path in dm_get_routes(world):
        response = client.get(path)
        assert response.status_code == 404, path
        assert world.unassigned.title not in response.text


def test_player_dm_actions_are_404_without_side_effects(
    client: TestClient, world: World, db: Session, no_real_sync: list[int]
) -> None:
    login(client, "alice")
    for path, data in dm_post_routes(world):
        response = post_form(client, path, data, "/connexion/mj")
        assert response.status_code == 404, path
    assert no_real_sync == []
    assert db.scalar(select(User).where(User.login == "mallory")) is None
    db.expire_all()
    assert db.get(Character, world.alice_character.id) is not None
    assert db.get(User, world.bob.id).is_active


def test_player_sees_own_character_and_shared_articles(client: TestClient, world: World) -> None:
    login(client, "alice")
    home = client.get("/joueur")
    assert home.status_code == 200
    assert "Elle fréquente" in home.text
    listing = client.get("/joueur/articles")
    assert world.alice_extra.title in listing.text
    for hidden in (world.unassigned, world.hidden_assigned, world.removed_assigned, world.bob_main):
        assert hidden.title not in listing.text
    assert client.get(f"/joueur/articles/{world.alice_extra.wa_id}").status_code == 200
    assert client.get(f"/joueur/articles/{world.alice_main.wa_id}").status_code == 200


def test_player_cannot_read_unassigned_or_unsafe_articles(client: TestClient, world: World) -> None:
    login(client, "alice")
    missing = client.get("/joueur/articles/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    for article in (
        world.unassigned,
        world.hidden_assigned,
        world.removed_assigned,
        world.bob_main,
    ):
        response = client.get(f"/joueur/articles/{article.wa_id}")
        assert response.status_code == 404, article.title
        assert response.text == missing.text
        assert article.title not in response.text


def test_cross_player_access_is_denied_both_ways(
    client: TestClient, other_client: TestClient, world: World
) -> None:
    login(client, "alice")
    login(other_client, "bob")
    assert client.get(f"/joueur/articles/{world.bob_main.wa_id}").status_code == 404
    assert other_client.get(f"/joueur/articles/{world.alice_main.wa_id}").status_code == 404
    assert other_client.get(f"/joueur/articles/{world.alice_extra.wa_id}").status_code == 404
    bob_home = other_client.get("/joueur")
    assert "Bob chante." in bob_home.text
    assert "Elle fréquente" not in bob_home.text


def test_player_links_only_point_to_accessible_articles(client: TestClient, world: World) -> None:
    login(client, "alice")
    page = client.get(f"/joueur/articles/{world.alice_main.wa_id}").text
    assert f'href="/joueur/articles/{world.alice_extra.wa_id}"' in page
    assert world.unassigned.wa_id not in page
    assert "Le Grand Secret" in page


def test_dm_links_point_to_every_known_article(client: TestClient, world: World) -> None:
    login(client, "maitre")
    page = client.get(f"/mj/articles/{world.alice_main.wa_id}").text
    assert f'href="/mj/articles/{world.unassigned.wa_id}"' in page
    assert f'href="/mj/articles/{world.alice_extra.wa_id}"' in page


def test_deactivated_player_loses_session_and_cannot_log_in(
    client: TestClient, other_client: TestClient, world: World
) -> None:
    login(client, "alice")
    assert client.get("/joueur").status_code == 200
    login(other_client, "maitre")
    post_form(other_client, f"/mj/joueurs/{world.alice.id}/desactiver", {}, "/connexion/mj")
    response = client.get("/joueur", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/connexion"
    assert login(client, "alice").status_code == 400


def test_csrf_is_required_on_posts(client: TestClient, world: World, db: Session) -> None:
    login(client, "maitre")
    missing = client.post("/mj/joueurs", data={"display_name": "X", "login": "xavier"})
    assert missing.status_code == 403
    wrong = client.post(
        "/mj/joueurs", data={"display_name": "X", "login": "xavier", "csrf_token": "forged"}
    )
    assert wrong.status_code == 403
    login_without_token = client.post("/connexion", data={"login": "maitre", "password": "x"})
    assert login_without_token.status_code == 403
    assert db.scalar(select(User).where(User.login == "xavier")) is None


def test_login_attempts_are_rate_limited(client: TestClient, world: World) -> None:
    for _ in range(5):
        assert login(client, "alice", "mauvais-mot-de-passe").status_code == 400
    blocked = login(client, "alice")
    assert blocked.status_code == 429
    assert "Trop de tentatives" in blocked.text


def test_login_error_does_not_reveal_whether_account_exists(
    client: TestClient, world: World
) -> None:
    unknown = login(client, "personne", "mauvais-mot-de-passe")
    wrong = login(client, "alice", "mauvais-mot-de-passe")
    assert unknown.status_code == wrong.status_code == 400
    assert "Identifiant ou mot de passe incorrect." in unknown.text
    assert "Identifiant ou mot de passe incorrect." in wrong.text


def test_session_cookie_flags_and_security_headers(client: TestClient, world: World) -> None:
    response = login(client, "alice")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    page = client.get("/joueur")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-frame-options"] == "DENY"


def test_csrf_token_is_bound_to_the_session(
    client: TestClient, other_client: TestClient, world: World
) -> None:
    login(client, "maitre")
    foreign_token = csrf_token(other_client, "/connexion/mj")
    response = client.post(
        "/mj/joueurs",
        data={"display_name": "X", "login": "xavier", "csrf_token": foreign_token},
    )
    assert response.status_code == 403
