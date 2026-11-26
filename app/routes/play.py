import json
import random
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.deps import DbSession, require_player, require_user
from app.models import Character, CharacterAction, Role, User
from app.services import dice
from app.services import play as play_rules
from app.templating import flash, is_htmx, render

router = APIRouter()

_system_random = random.SystemRandom()
MAX_DAMAGE_ROLLS = 4


def get_rng() -> random.Random:
    return _system_random


def require_member(request: Request, user: Annotated[User, Depends(require_user)]) -> User:
    if user.role == Role.PLAYER:
        return require_player(request, user)
    return user


Member = Annotated[User, Depends(require_member)]
Rng = Annotated[random.Random, Depends(get_rng)]
Amount = Annotated[int, Form(ge=0, le=play_rules.MAX_AMOUNT)]


def _character(db: DbSession, user: User, character_id: int) -> Character:
    character = db.get(Character, character_id)
    if character is None or (user.role != Role.DM and character.player_id != user.id):
        raise HTTPException(status_code=404)
    return character


def _dossier_url(user: User, character: Character) -> str:
    if user.role == Role.DM:
        if character.main_article_id:
            return f"/mj/articles/{character.main_article_id}#actions"
        return f"/mj/personnages/{character.id}"
    return "/joueur#actions"


def _respond(
    request: Request,
    db: DbSession,
    user: User,
    character: Character,
    message: str,
    *,
    error: bool = False,
    details: list[str] | None = None,
) -> Response:
    if error:
        db.rollback()
    else:
        db.commit()
    db.refresh(character)
    if not is_htmx(request):
        flash(request, message, "error" if error else "success")
        return RedirectResponse(_dossier_url(user, character), status_code=303)
    payload = {"text": message, "kind": "error" if error else "success", "details": details or []}
    return render(
        request,
        "components/play_panel.html",
        {"play": play_rules.play_view(character)},
        headers={"HX-Trigger": json.dumps({"play-message": payload})},
    )


@router.post("/des")
def roll_dice(
    request: Request,
    user: Member,
    rng: Rng,
    expression: Annotated[str, Form(max_length=dice.MAX_EXPRESSION_LENGTH)] = "",
    kind: Annotated[str, Form(max_length=20)] = "free",
    mode: Annotated[str, Form(max_length=20)] = "normal",
    crit: Annotated[str, Form(max_length=1)] = "",
    label: Annotated[str, Form(max_length=120)] = "",
    damage: Annotated[str, Form(max_length=300)] = "",
) -> Response:
    try:
        result = dice.roll(
            expression,
            rng,
            kind=kind,
            mode=mode if kind in dice.D20_KINDS else "normal",
            critical=crit == "1" and kind == "damage",
        )
    except dice.DiceError:
        return render(
            request,
            "components/dice_result.html",
            {"error": "Formule de dés invalide.", "label": label.strip(), "expression": expression},
            status_code=422,
        )
    follow_ups = []
    if result.critical:
        for item in damage.split(";")[:MAX_DAMAGE_ROLLS]:
            try:
                follow_ups.append(dice.parse(item).text)
            except dice.DiceError:
                continue
    return render(
        request,
        "components/dice_result.html",
        {"roll": result, "label": label.strip(), "follow_ups": follow_ups},
    )


@router.post("/jeu/{character_id}/pv")
def change_hit_points(
    request: Request,
    character_id: int,
    db: DbSession,
    user: Member,
    operation: Annotated[str, Form(max_length=20)] = "",
    amount: Amount = 0,
) -> Response:
    character = _character(db, user, character_id)
    actions = {
        "degats": play_rules.take_damage,
        "soins": play_rules.heal,
        "temporaires": play_rules.set_temp_hp,
    }
    if operation not in actions:
        raise HTTPException(status_code=400)
    try:
        message = actions[operation](character, amount)
    except play_rules.PlayError as exc:
        return _respond(request, db, user, character, str(exc), error=True)
    return _respond(request, db, user, character, message)


def _roll_line(roll: dice.Roll) -> str:
    values = " + ".join(str(die.value) for die in roll.dice)
    bonus = f" {dice.format_modifier(roll.modifier)}" if roll.modifier else ""
    return f"{roll.expression} : {values}{bonus} = {roll.total}"


@router.post("/jeu/{character_id}/repos-court")
def take_short_rest(
    request: Request,
    character_id: int,
    db: DbSession,
    user: Member,
    rng: Rng,
    hit_dice: Annotated[int, Form(ge=0, le=play_rules.MAX_HIT_DICE_SPENT)] = 0,
) -> Response:
    character = _character(db, user, character_id)
    try:
        report = play_rules.short_rest(character, hit_dice, rng)
    except play_rules.PlayError as exc:
        return _respond(request, db, user, character, str(exc), error=True)
    details = [_roll_line(roll) for roll in report.rolls]
    return _respond(request, db, user, character, report.message, details=details)


@router.post("/jeu/{character_id}/repos-long")
def take_long_rest(request: Request, character_id: int, db: DbSession, user: Member) -> Response:
    character = _character(db, user, character_id)
    return _respond(request, db, user, character, play_rules.long_rest(character).message)


@router.post("/jeu/{character_id}/ressources/{key}/{operation}")
def change_resource(
    request: Request,
    character_id: int,
    key: str,
    operation: str,
    db: DbSession,
    user: Member,
    amount: Annotated[int, Form(ge=1, le=play_rules.MAX_AMOUNT)] = 1,
) -> Response:
    character = _character(db, user, character_id)
    if operation not in {"utiliser", "rendre"}:
        raise HTTPException(status_code=404)
    change = play_rules.spend if operation == "utiliser" else play_rules.restore
    try:
        message = change(character, key, amount)
    except play_rules.PlayError as exc:
        return _respond(request, db, user, character, str(exc), error=True)
    return _respond(request, db, user, character, message)


@router.post("/jeu/{character_id}/sorts/{action_id}")
def cast_spell(
    request: Request,
    character_id: int,
    action_id: int,
    db: DbSession,
    user: Member,
    option: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    character = _character(db, user, character_id)
    action = db.get(CharacterAction, action_id)
    if action is None or action.character_id != character.id:
        return _respond(
            request, db, user, character, "Ce sort n'est plus dans le dossier.", error=True
        )
    try:
        message = play_rules.cast(character, action, option)
    except play_rules.PlayError as exc:
        return _respond(request, db, user, character, str(exc), error=True)
    return _respond(request, db, user, character, message)
