import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Battle, BattleToken, Role
from app.services.battles import FIRST_BATTLE, install_battle
from tests.factories import csrf_token, login, make_character, make_user

CELL = 10


def map_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (FIRST_BATTLE.cols * CELL, FIRST_BATTLE.rows * CELL), "green").save(
        output, "PNG"
    )
    return output.getvalue()


@pytest.fixture
def battle(db: Session) -> Battle:
    make_user(db, "maitre", Role.DM)
    make_character(db, "Zilvra", make_user(db, "alice"))
    make_character(db, "Khaetar Voss", make_user(db, "bob"))
    battle, _ = install_battle(db, FIRST_BATTLE, map_image())
    return battle


def tokens(db: Session) -> dict[str, BattleToken]:
    return {token.label: token for token in db.query(BattleToken).all()}


def place(db: Session, cells: dict[str, tuple[int, int]]) -> dict[str, BattleToken]:
    found = tokens(db)
    for label, (col, row) in cells.items():
        found[label].col, found[label].row = col, row
    db.commit()
    return found


def move(
    client: TestClient, battle: Battle, token: BattleToken, col: int, row: int, page: str = "/mj"
) -> int:
    token_value = csrf_token(client, page)
    return client.post(
        f"/mj/combat/{battle.id}/pions/{token.id}",
        data={"col": str(col), "row": str(row)},
        headers={"X-CSRF-Token": token_value},
    ).status_code


def test_install_creates_tokens_linked_to_characters_once(db: Session, battle: Battle) -> None:
    found = tokens(db)
    assert set(found) == {"Troll", "Gobelin 1", "Gobelin 2", "Zilvra", "Khaetar"}
    assert found["Troll"].size == "large" and found["Troll"].color == "#111111"
    assert found["Gobelin 1"].size == "small" and found["Gobelin 2"].color == "#2f9e44"
    assert found["Zilvra"].character.name == "Zilvra"
    assert found["Khaetar"].character.name == "Khaetar Voss"
    assert all(token.col is None for token in found.values())
    install_battle(db, FIRST_BATTLE)
    assert db.query(BattleToken).count() == 5
    assert db.query(Battle).count() == 1


def test_dm_places_and_moves_a_token(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    page = client.get("/mj/combat")
    assert page.status_code == 200
    assert "data-battle-dm" in page.text
    assert 'href="/mj/combat"' in client.get("/mj").text
    troll = tokens(db)["Troll"]
    assert move(client, battle, troll, 4, 5) == 204
    db.expire_all()
    assert (troll.col, troll.row) == (4, 5)
    assert move(client, battle, troll, FIRST_BATTLE.cols, 0) == 422
    assert move(client, battle, troll, -1, 0) == 422
    db.expire_all()
    assert (troll.col, troll.row) == (4, 5)
    assert f'data-battle-token="{troll.id}"' in client.get("/mj/combat").text
    full = client.get(f"/mj/combat/{battle.id}/carte")
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"


def test_move_requires_csrf(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    troll = tokens(db)["Troll"]
    response = client.post(f"/mj/combat/{battle.id}/pions/{troll.id}", data={"col": 1, "row": 1})
    assert response.status_code == 403
    db.expire_all()
    assert troll.col is None


def test_players_cannot_reach_the_dm_battle(
    client: TestClient, db: Session, battle: Battle
) -> None:
    login(client, "alice")
    troll = tokens(db)["Troll"]
    assert client.get("/mj/combat").status_code == 404
    assert client.get(f"/mj/combat/{battle.id}/carte").status_code == 404
    assert move(client, battle, troll, 1, 1, page="/joueur") == 404
    db.expire_all()
    assert troll.col is None


def test_player_sees_only_the_cells_around_their_token(
    client: TestClient, db: Session, battle: Battle
) -> None:
    found = place(
        db,
        {"Zilvra": (10, 10), "Troll": (12, 13), "Gobelin 1": (14, 10), "Khaetar": (0, 0)},
    )
    login(client, "alice")
    assert 'href="/joueur/combat"' in client.get("/joueur").text
    page = client.get("/joueur/combat")
    assert page.status_code == 200
    assert 'hx-trigger="every 3s"' in page.text
    shown = set(map(int, re.findall(r'data-battle-token="(\d+)"', page.text)))
    assert shown == {found["Zilvra"].id, found["Troll"].id}
    assert "fenetre=7-7-7-7" in page.text
    assert 'data-col="5" data-row="6"' in page.text
    assert "Troll" not in page.text and "Khaetar" not in page.text
    view = client.get("/joueur/combat/vue")
    assert view.status_code == 200 and "<html" not in view.text
    image = client.get("/joueur/combat/carte", params={"fenetre": "7-7-7-7"})
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as cropped:
        assert cropped.size == (7 * CELL, 7 * CELL)
    assert client.get("/joueur/combat/carte", params={"fenetre": "0-0-7-7"}).status_code == 404


def test_player_window_stays_inside_the_map(
    client: TestClient, db: Session, battle: Battle
) -> None:
    place(db, {"Khaetar": (0, 0), "Zilvra": (31, 23)})
    login(client, "bob")
    page = client.get("/joueur/combat").text
    assert "fenetre=0-0-7-7" in page
    assert 'data-col="0" data-row="0"' in page
    assert "Zilvra" not in page


def test_unplaced_player_and_player_without_battle(
    client: TestClient, other_client: TestClient, db: Session, battle: Battle
) -> None:
    login(client, "alice")
    page = client.get("/joueur/combat").text
    assert "pas encore placé" in page and "data-battle-token" not in page
    assert client.get("/joueur/combat/carte", params={"fenetre": "0-0-7-7"}).status_code == 404
    make_user(db, "carl")
    login(other_client, "carl")
    assert "Aucun combat" in other_client.get("/joueur/combat").text


def test_player_view_works_in_passwordless_mode(
    client: TestClient, db: Session, battle: Battle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "passwordless_login", True)
    place(db, {"Zilvra": (3, 3)})
    client.post(
        "/connexion",
        data={
            "mode": "joueur",
            "login": "alice",
            "csrf_token": csrf_token(client, "/connexion/mj"),
        },
    )
    assert "fenetre=0-0-7-7" in client.get("/joueur/combat").text
    assert client.get("/mj/combat").status_code == 404
