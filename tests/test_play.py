import copy
import json
import random
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Character, CharacterArticle, CharacterPlayState, Role, WaArticle
from app.routes.play import get_rng
from app.services import dice, play
from app.services.dossiers import import_dossier
from tests.factories import bare_numbers, csrf_token, login, make_article, make_character, make_user


class FixedRng(random.Random):
    def __init__(self, values: list[int]) -> None:
        super().__init__(0)
        self.values = values

    def randint(self, a: int, b: int) -> int:
        value = self.values.pop(0)
        assert a <= value <= b
        return value


KHAETAR: dict[str, Any] = {
    "character": "Khaetar Voss",
    "combat": {
        "armor_class": 13,
        "hit_points": 24,
        "hit_dice": "3d8",
        "constitution_modifier": "+2",
        "initiative": "+1",
        "spell_attack": "+5",
        "resources": [
            {
                "name": "Emplacements de magie de pacte (niveau 2)",
                "uses": 2,
                "recharge": "Repos court ou long",
            },
            {
                "name": "Malédiction de la Lame maudite",
                "uses": 1,
                "recharge": "Repos court ou long",
            },
            {
                "name": "Représailles infernales sans emplacement",
                "uses": 1,
                "recharge": "Repos long",
            },
            {"name": "Inspiration", "uses": "selon le MJ"},
        ],
    },
    "actions": [
        {
            "type": "action",
            "name": "Arme de pacte : grande hache",
            "category": "Pacte de la lame",
            "hit": "+5",
            "damage": "1d12 + 3 tranchants (+2 contre la cible maudite)",
        },
        {
            "type": "action",
            "name": "Explosion occulte (Eldritch Blast)",
            "category": "Sort mineur",
            "hit": "+5",
            "damage": "1d10 + 3 force",
            "uses": "À volonté",
        },
        {"type": "action", "name": "Flou (Blur)", "category": "Sort niv. 2, Lame maudite"},
        {
            "type": "action",
            "name": "Simulacre de vie (False Life)",
            "category": "Sort niv. 1, Vigueur fiélonne",
            "uses": "À volonté, sans emplacement",
        },
        {
            "type": "bonus",
            "name": "Malédiction de la Lame maudite (Hexblade's Curse)",
            "category": "Lame maudite",
            "uses": "1 fois par repos court ou long",
        },
        {
            "type": "reaction",
            "name": "Représailles infernales (Hellish Rebuke)",
            "category": "Sort niv. 1, héritage infernal",
            "save": "DEX 13",
            "damage": "2d10 feu, moitié si réussite (3d10 avec un emplacement de pacte)",
        },
    ],
}

ZILVRA: dict[str, Any] = {
    "character": "Zilvra Vhaelor",
    "combat": {
        "hit_points": 28,
        "resources": [
            {"name": "Emplacements de sort (niveau 1)", "uses": 3, "recharge": "Repos long"},
            {
                "name": "Conduit divin",
                "uses": 2,
                "recharge": "Repos long, 1 récupérée au repos court",
            },
            {"name": "Imposition des mains (réserve de PV)", "uses": 15, "recharge": "Repos long"},
        ],
    },
    "actions": [
        {"type": "action", "name": "Soins (Cure Wounds)", "category": "Sort niv. 1"},
        {"type": "action", "name": "Détection du poison", "category": "Sort niv. 1, rituel"},
        {
            "type": "action",
            "name": "Aspect redoutable",
            "category": "Conduit divin, Oathbreaker",
            "uses": "1 Conduit divin",
        },
        {"type": "bonus", "name": "Imposition des mains", "category": "Paladin"},
    ],
}


@dataclass
class Table:
    keator: Character
    zilvra: Character
    khaetar: WaArticle


@pytest.fixture
def table(db: Session) -> Table:
    make_user(db, "maitre", Role.DM)
    khaetar = make_article(db, "Khaetar Voss", content="[p]Occultiste.[/p]")
    return Table(
        make_character(db, "Khaetar Voss", make_user(db, "keator"), khaetar),
        make_character(db, "Zilvra Vhaelor", make_user(db, "zilvra"), make_article(db, "Zilvra")),
        khaetar,
    )


