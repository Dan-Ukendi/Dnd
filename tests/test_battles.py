import hashlib
import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Battle, BattleMap, BattleToken, Role
from app.services.battles import (
    MAX_ENEMIES_PER_COLOR,
    BattleError,
    LibraryEntry,
    create_battle,
    default_enemies,
    detect_grid,
    enemy_counts,
    import_library,
    import_map,
)
from tests.factories import csrf_token, login, make_character, make_user

CELL = 10
COLS, ROWS = 32, 24


def png(width: int, height: int, grid: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (70, 110, 60))
    if grid:
        draw = ImageDraw.Draw(image)
        for x in range(grid, width, grid):
            draw.line([(x, 0), (x, height)], fill=(20, 20, 20), width=2)
        for y in range(grid, height, grid):
            draw.line([(0, y), (width, y)], fill=(20, 20, 20), width=2)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def battle_map(db: Session) -> BattleMap:
    battle_map, _ = import_map(db, "Forêt", png(COLS * CELL, ROWS * CELL), "foret-32x24.png")
    return battle_map


@pytest.fixture
def battle(db: Session, battle_map: BattleMap) -> Battle:
    make_user(db, "maitre", Role.DM)
    make_character(db, "Zilvra", make_user(db, "alice"))
    make_character(db, "Khaetar Voss", make_user(db, "bob"))
    enemies = [("noir", "Troll"), ("vert", "Gobelin 1"), ("vert", "Gobelin 2")]
    return create_battle(db, battle_map, "Combat 1", enemies)


def tokens(db: Session, battle: Battle) -> dict[str, BattleToken]:
    return {
        token.label: token
        for token in db.query(BattleToken).filter(BattleToken.battle_id == battle.id)
    }


def place(db: Session, battle: Battle, cells: dict[str, tuple[int, int]]) -> None:
    found = tokens(db, battle)
    for label, (col, row) in cells.items():
        found[label].col, found[label].row = col, row
    db.commit()


def move(
    client: TestClient, battle: Battle, token: BattleToken, col: int, row: int, page: str = "/mj"
) -> int:
    token_value = csrf_token(client, page)
    return client.post(
        f"/mj/combat/{battle.id}/pions/{token.id}",
        data={"col": str(col), "row": str(row)},
        headers={"X-CSRF-Token": token_value},
    ).status_code


def post_form(client: TestClient, url: str, data: dict, page: str = "/mj/combat"):
    return client.post(
        url, data={**data, "csrf_token": csrf_token(client, page)}, follow_redirects=False
    )


def test_detect_grid_from_lines() -> None:
    with Image.open(io.BytesIO(png(12 * 40, 8 * 40, grid=40))) as image:
        assert detect_grid(image) == (12, 8)
    with Image.open(io.BytesIO(png(480, 320))) as image:
        assert detect_grid(image) is None


def test_import_library_is_idempotent(db: Session, tmp_path: Path) -> None:
    (tmp_path / "pack").mkdir()
    (tmp_path / "pack" / "Big-Gridded-30x20-Map.png").write_bytes(png(3000, 2000))
    (tmp_path / "lines.png").write_bytes(png(10 * 50, 6 * 50, grid=50))
    (tmp_path / "plain.png").write_bytes(png(400, 300))
    legacy = png(160, 120)
    (tmp_path / "legacy-16x12.png").write_bytes(legacy)
    sha = hashlib.sha256(legacy).hexdigest()
    db.add(
        BattleMap(
            name="Combat 1",
            cols=16,
            rows=12,
            image=legacy,
            image_type="image/png",
            image_sha256=sha,
            source_sha256=sha,
        )
    )
    db.commit()
    entries = (
        LibraryEntry("Grande carte", "pack/Big-Gridded-30x20-Map.png"),
        LibraryEntry("Quadrillage", "lines.png"),
        LibraryEntry("Sans grille", "plain.png"),
        LibraryEntry("Clairière", "legacy-16x12.png"),
        LibraryEntry("Absente", "missing.png"),
    )
    first = {entry.title: status for entry, _, status in import_library(db, tmp_path, entries)}
    assert first == {
        "Grande carte": "created",
        "Quadrillage": "created",
        "Sans grille": "error: grid not found",
        "Clairière": "updated",
        "Absente": "missing",
    }
    maps = {item.name: item for item in db.query(BattleMap)}
    assert set(maps) == {"Grande carte", "Quadrillage", "Clairière"}
    assert (maps["Grande carte"].cols, maps["Grande carte"].rows) == (30, 20)
    assert (maps["Quadrillage"].cols, maps["Quadrillage"].rows) == (10, 6)
    assert maps["Clairière"].image_type == "image/webp"
    with Image.open(io.BytesIO(maps["Grande carte"].image)) as image:
        assert image.format == "WEBP" and image.size == (2400, 1600)
    with Image.open(io.BytesIO(maps["Grande carte"].thumbnail)) as image:
        assert max(image.size) == 480
    second = {entry.title: status for entry, _, status in import_library(db, tmp_path, entries)}
    assert second["Grande carte"] == second["Quadrillage"] == second["Clairière"] == "unchanged"
    assert db.query(BattleMap).count() == 3


