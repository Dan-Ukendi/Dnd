import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Role
from tests.factories import csrf_token, make_user


@pytest.fixture
def passwordless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "passwordless_login", True)


def enter(client: TestClient, mode: str, login: str = "") -> object:
    return client.post(
        "/connexion",
        data={"mode": mode, "login": login, "csrf_token": csrf_token(client, "/connexion/mj")},
        follow_redirects=False,
    )


def test_dm_enters_without_password(client: TestClient, db: Session, passwordless: None) -> None:
    make_user(db, "maitre", Role.DM)
    form = client.get("/connexion/mj").text
    assert 'name="password"' not in form
    response = enter(client, "mj")
    assert response.status_code == 303
    assert response.headers["location"] == "/mj"
    assert client.get("/mj").status_code == 200


def test_player_enters_without_password_or_pin_setup(
    client: TestClient, db: Session, passwordless: None
) -> None:
    make_user(db, "keator", password=None)
    listing = client.get("/connexion/joueurs").text
    assert 'href="/connexion/joueurs/keator"' in listing
    assert 'name="password"' not in client.get("/connexion/joueurs/keator").text
    response = enter(client, "joueur", "keator")
    assert response.headers["location"] == "/joueur"
    home = client.get("/joueur")
    assert home.status_code == 200
    assert 'href="/joueur/mot-de-passe"' not in home.text


def test_passwordless_still_needs_an_active_account(
    client: TestClient, db: Session, passwordless: None
) -> None:
    make_user(db, "endormi", active=False)
    assert client.get("/connexion/joueurs/endormi").status_code == 404
    assert enter(client, "joueur", "endormi").status_code == 404
    assert enter(client, "mj").status_code == 400


def test_player_cannot_reach_dm_pages_in_passwordless_mode(
    client: TestClient, db: Session, passwordless: None
) -> None:
    make_user(db, "maitre", Role.DM)
    make_user(db, "keator")
    enter(client, "joueur", "keator")
    assert client.get("/mj", follow_redirects=False).status_code == 404


def test_passwords_are_required_by_default(client: TestClient, db: Session) -> None:
    make_user(db, "maitre", Role.DM)
    assert 'name="password"' in client.get("/connexion/mj").text
    assert enter(client, "mj").status_code == 400