@pytest.fixture
def rolls(app: FastAPI) -> Iterator[list[int]]:
    values: list[int] = []
    app.dependency_overrides[get_rng] = lambda: FixedRng(values)
    yield values
    app.dependency_overrides.pop(get_rng, None)


def _imported(db: Session, data: dict[str, Any]) -> Character:
    return import_dossier(db, copy.deepcopy(data))


def _action(character: Character, name: str) -> Any:
    return next(action for action in character.actions if action.name.startswith(name))


def _used(character: Character, key: str) -> int:
    state = character.play_state
    return 0 if state is None else {**state.slots_used, **state.resources_used}.get(key, 0)


# Dice


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("1d12 + 3", "1d12+3"),
        ("2d6", "2d6"),
        ("d20 − 1", "1d20-1"),
        ("1d8 + 1d6 + 2", "1d8+1d6+2"),
        ("+2d6", "2d6"),
    ],
)
def test_parse_normalizes_expressions(text: str, normalized: str) -> None:
    assert dice.parse(text).text == normalized


@pytest.mark.parametrize("text", ["", "d", "1d1", "abc", "1d6*2", "101d6", "1d6+", "2d10000"])
def test_parse_rejects_invalid_expressions(text: str) -> None:
    with pytest.raises(dice.DiceError):
        dice.parse(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("+5", 5),
        ("-1", -1),
        ("+3 (maîtrise)", 3),
        ("−2", -2),
        ("DEX 13", None),
        ("+5 à la CA", None),
    ],
)
def test_parse_modifier(text: str, value: int | None) -> None:
    assert dice.parse_modifier(text) == value


def test_dice_are_found_in_damage_texts() -> None:
    assert dice.dice_in_text("1d8 + 3 tranchants, 1d10 + 3 à deux mains (+2 maudite)") == [
        "1d8+3",
        "1d10+3",
    ]
    assert dice.dice_in_text("2d10 feu, moitié si réussite (3d10 avec un emplacement)") == [
        "2d10",
        "3d10",
    ]
    assert dice.dice_in_text("+2d6 nécrotiques") == ["2d6"]
    assert dice.dice_in_text("12 PV temporaires") == []
    segments = dice.split_dice("2d8 + 3 PV rendus")
    assert segments == [("2d8 + 3", "2d8+3"), (" PV rendus", None)]
    assert (
        "".join(text for text, _ in dice.split_dice("1d12 + 3 tranchants")) == "1d12 + 3 tranchants"
    )


def test_seeded_rolls_are_reproducible_and_in_range() -> None:
    first = dice.roll("3d6+2", random.Random(42))  # noqa: S311
    again = dice.roll("3d6+2", random.Random(42))  # noqa: S311
    assert [die.value for die in first.dice] == [die.value for die in again.dice]
    assert len(first.dice) == 3 and all(1 <= die.value <= 6 for die in first.dice)
    assert first.total == sum(die.value for die in first.dice) + 2
    assert first.modifier == 2


def test_d20_with_advantage_disadvantage_and_criticals() -> None:
    advantage = dice.roll("+5", FixedRng([7, 20]), kind="attack", mode="advantage")
    assert advantage.natural == 20 and advantage.total == 25 and advantage.critical
    assert [die.kept for die in advantage.dice] == [False, True]
    disadvantage = dice.roll("+5", FixedRng([7, 20]), kind="attack", mode="disadvantage")
    assert disadvantage.natural == 7 and disadvantage.total == 12 and not disadvantage.critical
    fumble = dice.roll("+5", FixedRng([1]), kind="attack")
    assert fumble.fumble and fumble.total == 6
    check = dice.roll("+3", FixedRng([20]), kind="check")
    assert check.total == 23 and not check.critical
    with pytest.raises(dice.DiceError):
        dice.roll("1d6", FixedRng([]), kind="save")


def test_critical_damage_doubles_the_dice() -> None:
    result = dice.roll("1d12+3", FixedRng([5, 9]), kind="damage", critical=True)
    assert [die.value for die in result.dice] == [5, 9]
    assert result.total == 17 and result.doubled
    assert dice.roll("1d4-3", FixedRng([1])).total == 0


# Play rules