def test_dm_creates_a_battle_in_three_steps(
    client: TestClient, db: Session, battle_map: BattleMap
) -> None:
    make_user(db, "maitre", Role.DM)
    make_character(db, "Zilvra", make_user(db, "alice"))
    make_character(db, "Khaetar Voss", make_user(db, "bob"))
    login(client, "maitre")
    empty = client.get("/mj/combat").text
    assert 'href="/mj/combat/nouveau"' in empty
    step1 = client.get("/mj/combat/nouveau").text
    assert f"/mj/combat/nouveau?carte={battle_map.id}" in step1 and "Forêt" in step1
    thumbnail = client.get(f"/mj/combat/cartes/{battle_map.id}/apercu")
    assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/webp"
    step2 = client.get("/mj/combat/nouveau", params={"carte": battle_map.id}).text
    for key in ("noir", "vert", "violet", "orange", "bleu"):
        assert f'name="{key}"' in step2
    step3 = client.get(
        "/mj/combat/nouveau",
        params={"carte": battle_map.id, "etape": "noms", "noir": "2", "bleu": "1", "vert": "x"},
    ).text
    assert re.findall(r'name="nom" value="([^"]+)"', step3) == ["Noir 1", "Noir 2", "Bleu 1"]
    response = client.post(
        "/mj/combat",
        data={
            "csrf_token": csrf_token(client, "/mj/combat"),
            "carte": str(battle_map.id),
            "nom_combat": "Embuscade",
            "couleur": ["noir", "noir", "bleu"],
            "nom": ["Troll", "  ", "Dragon"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    battle = db.query(Battle).one()
    assert response.headers["location"] == f"/mj/combat/{battle.id}"
    assert battle.name == "Embuscade" and battle.map_id == battle_map.id
    found = [(token.label, token.color, token.size) for token in battle.tokens]
    assert found == [
        ("Zilvra", "#9a9a9a", "medium"),
        ("Khaetar", "#c0392b", "medium"),
        ("Troll", "#111111", "medium"),
        ("Noir", "#111111", "medium"),
        ("Dragon", "#1c7ed6", "medium"),
    ]
    party = tokens(db, battle)
    assert party["Zilvra"].character.name == "Zilvra"
    assert party["Khaetar"].character.name == "Khaetar Voss"
    page = client.get(f"/mj/combat/{battle.id}").text
    assert "data-battle-dm" in page and "Embuscade" in page


def test_enemy_counts_are_capped_and_names_unique(db: Session, battle_map: BattleMap) -> None:
    counts = enemy_counts({"noir": "99", "vert": "-3", "orange": "2"})
    assert counts["noir"] == MAX_ENEMIES_PER_COLOR and counts["vert"] == 0
    assert len(default_enemies(counts)) == MAX_ENEMIES_PER_COLOR + 2
    battle = create_battle(db, battle_map, "", [("vert", "Gobelin"), ("vert", "Gobelin")])
    assert battle.name == "Combat 1"
    assert [token.label for token in battle.tokens][2:] == ["Gobelin", "Gobelin 2"]
    assert battle.tokens[0].character_id is None
    again = create_battle(db, battle_map, "Combat 1", [])
    assert again.name == "Combat 1 2"
    with pytest.raises(BattleError):
        create_battle(db, battle_map, "X", [("rose", "Troll")])


def test_create_battle_requires_dm_and_csrf(
    client: TestClient, db: Session, battle: Battle, battle_map: BattleMap
) -> None:
    data = {"carte": str(battle_map.id), "couleur": "noir", "nom": "Troll"}
    login(client, "maitre")
    assert client.post("/mj/combat", data=data).status_code == 403
    for url in (f"/mj/combat/{battle.id}/terminer", f"/mj/combat/{battle.id}/reprendre"):
        assert client.post(url).status_code == 403
    client.cookies.clear()
    login(client, "alice")
    assert post_form(client, "/mj/combat", data, page="/joueur").status_code == 404
    assert post_form(client, f"/mj/combat/{battle.id}/terminer", {}, "/joueur").status_code == 404
    assert client.get("/mj/combat/nouveau").status_code == 404
    assert client.get(f"/mj/combat/cartes/{battle_map.id}/apercu").status_code == 404
    db.expire_all()
    assert db.query(Battle).count() == 1 and battle.ended_at is None


def test_players_see_the_newest_active_battle(
    client: TestClient,
    other_client: TestClient,
    db: Session,
    battle: Battle,
    battle_map: BattleMap,
) -> None:
    newer = create_battle(db, battle_map, "Combat 2", [("orange", "Dragon")])
    place(db, battle, {"Zilvra": (3, 3)})
    place(db, newer, {"Zilvra": (20, 20), "Dragon": (21, 21)})
    login(client, "alice")
    page = client.get("/joueur/combat").text
    assert "Combat 2" in page and "fenetre=12-12-16-12" in page and "Adversaire" in page
    dm = other_client
    login(dm, "maitre")
    assert post_form(dm, f"/mj/combat/{newer.id}/terminer", {}).status_code == 303
    assert "Visible des joueurs" in dm.get(f"/mj/combat/{battle.id}").text
    page = client.get("/joueur/combat").text
    assert "Combat 1" in page and "fenetre=0-0-16-12" in page
    assert post_form(dm, f"/mj/combat/{newer.id}/reprendre", {}).status_code == 303
    assert "Combat 2" in client.get("/joueur/combat").text
    post_form(dm, f"/mj/combat/{newer.id}/terminer", {})
    post_form(dm, f"/mj/combat/{battle.id}/terminer", {})
    assert "Aucun combat" in client.get("/joueur/combat").text
    assert "Aucun combat en cours" in dm.get("/mj/combat").text


def test_dm_places_and_moves_a_token(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    page = client.get("/mj/combat")
    assert page.status_code == 200
    assert "data-battle-dm" in page.text
    assert 'href="/mj/combat"' in client.get("/mj").text
    troll = tokens(db, battle)["Troll"]
    assert move(client, battle, troll, 4, 5) == 204
    db.expire_all()
    assert (troll.col, troll.row) == (4, 5)
    assert move(client, battle, troll, COLS, 0) == 422
    assert move(client, battle, troll, -1, 0) == 422
    db.expire_all()
    assert (troll.col, troll.row) == (4, 5)
    assert f'data-battle-token="{troll.id}"' in client.get("/mj/combat").text
    full = client.get(f"/mj/combat/{battle.id}/carte")
    assert full.status_code == 200 and full.headers["content-type"] == "image/webp"


def test_dm_turns_a_token(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    troll = tokens(db, battle)["Troll"]
    move(client, battle, troll, 4, 5)
    url = f"/mj/combat/{battle.id}/pions/{troll.id}/orientation"
    headers = {"X-CSRF-Token": csrf_token(client, "/mj/combat")}
    assert client.post(url, data={"facing": "3"}, headers=headers).status_code == 204
    assert client.post(url, data={"facing": "8"}, headers=headers).status_code == 422
    db.expire_all()
    assert troll.facing == 3
    assert 'data-facing="3"' in client.get("/mj/combat").text


def test_dm_kills_an_enemy(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    troll = tokens(db, battle)["Troll"]
    move(client, battle, troll, 4, 5)
    url = f"/mj/combat/{battle.id}/pions/{troll.id}/tuer"
    data = {"csrf_token": csrf_token(client, "/mj/combat")}
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    db.expire_all()
    assert troll.defeated and troll.col is None
    assert move(client, battle, troll, 1, 1) == 404
    pc = next(token for token in tokens(db, battle).values() if token.character_id is not None)
    pc_url = f"/mj/combat/{battle.id}/pions/{pc.id}/tuer"
    assert client.post(pc_url, data=data, follow_redirects=False).status_code == 404


def test_dm_resets_the_map(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    troll = tokens(db, battle)["Troll"]
    move(client, battle, troll, 4, 5)
    response = post_form(client, f"/mj/combat/{battle.id}/reinitialiser", {})
    assert response.status_code == 303
    assert response.headers["location"] == f"/mj/combat/{battle.id}"
    db.expire_all()
    assert all(token.col is None and token.row is None for token in tokens(db, battle).values())


def test_move_requires_csrf(client: TestClient, db: Session, battle: Battle) -> None:
    login(client, "maitre")
    troll = tokens(db, battle)["Troll"]
    response = client.post(f"/mj/combat/{battle.id}/pions/{troll.id}", data={"col": 1, "row": 1})
    assert response.status_code == 403
    db.expire_all()
    assert troll.col is None


def test_players_cannot_reach_the_dm_battle(
    client: TestClient, db: Session, battle: Battle
) -> None:
    login(client, "alice")
    troll = tokens(db, battle)["Troll"]
    assert client.get("/mj/combat").status_code == 404
    assert client.get(f"/mj/combat/{battle.id}").status_code == 404
    assert client.get(f"/mj/combat/{battle.id}/carte").status_code == 404
    assert move(client, battle, troll, 1, 1, page="/joueur") == 404
    db.expire_all()
    assert troll.col is None


def test_player_sees_only_the_cells_around_their_token(
    client: TestClient, db: Session, battle: Battle
) -> None:
    place(
        db,
        battle,
        {"Zilvra": (10, 10), "Troll": (12, 13), "Gobelin 1": (14, 10), "Khaetar": (0, 0)},
    )
    found = tokens(db, battle)
    login(client, "alice")
    assert 'href="/joueur/combat"' in client.get("/joueur").text
    page = client.get("/joueur/combat")
    assert page.status_code == 200
    assert 'hx-trigger="every 3s"' in page.text
    shown = set(map(int, re.findall(r'data-battle-token="(\d+)"', page.text)))
    assert shown == {found["Zilvra"].id, found["Troll"].id, found["Gobelin 1"].id}
    assert "fenetre=2-4-16-12" in page.text
    assert 'data-col="10" data-row="9"' in page.text
    assert "Troll" not in page.text and "Khaetar" not in page.text and "Forêt" not in page.text
    view = client.get("/joueur/combat/vue")
    assert view.status_code == 200 and "<html" not in view.text
    image = client.get("/joueur/combat/carte", params={"fenetre": "2-4-16-12"})
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as cropped:
        assert cropped.size == (16 * CELL, 12 * CELL)
    assert client.get("/joueur/combat/carte", params={"fenetre": "0-0-16-12"}).status_code == 404


def test_player_window_stays_inside_the_map(
    client: TestClient, db: Session, battle: Battle
) -> None:
    place(db, battle, {"Khaetar": (0, 0), "Zilvra": (31, 23)})
    login(client, "bob")
    page = client.get("/joueur/combat").text
    assert "fenetre=0-0-16-12" in page
    assert 'data-col="0" data-row="0"' in page
    assert "Zilvra" not in page


def test_unplaced_player_and_player_without_battle(
    client: TestClient, other_client: TestClient, db: Session, battle: Battle
) -> None:
    login(client, "alice")
    page = client.get("/joueur/combat").text
    assert "pas encore placé" in page and "data-battle-token" not in page
    assert client.get("/joueur/combat/carte", params={"fenetre": "0-0-16-12"}).status_code == 404
    make_user(db, "carl")
    login(other_client, "carl")
    assert "Aucun combat" in other_client.get("/joueur/combat").text


def test_player_view_works_in_passwordless_mode(
    client: TestClient, db: Session, battle: Battle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "passwordless_login", True)
    place(db, battle, {"Zilvra": (3, 3)})
    client.post(
        "/connexion",
        data={
            "mode": "joueur",
            "login": "alice",
            "csrf_token": csrf_token(client, "/connexion/mj"),
        },
    )
    assert "fenetre=0-0-16-12" in client.get("/joueur/combat").text
    assert client.get("/mj/combat").status_code == 404


def test_migration_moves_the_first_battle_map_into_the_library(db: Session) -> None:
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    image = png(COLS * CELL, ROWS * CELL)
    sha = hashlib.sha256(image).hexdigest()
    db.rollback()
    command.downgrade(config, "0026")
    try:
        db.execute(
            text(
                "INSERT INTO battles (name, cols, rows, image, image_type, image_sha256) "
                "VALUES ('Combat 1', 32, 24, :image, 'image/png', :sha)"
            ),
            {"image": image, "sha": sha},
        )
        db.execute(
            text(
                "INSERT INTO battle_tokens (battle_id, label, color) VALUES (1, 'Troll', '#111111')"
            )
        )
        db.commit()
        command.upgrade(config, "head")
        battle = db.query(Battle).one()
        assert battle.map is not None and battle.ended_at is None
        assert (battle.map.name, battle.cols, battle.rows) == ("Combat 1", 32, 24)
        assert battle.map.source_sha256 == sha and battle.map.image == image
        assert [token.label for token in battle.tokens] == ["Troll"]
        renamed, status = import_map(db, "Clairière en forêt", image, "il_1588xN.webp")
        assert status == "updated" and renamed.id == battle.map.id
        assert (renamed.cols, renamed.rows) == (32, 24) and renamed.image_type == "image/webp"
    finally:
        db.rollback()
        command.upgrade(config, "head")
