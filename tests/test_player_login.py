from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import Role, User
from app.security import verify_password
from app.services.accounts import set_temporary_password
from tests.factories import csrf_token, login, make_article, make_character, make_user, post_form

TEMPORARY = "0000"
NEW_PASSWORD = "un-nouveau-secret-solide"


def temporary_player(db: Session, login_name: str) -> User:
    player = make_user(db, login_name, password=None)
    set_temporary_password(db, player, TEMPORARY)
    return player


def player_login(client: TestClient, login_name: str, password: str) -> Response:
    return client.post(
        "/connexion",
        data={
            "login": login_name,
            "password": password,
            "mode": "joueur",
            "csrf_token": csrf_token(client, f"/connexion/joueurs/{login_name}"),
        },
        follow_redirects=False,
    )


def change(client: TestClient, current: str, new: str, confirmation: str | None = None) -> Response:
    return client.post(
        "/joueur/mot-de-passe",
        data={
            "current_password": current,
            "new_password": new,
            "new_password_confirmation": new if confirmation is None else confirmation,
            "csrf_token": csrf_token(client, "/joueur/mot-de-passe"),
        },
        follow_redirects=False,
    )


def test_landing_page_offers_both_roles(client: TestClient) -> None:
    page = client.get("/connexion")
    assert page.status_code == 200
    assert 'href="/connexion/joueurs"' in page.text
    assert 'href="/connexion/mj"' in page.text
    assert 'name="password"' not in page.text


def test_player_list_shows_only_players_who_can_log_in(client: TestClient, db: Session) -> None:
    make_user(db, "maitre", Role.DM)
    zilvra = temporary_player(db, "zilvra")
    make_character(db, "Zilvra Vhaelor", zilvra, make_article(db, "Zilvra"))
    make_user(db, "endormi", active=False)
    make_user(db, "invite", password=None)
    page = client.get("/connexion/joueurs").text
    assert 'href="/connexion/joueurs/zilvra"' in page
    assert "Zilvra Vhaelor" in page
    for hidden in ("maitre", "endormi", "invite"):
        assert f"/connexion/joueurs/{hidden}" not in page
        assert client.get(f"/connexion/joueurs/{hidden}").status_code == 404
    assert client.get("/connexion/joueurs/personne").status_code == 404


def test_temporary_password_forces_a_new_password(client: TestClient, db: Session) -> None:
    temporary_player(db, "keator")
    response = player_login(client, "keator", TEMPORARY)
    assert response.status_code == 303
    assert response.headers["location"] == "/joueur/mot-de-passe"
    for path in ("/joueur", "/joueur/articles"):
        blocked = client.get(path, follow_redirects=False)
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/joueur/mot-de-passe"
    htmx = client.get("/joueur/articles", headers={"HX-Request": "true"}, follow_redirects=False)
    assert htmx.headers["hx-redirect"] == "/joueur/mot-de-passe"
    assert "Choisissez votre mot de passe" in client.get("/joueur/mot-de-passe").text


def test_password_change_validation(client: TestClient, db: Session) -> None:
    temporary_player(db, "keator")
    player_login(client, "keator", TEMPORARY)
    assert change(client, "mauvais", NEW_PASSWORD).status_code == 400
    assert change(client, TEMPORARY, "court").status_code == 400
    assert change(client, TEMPORARY, NEW_PASSWORD, "autre-chose-encore").status_code == 400
    assert client.get("/joueur", follow_redirects=False).status_code == 303


def test_password_change_unlocks_the_account(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    player = temporary_player(db, "keator")
    player_login(client, "keator", TEMPORARY)
    response = change(client, TEMPORARY, NEW_PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/joueur"
    assert client.get("/joueur").status_code == 200
    db.refresh(player)
    assert not player.must_change_password
    assert verify_password(player.password_hash, NEW_PASSWORD)
    assert player_login(other_client, "keator", TEMPORARY).status_code == 400
    assert player_login(other_client, "keator", NEW_PASSWORD).headers["location"] == "/joueur"


def test_wrong_player_password_stays_on_the_player_form(client: TestClient, db: Session) -> None:
    temporary_player(db, "zilvra")
    response = player_login(client, "zilvra", "9999")
    assert response.status_code == 400
    assert "Mot de passe incorrect." in response.text
    assert 'name="mode" value="joueur"' in response.text


def test_dm_can_reset_a_player_password(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    player = make_user(db, "zilvra")
    login(other_client, "zilvra")
    assert other_client.get("/joueur").status_code == 200
    login(client, "maitre")
    response = post_form(client, f"/mj/joueurs/{player.id}/mot-de-passe-provisoire", {}, "/mj")
    assert response.status_code == 303
    db.refresh(player)
    assert player.must_change_password
    assert verify_password(player.password_hash, TEMPORARY)
    assert other_client.get("/joueur", follow_redirects=False).headers["location"] == "/connexion"
    assert "Mot de passe provisoire" in client.get("/mj/joueurs").text


def test_player_cannot_reset_passwords(client: TestClient, db: Session) -> None:
    victim = make_user(db, "zilvra")
    make_user(db, "keator")
    login(client, "keator")
    response = post_form(client, f"/mj/joueurs/{victim.id}/mot-de-passe-provisoire", {}, "/joueur")
    assert response.status_code == 404
    db.refresh(victim)
    assert not victim.must_change_password
