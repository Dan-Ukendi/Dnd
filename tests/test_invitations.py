import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invitation, Role, User, utcnow
from tests.factories import csrf_token, login, make_user, post_form

LINK_RE = re.compile(r'value="(http://testserver/invitation/[^"]+)"')


def create_player_and_invite(dm_client: TestClient) -> str:
    created = post_form(
        dm_client, "/mj/joueurs", {"display_name": "Chloé", "login": "chloe"}, "/connexion/mj"
    )
    assert created.status_code == 303
    page = dm_client.get("/mj/joueurs").text
    player_id = re.search(r"/mj/joueurs/(\d+)/invitation", page).group(1)
    response = post_form(dm_client, f"/mj/joueurs/{player_id}/invitation", {}, "/connexion/mj")
    assert response.status_code == 200
    return LINK_RE.search(response.text).group(1).replace("http://testserver", "")


def accept(client: TestClient, path: str, password: str, confirmation: str | None = None):
    token = csrf_token(client, "/connexion/mj")
    return client.post(
        path,
        data={
            "password": password,
            "password_confirmation": confirmation if confirmation is not None else password,
            "csrf_token": token,
        },
        follow_redirects=False,
    )


def test_invitation_flow_creates_a_usable_account(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    path = create_player_and_invite(client)

    page = other_client.get(path)
    assert page.status_code == 200
    assert "chloe" in page.text

    response = accept(other_client, path, "un-tres-bon-mot-de-passe")
    assert response.status_code == 303
    assert response.headers["location"] == "/joueur"
    assert other_client.get("/joueur").status_code == 200

    other_client.post("/deconnexion", data={"csrf_token": csrf_token(other_client, "/joueur")})
    assert login(other_client, "chloe", "un-tres-bon-mot-de-passe").status_code == 303


def test_invitation_link_is_single_use(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    path = create_player_and_invite(client)
    assert accept(other_client, path, "un-tres-bon-mot-de-passe").status_code == 303
    assert other_client.get(path).status_code == 404
    assert accept(other_client, path, "un-autre-mot-de-passe").status_code == 404


def test_invitation_rejects_weak_or_mismatched_passwords(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    path = create_player_and_invite(client)
    short = accept(other_client, path, "court")
    assert short.status_code == 400
    assert "au moins 10 caractères" in short.text
    mismatch = accept(other_client, path, "un-tres-bon-mot-de-passe", "un-autre-mot-de-passe")
    assert mismatch.status_code == 400
    assert db.scalar(select(User).where(User.login == "chloe")).password_hash is None


def test_expired_invitation_is_rejected(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    path = create_player_and_invite(client)
    invitation = db.scalar(select(Invitation))
    invitation.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert other_client.get(path).status_code == 404


def test_new_invitation_revokes_the_previous_one(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_user(db, "maitre", Role.DM)
    login(client, "maitre")
    first = create_player_and_invite(client)
    player_id = db.scalar(select(User.id).where(User.login == "chloe"))
    response = post_form(client, f"/mj/joueurs/{player_id}/invitation", {}, "/connexion/mj")
    second = LINK_RE.search(response.text).group(1).replace("http://testserver", "")
    assert other_client.get(first).status_code == 404
    assert other_client.get(second).status_code == 200


def test_invalid_token_is_404(client: TestClient) -> None:
    assert client.get("/invitation/not-a-real-token").status_code == 404


def test_health_reports_database_status(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
