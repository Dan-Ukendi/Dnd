import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import Character, CharacterAction, CharacterPlayState, utcnow
from app.services import dice

SLOT_RE = re.compile(r"^emplacements?-de-(sorts?|magie-de-pacte|pacte)\b.*?\bniv(?:eau)?-(\d)\b")
SPELL_LEVEL_RE = re.compile(r"^sorts?-niv-(\d)\b")
CANTRIP_RE = re.compile(r"^sorts?-mineurs?\b")
PARTIAL_SHORT_RE = re.compile(r"(\d+)-recuperees?-au-repos-court")
HIT_DICE_RE = re.compile(r"^\s*(\d{1,2})\s*d\s*(\d{1,2})\s*$", re.I)
FREE_SUFFIX = "-sans-emplacement"
POOL_THRESHOLD = 6
MAX_AMOUNT = 999
MAX_HIT_DICE_SPENT = 20


class PlayError(ValueError):
    pass


def slug(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-")


def base_slug(name: str) -> str:
    return slug(name.split("(", 1)[0])


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _recharge(recharge: str | None, total: int) -> tuple[int, bool]:
    text = slug(recharge or "")
    partial = PARTIAL_SHORT_RE.search(text)
    if partial:
        return min(int(partial.group(1)), total), True
    short = "repos-court" in text
    return (total if short else 0), short or "repos-long" in text


@dataclass(frozen=True)
class Pool:
    key: str
    name: str
    total: int
    recharge: str | None
    short_rest: int
    long_rest: bool
    level: int | None = None
    pact: bool = False

    @property
    def is_slot(self) -> bool:
        return self.level is not None

    @property
    def label(self) -> str:
        if self.pact:
            return f"Magie de pacte, niveau {self.level}"
        return f"Niveau {self.level}"

    @property
    def is_large(self) -> bool:
        return self.total > POOL_THRESHOLD


@dataclass(frozen=True)
class Sheet:
    max_hp: int | None
    hit_dice: int | None
    hit_die: int | None
    hit_die_bonus: int | None
    slots: list[Pool]
    resources: list[Pool]
    static_resources: list[dict[str, Any]]

    def pool(self, key: str) -> Pool | None:
        return next((pool for pool in self.slots + self.resources if pool.key == key), None)


def sheet_of(character: Character) -> Sheet:
    combat = (character.profile.combat if character.profile else None) or {}
    stats = combat.get("stats", {}) if isinstance(combat, dict) else {}
    max_hp = _whole(stats.get("hit_points"))
    hit_match = HIT_DICE_RE.match(str(stats.get("hit_dice") or ""))
    bonus = dice.parse_modifier(str(stats.get("constitution_modifier") or ""))
    slots: list[Pool] = []
    resources: list[Pool] = []
    static: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in combat.get("resources", []) if isinstance(combat, dict) else []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = str(entry["name"])
        total = _whole(entry.get("uses"))
        if not total:
            static.append(entry)
            continue
        short_rest, long_rest = _recharge(entry.get("recharge"), total)
        name_slug = slug(name)
        slot = SLOT_RE.match(name_slug)
        pact = False
        if slot:
            pact = "pacte" in slot.group(1)
            key = f"{'pacte' if pact else 'sort'}-{slot.group(2)}"
            if pact:
                short_rest, long_rest = total, True
        else:
            key = name_slug[:60] or "ressource"
        while key in seen:
            key = f"{key}-bis"
        seen.add(key)
        pool = Pool(
            key,
            name,
            total,
            entry.get("recharge"),
            short_rest,
            long_rest,
            int(slot.group(2)) if slot else None,
            pact,
        )
        (slots if slot else resources).append(pool)
    slots.sort(key=lambda pool: (pool.level or 0, pool.pact))
    return Sheet(
        max_hp if max_hp else None,
        int(hit_match.group(1)) if hit_match and int(hit_match.group(1)) else None,
        int(hit_match.group(2)) if hit_match and int(hit_match.group(2)) > 1 else None,
        bonus,
        slots,
        resources,
        static,
    )


def state_of(character: Character) -> CharacterPlayState:
    if character.play_state is None:
        character.play_state = CharacterPlayState(
            character_id=character.id,
            current_hp=None,
            temp_hp=0,
            hit_dice_used=0,
            slots_used={},
            resources_used={},
        )
    return character.play_state


def _used(state: CharacterPlayState | None, pool: Pool) -> int:
    if state is None:
        return 0
    store = state.slots_used if pool.is_slot else state.resources_used
    value = (store or {}).get(pool.key, 0)
    return min(max(value, 0), pool.total) if isinstance(value, int) else 0


def _set_used(state: CharacterPlayState, pool: Pool, used: int) -> None:
    used = min(max(used, 0), pool.total)
    if pool.is_slot:
        state.slots_used = {**(state.slots_used or {}), pool.key: used}
    else:
        state.resources_used = {**(state.resources_used or {}), pool.key: used}


def current_hp(sheet: Sheet, state: CharacterPlayState | None) -> int | None:
    if sheet.max_hp is None:
        return None
    if state is None or state.current_hp is None:
        return sheet.max_hp
    return min(max(state.current_hp, 0), sheet.max_hp)


def _hit_dice_left(sheet: Sheet, state: CharacterPlayState | None) -> int:
    used = state.hit_dice_used if state is not None else 0
    return max((sheet.hit_dice or 0) - min(used, sheet.hit_dice or 0), 0)


def _amount(value: int) -> int:
    if not 1 <= value <= MAX_AMOUNT:
        raise PlayError("Indiquez une valeur entre 1 et 999.")
    return value


def _require_hp(sheet: Sheet) -> int:
    if sheet.max_hp is None:
        raise PlayError("Les points de vie maximum ne sont pas renseignés.")
    return sheet.max_hp


def _touch(state: CharacterPlayState) -> None:
    state.updated_at = utcnow()


def take_damage(character: Character, amount: int) -> str:
    sheet = sheet_of(character)
    _require_hp(sheet)
    amount = _amount(amount)
    state = state_of(character)
    hp = current_hp(sheet, state) or 0
    absorbed = min(state.temp_hp or 0, amount)
    state.temp_hp = (state.temp_hp or 0) - absorbed
    state.current_hp = max(hp - (amount - absorbed), 0)
    _touch(state)
    detail = f" ({absorbed} absorbés par les PV temporaires)" if absorbed else ""
    return f"{amount} dégâts subis{detail} : {state.current_hp} PV sur {sheet.max_hp}."


def heal(character: Character, amount: int) -> str:
    sheet = sheet_of(character)
    maximum = _require_hp(sheet)
    amount = _amount(amount)
    state = state_of(character)
    state.current_hp = min((current_hp(sheet, state) or 0) + amount, maximum)
    _touch(state)
    return f"{amount} PV rendus : {state.current_hp} PV sur {maximum}."


def set_temp_hp(character: Character, amount: int) -> str:
    sheet = sheet_of(character)
    _require_hp(sheet)
    if not 0 <= amount <= MAX_AMOUNT:
        raise PlayError("Indiquez une valeur entre 0 et 999.")
    state = state_of(character)
    state.temp_hp = amount
    _touch(state)
    return f"PV temporaires : {amount}." if amount else "PV temporaires retirés."


@dataclass
class RestReport:
    message: str
    rolls: list[dice.Roll] = field(default_factory=list)


def short_rest(character: Character, hit_dice_spent: int, rng: random.Random) -> RestReport:
    sheet = sheet_of(character)
    state = state_of(character)
    parts = []
    rolls = []
    if hit_dice_spent:
        if sheet.hit_dice is None or sheet.hit_die is None or sheet.max_hp is None:
            raise PlayError("Les dés de vie ne sont pas renseignés.")
        if not 0 < hit_dice_spent <= min(_hit_dice_left(sheet, state), MAX_HIT_DICE_SPENT):
            raise PlayError("Pas assez de dés de vie disponibles.")
        bonus = sheet.hit_die_bonus or 0
        healed = 0
        for _ in range(hit_dice_spent):
            result = dice.roll_expression(
                dice.parse(f"1d{sheet.hit_die}{dice.format_modifier(bonus)}"), rng
            )
            rolls.append(result)
            healed += max(result.total, 0)
        state.hit_dice_used = min(state.hit_dice_used + hit_dice_spent, sheet.hit_dice)
        state.current_hp = min((current_hp(sheet, state) or 0) + healed, sheet.max_hp)
        plural = "s" if hit_dice_spent > 1 else ""
        parts.append(f"{hit_dice_spent} dé{plural} de vie : {healed} PV rendus")
    restored = []
    for pool in sheet.slots + sheet.resources:
        used = _used(state, pool)
        if pool.short_rest and used:
            _set_used(state, pool, used - pool.short_rest)
            restored.append(pool.name)
    if restored:
        parts.append("récupéré : " + ", ".join(restored))
    _touch(state)
    detail = " ; ".join(parts) if parts else "rien à récupérer"
    return RestReport(f"Repos court terminé ({detail}).", rolls)


def long_rest(character: Character) -> RestReport:
    sheet = sheet_of(character)
    state = state_of(character)
    state.current_hp = None
    state.temp_hp = 0
    state.hit_dice_used = 0
    for pool in sheet.slots + sheet.resources:
        if pool.long_rest:
            _set_used(state, pool, 0)
    _touch(state)
    hp = f" : {sheet.max_hp} PV" if sheet.max_hp is not None else ""
    return RestReport(f"Repos long terminé{hp}, emplacements et ressources récupérés.")


def _pool(sheet: Sheet, key: str) -> Pool:
    pool = sheet.pool(key)
    if pool is None:
        raise PlayError("Cette ressource n'existe plus dans le dossier.")
    return pool


def spend(character: Character, key: str, amount: int = 1) -> str:
    pool = _pool(sheet_of(character), key)
    amount = _amount(amount)
    state = state_of(character)
    left = pool.total - _used(state, pool)
    if amount > left:
        raise PlayError(f"Plus assez de « {pool.name} » ({left} sur {pool.total}).")
    _set_used(state, pool, _used(state, pool) + amount)
    _touch(state)
    return f"{pool.name} : {left - amount} sur {pool.total}."


def restore(character: Character, key: str, amount: int = 1) -> str:
    pool = _pool(sheet_of(character), key)
    amount = _amount(amount)
    state = state_of(character)
    used = _used(state, pool)
    if not used:
        raise PlayError(f"« {pool.name} » est déjà au maximum.")
    _set_used(state, pool, used - amount)
    _touch(state)
    return f"{pool.name} : {pool.total - _used(state, pool)} sur {pool.total}."


@dataclass(frozen=True)
class CastOption:
    value: str
    label: str


@dataclass(frozen=True)
class SpellInfo:
    level: int
    at_will: bool
    options: list[CastOption]


def spell_level(category: str | None) -> int | None:
    text = slug(category or "")
    if CANTRIP_RE.match(text):
        return 0
    match = SPELL_LEVEL_RE.match(text)
    return int(match.group(1)) if match else None


def _free_resource(sheet: Sheet, action: CharacterAction) -> Pool | None:
    name = base_slug(action.name)
    for pool in sheet.resources:
        text = slug(pool.name)
        if text.endswith(FREE_SUFFIX) and text[: -len(FREE_SUFFIX)] == name:
            return pool
    return None


def spell_info(sheet: Sheet, action: CharacterAction) -> SpellInfo | None:
    level = spell_level(action.category)
    if not level:
        return None
    uses = slug(action.uses or "")
    if "a-volonte" in uses:
        return SpellInfo(level, True, [])
    options = [
        CastOption(f"slot:{pool.key}", pool.label)
        for pool in sheet.slots
        if pool.level is not None and pool.level >= level
    ]
    free = _free_resource(sheet, action)
    if free is not None:
        options.append(CastOption(f"free:{free.key}", "Sans emplacement"))
    if "rituel" in slug(action.category or ""):
        options.append(CastOption("ritual", "En rituel, sans emplacement"))
    return SpellInfo(level, False, options)


def linked_resource(sheet: Sheet, action: CharacterAction) -> Pool | None:
    if spell_level(action.category) is not None:
        return None
    name = base_slug(action.name)
    uses = slug(action.uses or "")
    for pool in sheet.resources:
        base = base_slug(pool.name)
        if base and (base == name or re.search(rf"(?:^|-){re.escape(base)}(?:-|$)", uses)):
            return pool
    return None


def cast(character: Character, action: CharacterAction, option: str) -> str:
    sheet = sheet_of(character)
    info = spell_info(sheet, action)
    if info is None or info.at_will:
        raise PlayError("Ce sort ne consomme rien.")
    if option not in {choice.value for choice in info.options}:
        raise PlayError("Choisissez un emplacement valable pour ce sort.")
    if option == "ritual":
        return f"{action.name} lancé en rituel, sans emplacement."
    kind, key = option.split(":", 1)
    pool = _pool(sheet, key)
    state = state_of(character)
    left = pool.total - _used(state, pool)
    if left <= 0:
        if pool.is_slot:
            raise PlayError(f"Plus d'emplacement ({pool.label.lower()}) : {action.name} refusé.")
        raise PlayError(f"« {pool.name} » est épuisé : {action.name} refusé.")
    _set_used(state, pool, _used(state, pool) + 1)
    _touch(state)
    if kind == "free":
        return f"{action.name} lancé sans emplacement ({pool.name} : {left - 1} sur {pool.total})."
    return f"{action.name} lancé ({pool.label.lower()}) : {left - 1} sur {pool.total} restant."


@dataclass(frozen=True)
class PoolView:
    key: str
    name: str
    label: str
    total: int
    left: int
    recharge: str | None
    large: bool

    @property
    def pips(self) -> list[bool]:
        return [index < self.left for index in range(self.total)]


@dataclass(frozen=True)
class PlayView:
    character_id: int
    max_hp: int | None
    hp: int | None
    temp_hp: int
    hit_dice: int | None
    hit_die: int | None
    hit_dice_left: int
    slots: list[PoolView]
    resources: list[PoolView]
    static_resources: list[dict[str, Any]]

    @property
    def tracks_anything(self) -> bool:
        return bool(self.max_hp or self.slots or self.resources)

    @property
    def hp_percent(self) -> int:
        if not self.max_hp or self.hp is None:
            return 0
        return round(100 * self.hp / self.max_hp)

    @property
    def can_spend_hit_dice(self) -> bool:
        return bool(self.hit_die and self.hit_dice and self.max_hp)


def _pool_view(pool: Pool, state: CharacterPlayState | None) -> PoolView:
    return PoolView(
        pool.key,
        pool.name,
        pool.label if pool.is_slot else pool.name,
        pool.total,
        pool.total - _used(state, pool),
        pool.recharge,
        pool.is_large,
    )


def play_view(character: Character) -> PlayView:
    sheet = sheet_of(character)
    state = character.play_state
    return PlayView(
        character.id,
        sheet.max_hp,
        current_hp(sheet, state),
        min(state.temp_hp, MAX_AMOUNT) if state is not None else 0,
        sheet.hit_dice,
        sheet.hit_die,
        _hit_dice_left(sheet, state),
        [_pool_view(pool, state) for pool in sheet.slots],
        [_pool_view(pool, state) for pool in sheet.resources],
        sheet.static_resources,
    )