def test_sheet_reads_slots_and_resources_from_the_dossier(db: Session, table: Table) -> None:
    sheet = play.sheet_of(_imported(db, KHAETAR))
    assert (sheet.max_hp, sheet.hit_dice, sheet.hit_die, sheet.hit_die_bonus) == (24, 3, 8, 2)
    assert [(pool.key, pool.level, pool.pact, pool.total) for pool in sheet.slots] == [
        ("pacte-2", 2, True, 2)
    ]
    assert [(pool.name, pool.short_rest, pool.long_rest) for pool in sheet.resources] == [
        ("Malédiction de la Lame maudite", 1, True),
        ("Représailles infernales sans emplacement", 0, True),
    ]
    assert [entry["name"] for entry in sheet.static_resources] == ["Inspiration"]
    zilvra = play.sheet_of(_imported(db, ZILVRA))
    assert zilvra.hit_dice is None and zilvra.hit_die_bonus is None
    assert [(pool.key, pool.short_rest) for pool in zilvra.slots] == [("sort-1", 0)]
    assert [pool.short_rest for pool in zilvra.resources] == [1, 0]


def test_missing_values_hide_the_features(db: Session, table: Table) -> None:
    character = _imported(db, {"character": "Khaetar Voss", "combat": {"hit_points": "beaucoup"}})
    view = play.play_view(character)
    assert view.max_hp is None and not view.tracks_anything and not view.can_spend_hit_dice
    with pytest.raises(play.PlayError):
        play.take_damage(character, 3)


