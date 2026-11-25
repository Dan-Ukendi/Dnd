from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import Role, User
from app.security import hash_password, verify_password
from app.services.accounts import set_temporary_password
from tests.factories import csrf_token, login, make_article, make_character, make_user, post_form

TEMPORARY = "0000"
NEW_PIN = "4827"
MASTER_KEY = "0610"


def temporary_player(db: Session, login_name: str) -> User:
    player = make_user(db, login_name, password=None)
    set_temporary_password(db, player, TEMPORARY)
    return player


def make_dm_with_master_key(db: Session) -> User:
    dm = make_user(db, "maitre", Role.DM)
    dm.password_hash = hash_password(MASTER_KEY)
    db.commit()
    return dm


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


def set_pin(
    client: TestClient, new: str, confirmation: str | None = None, current: str | None = None
) -> Response:
    data = {
        "new_pin": new,
        "new_pin_confirmation": new if confirmation is None else confirmation,
        "csrf_token": csrf_token(client, "/joueur/mot-de-passe"),
    }
    if current is not None:
        data["current_pin"] = current
    return client.post("/joueur/mot-de-passe", data=data, follow_redirects=False)


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


def test_temporary_code_forces_a_pin(client: TestClient, db: Session) -> None:
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
    page = client.get("/joueur/mot-de-passe").text
    assert "Choisissez votre code PIN" in page
    assert 'name="current_pin"' not in page


def test_pin_validation(client: TestClient, db: Session) -> None:
    temporary_player(db, "keator")
    player_login(client, "keator", TEMPORARY)
    for bad in ("123", "12345", "12a4", "un-long-mot-de-passe"):
        assert set_pin(client, bad).status_code == 400
    assert set_pin(client, NEW_PIN, "4828").status_code == 400
    unchanged = set_pin(client, TEMPORARY)
    assert unchanged.status_code == 400
    assert "différent" in unchanged.text
    assert client.get("/joueur", follow_redirects=False).status_code == 303


def test_first_pin_unlocks_the_account(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    player = temporary_player(db, "keator")
    player_login(client, "keator", TEMPORARY)
    response = set_pin(client, NEW_PIN)
    assert response.status_code == 303
    assert response.headers["location"] == "/joueur"
    assert client.get("/joueur").status_code == 200
    db.refresh(player)
    assert not player.must_change_password
    assert verify_password(player.password_hash, NEW_PIN)
    assert player_login(other_client, "keator", TEMPORARY).status_code == 400
    assert player_login(other_client, "keator", NEW_PIN).headers["location"] == "/joueur"


def test_changing_the_pin_later_needs_the_current_one(client: TestClient, db: Session) -> None:
    player = temporary_player(db, "keator")
    player_login(client, "keator", TEMPORARY)
    set_pin(client, NEW_PIN)
    assert 'name="current_pin"' in client.get("/joueur/mot-de-passe").text
    assert set_pin(client, "1357", current="9999").status_code == 400
    assert set_pin(client, "1357").status_code == 400
    assert set_pin(client, "1357", current=NEW_PIN).status_code == 303
    db.refresh(player)
    assert verify_password(player.password_hash, "1357")


def test_wrong_player_code_stays_on_the_player_form(client: TestClient, db: Session) -> None:
    temporary_player(db, "zilvra")
    response = player_login(client, "zilvra", "9999")
    assert response.status_code == 400
    assert "Code incorrect." in response.text
    assert 'name="mode" value="joueur"' in response.text


def test_dm_password_opens_every_player_account(
    client: TestClient, other_client: TestClient, db: Session
) -> None:
    make_dm_with_master_key(db)
    keator = temporary_player(db, "keator")
    zilvra = make_user(db, "zilvra")
    response = player_login(client, "keator", MASTER_KEY)
    assert response.status_code == 303
    assert response.headers["location"] == "/joueur"
    home = client.get("/joueur")
    assert home.status_code == 200
    assert 'href="/joueur/mot-de-passe"' not in home.text
    assert client.get("/joueur/mot-de-passe").status_code == 404
    db.refresh(keator)
    assert keator.must_change_password
    assert verify_password(keator.password_hash, TEMPORARY)
    assert player_login(other_client, "zilvra", MASTER_KEY).headers["location"] == "/joueur"
    db.refresh(zilvra)
    assert not verify_password(zilvra.password_hash, MASTER_KEY)


def test_master_key_never_opens_a_dm_or_disabled_account(client: TestClient, db: Session) -> None:
    make_dm_with_master_key(db)
    make_user(db, "second", Role.DM)
    make_user(db, "endormi", active=False)
    assert login(client, "second", MASTER_KEY).status_code == 400
    assert login(client, "endormi", MASTER_KEY).status_code == 400


def test_disabled_dm_password_is_not_a_master_key(client: TestClient, db: Session) -> None:
    dm = make_dm_with_master_key(db)
    dm.is_active = False
    db.commit()
    temporary_player(db, "keator")
    assert player_login(client, "keator", MASTER_KEY).status_code == 400


def test_player_password_is_not_a_master_key(client: TestClient, db: Session) -> None:
    make_dm_with_master_key(db)
    temporary_player(db, "keator")
    zilvra = temporary_player(db, "zilvra")
    player_login(client, "zilvra", TEMPORARY)
    set_pin(client, NEW_PIN)
    db.refresh(zilvra)
    assert verify_password(zilvra.password_hash, NEW_PIN)
    assert player_login(client, "keator", NEW_PIN).status_code == 400


def test_dm_can_reset_a_player_pin(
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
    assert "Code provisoire" in client.get("/mj/joueurs").text


def test_player_cannot_reset_pins(client: TestClient, db: Session) -> None:
    victim = make_user(db, "zilvra")
    make_user(db, "keator")
    login(client, "keator")
    response = post_form(client, f"/mj/joueurs/{victim.id}/mot-de-passe-provisoire", {}, "/joueur")
    assert response.status_code == 404
    db.refresh(victim)
    assert not victim.must_change_password