def test_damage_heal_and_temporary_hit_points(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    assert play.play_view(character).hp == 24
    play.set_temp_hp(character, 5)
    play.take_damage(character, 8)
    assert (character.play_state.current_hp, character.play_state.temp_hp) == (21, 0)
    play.take_damage(character, 30)
    assert character.play_state.current_hp == 0
    play.heal(character, 50)
    assert character.play_state.current_hp == 24
    with pytest.raises(play.PlayError):
        play.take_damage(character, 0)
    db.commit()
    db.expire_all()
    assert db.get(CharacterPlayState, character.id).current_hp == 24


def test_casting_uses_the_chosen_slot_and_refuses_when_empty(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    sheet = play.sheet_of(character)
    blur = _action(character, "Flou")
    info = play.spell_info(sheet, blur)
    assert info is not None and [option.value for option in info.options] == ["slot:pacte-2"]
    play.cast(character, blur, "slot:pacte-2")
    play.cast(character, blur, "slot:pacte-2")
    assert _used(character, "pacte-2") == 2
    with pytest.raises(play.PlayError, match="Plus d'emplacement"):
        play.cast(character, blur, "slot:pacte-2")
    with pytest.raises(play.PlayError):
        play.cast(character, blur, "slot:sort-9")
    assert play.spell_info(sheet, _action(character, "Explosion occulte")) is None
    assert play.spell_info(sheet, _action(character, "Simulacre")).at_will


def test_free_casting_and_rituals(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    rebuke = _action(character, "Représailles")
    options = [option.value for option in play.spell_info(play.sheet_of(character), rebuke).options]
    assert options == ["slot:pacte-2", "free:represailles-infernales-sans-emplacement"]
    play.cast(character, rebuke, "free:represailles-infernales-sans-emplacement")
    assert _used(character, "pacte-2") == 0
    with pytest.raises(play.PlayError, match="épuisé"):
        play.cast(character, rebuke, "free:represailles-infernales-sans-emplacement")
    zilvra = _imported(db, ZILVRA)
    ritual = _action(zilvra, "Détection")
    assert "ritual" in [o.value for o in play.spell_info(play.sheet_of(zilvra), ritual).options]
    play.cast(zilvra, ritual, "ritual")
    assert _used(zilvra, "sort-1") == 0


def test_linked_resources(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    sheet = play.sheet_of(character)
    assert play.linked_resource(sheet, _action(character, "Malédiction")).key == (
        "malediction-de-la-lame-maudite"
    )
    assert play.linked_resource(sheet, _action(character, "Représailles")) is None
    zilvra = _imported(db, ZILVRA)
    sheet = play.sheet_of(zilvra)
    assert play.linked_resource(sheet, _action(zilvra, "Aspect")).name == "Conduit divin"
    assert play.linked_resource(sheet, _action(zilvra, "Imposition")).is_large


def test_short_rest_spends_hit_dice_and_restores_short_rest_resources(
    db: Session, table: Table
) -> None:
    character = _imported(db, KHAETAR)
    play.take_damage(character, 20)
    for key in ("pacte-2", "malediction-de-la-lame-maudite"):
        play.spend(character, key)
    play.spend(character, "represailles-infernales-sans-emplacement")
    report = play.short_rest(character, 2, FixedRng([3, 8]))
    state = character.play_state
    assert [roll.total for roll in report.rolls] == [5, 10]
    assert (state.current_hp, state.hit_dice_used) == (19, 2)
    assert _used(character, "pacte-2") == 0
    assert _used(character, "malediction-de-la-lame-maudite") == 0
    assert _used(character, "represailles-infernales-sans-emplacement") == 1
    with pytest.raises(play.PlayError):
        play.short_rest(character, 2, FixedRng([1, 1]))
    zilvra = _imported(db, ZILVRA)
    play.spend(zilvra, "conduit-divin", 2)
    play.spend(zilvra, "sort-1", 2)
    play.short_rest(zilvra, 0, FixedRng([]))
    assert (_used(zilvra, "conduit-divin"), _used(zilvra, "sort-1")) == (1, 2)
    with pytest.raises(play.PlayError, match="dés de vie"):
        play.short_rest(zilvra, 1, FixedRng([4]))


def test_long_rest_restores_everything(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    play.set_temp_hp(character, 4)
    play.take_damage(character, 10)
    play.short_rest(character, 1, FixedRng([2]))
    play.spend(character, "represailles-infernales-sans-emplacement")
    play.spend(character, "pacte-2", 2)
    play.long_rest(character)
    view = play.play_view(character)
    assert (view.hp, view.temp_hp, view.hit_dice_left) == (24, 0, 3)
    assert [pool.left for pool in view.slots + view.resources] == [2, 1, 1]


def test_reimport_keeps_the_play_state_and_clamps_it(db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    play.take_damage(character, 4)
    play.spend(character, "pacte-2", 2)
    db.commit()
    again = _imported(db, KHAETAR)
    assert again.play_state.current_hp == 20 and _used(again, "pacte-2") == 2
    smaller = copy.deepcopy(KHAETAR)
    smaller["combat"]["hit_points"] = 12
    smaller["combat"]["resources"][0]["uses"] = 1
    character = _imported(db, smaller)
    view = play.play_view(character)
    assert view.hp == 12 and view.slots[0].left == 0
    play.cast(
        character,
        _action(character, "Représailles"),
        "free:represailles-infernales-sans-emplacement",
    )


# Routes


def _token(client: TestClient) -> str:
    return csrf_token(client, "/joueur")


def _htmx(client: TestClient, path: str, data: dict[str, Any], token: str) -> Any:
    return client.post(
        path,
        data=data,
        headers={"HX-Request": "true", "X-CSRF-Token": token},
        follow_redirects=False,
    )


def test_roll_endpoint_returns_the_detailed_result(
    client: TestClient, db: Session, table: Table, rolls: list[int]
) -> None:
    _imported(db, KHAETAR)
    login(client, "keator")
    token = _token(client)
    rolls.extend([20])
    response = _htmx(
        client,
        "/des",
        {"expression": "+5", "kind": "attack", "label": "Grande hache", "damage": "1d12+3"},
        token,
    )
    assert response.status_code == 200
    assert "Coup critique" in response.text and ">25<" in response.text
    assert 'data-roll="1d12+3"' in response.text and 'data-roll-crit="1"' in response.text
    rolls.extend([4, 6])
    response = _htmx(client, "/des", {"expression": "1d12+3", "kind": "damage", "crit": "1"}, token)
    assert ">13<" in response.text and "critique" in response.text
    rolls.extend([3, 15])
    response = _htmx(
        client, "/des", {"expression": "+1", "kind": "save", "mode": "disadvantage"}, token
    )
    assert "avec désavantage" in response.text and ">4<" in response.text
    bad = _htmx(client, "/des", {"expression": "1d6;drop", "kind": "damage"}, token)
    assert bad.status_code == 422 and "Formule de dés invalide" in bad.text


def test_roll_endpoint_needs_csrf_and_a_session(
    client: TestClient, db: Session, table: Table
) -> None:
    assert client.post("/des", data={"expression": "1d6"}, follow_redirects=False).status_code in (
        303,
        403,
    )
    login(client, "keator")
    assert client.post("/des", data={"expression": "1d6"}).status_code == 403


def test_owner_changes_hit_points_through_htmx(
    client: TestClient, db: Session, table: Table
) -> None:
    _imported(db, KHAETAR)
    login(client, "keator")
    token = _token(client)
    path = f"/jeu/{table.keator.id}/pv"
    response = _htmx(client, path, {"operation": "degats", "amount": "7"}, token)
    assert response.status_code == 200
    assert (
        f'id="play-{table.keator.id}"' in response.text and "<strong>17</strong>" in response.text
    )
    message = json.loads(response.headers["HX-Trigger"])["play-message"]
    assert message["kind"] == "success" and "17 PV sur 24" in message["text"]
    refused = _htmx(client, path, {"operation": "degats", "amount": "0"}, token)
    assert json.loads(refused.headers["HX-Trigger"])["play-message"]["kind"] == "error"
    plain = client.post(
        path,
        data={"operation": "soins", "amount": "3", "csrf_token": token},
        follow_redirects=False,
    )
    assert plain.status_code == 303 and plain.headers["location"] == "/joueur#actions"
    db.expire_all()
    assert db.get(CharacterPlayState, table.keator.id).current_hp == 20


def test_casting_through_the_route(client: TestClient, db: Session, table: Table) -> None:
    character = _imported(db, KHAETAR)
    blur = _action(character, "Flou").id
    login(client, "keator")
    token = _token(client)
    path = f"/jeu/{table.keator.id}/sorts/{blur}"
    for _ in range(2):
        response = _htmx(client, path, {"option": "slot:pacte-2"}, token)
        assert json.loads(response.headers["HX-Trigger"])["play-message"]["kind"] == "success"
    refused = _htmx(client, path, {"option": "slot:pacte-2"}, token)
    assert "Plus d'emplacement" in json.loads(refused.headers["HX-Trigger"])["play-message"]["text"]
    db.expire_all()
    assert db.get(CharacterPlayState, table.keator.id).slots_used == {"pacte-2": 2}
    rest = _htmx(client, f"/jeu/{table.keator.id}/repos-court", {"hit_dice": "0"}, token)
    assert rest.status_code == 200
    db.expire_all()
    assert db.get(CharacterPlayState, table.keator.id).slots_used == {"pacte-2": 0}


def test_other_players_cannot_touch_a_character(
    client: TestClient, db: Session, table: Table
) -> None:
    character = _imported(db, KHAETAR)
    _imported(db, ZILVRA)
    blur = _action(character, "Flou").id
    login(client, "zilvra")
    token = _token(client)
    base = f"/jeu/{table.keator.id}"
    for path, data in [
        (f"{base}/pv", {"operation": "degats", "amount": "5"}),
        (f"{base}/repos-long", {}),
        (f"{base}/repos-court", {}),
        (f"{base}/ressources/pacte-2/utiliser", {}),
        (f"{base}/sorts/{blur}", {"option": "slot:pacte-2"}),
    ]:
        assert _htmx(client, path, data, token).status_code == 404, path
    zilvra_action = _action(db.get(Character, table.zilvra.id), "Soins").id
    own = _htmx(client, f"/jeu/{table.zilvra.id}/sorts/{blur}", {"option": "slot:pacte-2"}, token)
    message = json.loads(own.headers["HX-Trigger"])["play-message"]["text"]
    assert message == "Ce sort n'est plus dans le dossier."
    assert (
        _htmx(
            client,
            f"/jeu/{table.zilvra.id}/sorts/{zilvra_action}",
            {"option": "slot:sort-1"},
            token,
        ).status_code
        == 200
    )
    db.expire_all()
    assert db.get(CharacterPlayState, table.keator.id) is None


def test_play_posts_need_csrf(client: TestClient, db: Session, table: Table) -> None:
    _imported(db, KHAETAR)
    login(client, "keator")
    path = f"/jeu/{table.keator.id}/pv"
    assert client.post(path, data={"operation": "degats", "amount": "5"}).status_code == 403
    assert (
        client.post(f"/jeu/{table.keator.id}/repos-long", headers={"X-CSRF-Token": "x"}).status_code
        == 403
    )
    db.expire_all()
    assert db.get(CharacterPlayState, table.keator.id) is None


def test_dm_can_change_any_character(client: TestClient, db: Session, table: Table) -> None:
    _imported(db, ZILVRA)
    login(client, "maitre")
    token = csrf_token(client, "/mj")
    response = _htmx(
        client,
        f"/jeu/{table.zilvra.id}/ressources/imposition-des-mains-reserve-de-pv/utiliser",
        {"amount": "5"},
        token,
    )
    assert response.status_code == 200 and "10 sur 15" in response.text
    plain = client.post(
        f"/jeu/{table.zilvra.id}/repos-long", data={"csrf_token": token}, follow_redirects=False
    )
    assert plain.status_code == 303
    assert plain.headers["location"] == f"/mj/articles/{table.zilvra.main_article_id}#actions"


def test_passwordless_sessions_can_play(
    client: TestClient, db: Session, table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "passwordless_login", True)
    _imported(db, KHAETAR)
    nopass = make_user(db, "sanspin", password=None)
    character = make_character(db, "Gorm", nopass)
    import_dossier(db, {**copy.deepcopy(KHAETAR), "character": "Gorm"})
    client.post(
        "/connexion",
        data={
            "mode": "joueur",
            "login": "sanspin",
            "csrf_token": csrf_token(client, "/connexion/mj"),
        },
    )
    token = _token(client)
    response = _htmx(
        client, f"/jeu/{character.id}/pv", {"operation": "degats", "amount": "2"}, token
    )
    assert response.status_code == 200 and "<strong>22</strong>" in response.text
    assert (
        _htmx(
            client, f"/jeu/{table.keator.id}/pv", {"operation": "degats", "amount": "2"}, token
        ).status_code
        == 404
    )
    assert _htmx(client, "/des", {"expression": "1d6"}, token).status_code == 200


# Rendering


def test_dossier_shows_roll_buttons_and_the_play_panel(
    client: TestClient, db: Session, table: Table
) -> None:
    _imported(db, KHAETAR)
    login(client, "keator")
    page = client.get("/joueur").text
    assert f'id="play-{table.keator.id}"' in page
    attack = (
        'data-roll="+5" data-roll-kind="attack" data-roll-label="Arme de pacte : grande hache"'
        ' data-roll-damage="1d12+3"'
    )
    assert attack in page
    assert re.search(
        r'data-roll="1d12\+3" data-roll-kind="damage"[^>]*>1d12 \+ 3</button> tranchants', page
    )
    assert 'data-roll="2d10"' in page and 'data-roll="3d10"' in page
    assert 'data-roll="+1" data-roll-kind="initiative"' in page
    assert "Lancer avec" in page and 'value="free:represailles-infernales-sans-emplacement"' in page
    assert (
        f'action="/jeu/{table.keator.id}/ressources/malediction-de-la-lame-maudite/utiliser"'
        in page
    )
    assert "Dés de vie à dépenser" in page and "data-dice-scope" in page
    assert "Inspiration" in page
    fiche = client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    assert f'id="play-{table.keator.id}"' in fiche


def test_dm_page_has_the_panel_without_bare_numbers(
    client: TestClient, db: Session, table: Table
) -> None:
    _imported(db, KHAETAR)
    login(client, "maitre")
    page = client.get(f"/mj/articles/{table.khaetar.wa_id}").text
    assert f'id="play-{table.keator.id}"' in page and 'data-roll="+5"' in page
    assert bare_numbers(page) == []


def test_other_players_see_no_play_controls(client: TestClient, db: Session, table: Table) -> None:
    _imported(db, KHAETAR)
    db.add(CharacterArticle(character_id=table.zilvra.id, article_id=table.khaetar.wa_id))
    db.commit()
    login(client, "zilvra")
    page = client.get(f"/joueur/articles/{table.khaetar.wa_id}").text
    assert "Occultiste." in page
    assert f"/jeu/{table.keator.id}" not in page and "data-roll" not in page
    assert "data-dice-scope" not in page
