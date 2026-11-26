import random
import re
from dataclasses import dataclass, field

MAX_EXPRESSION_LENGTH = 60
MAX_TERMS = 10
MAX_DICE = 100
MAX_SIDES = 1000
MAX_CONSTANT = 1000
D20_KINDS = frozenset({"attack", "check", "save", "initiative"})
ROLL_KINDS = D20_KINDS | {"damage", "free"}
MODES = frozenset({"normal", "advantage", "disadvantage"})

MINUS_SIGNS = str.maketrans({"−": "-", "–": "-", "—": "-"})
EXPRESSION_RE = re.compile(r"[+-]?(?:\d*d\d+|\d+)(?:[+-](?:\d*d\d+|\d+))*")
TERM_RE = re.compile(r"([+-]?)(?:(\d*)d(\d+)|(\d+))")
MODIFIER_RE = re.compile(r"^\s*([+-]?)\s*(\d{1,3})\b")
DICE_TEXT_RE = re.compile(
    r"(?<![\w])\d{1,3}\s*d\s*\d{1,4}(?:\s*[+−–-]\s*\d{1,4}(?:\s*d\s*\d{1,4})?(?![\w]))*", re.I
)


class DiceError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    sign: int
    count: int
    sides: int = 0

    @property
    def is_constant(self) -> bool:
        return self.sides == 0


@dataclass(frozen=True)
class Expression:
    terms: tuple[Term, ...]

    @property
    def text(self) -> str:
        parts = []
        for term in self.terms:
            body = str(term.count) if term.is_constant else f"{term.count}d{term.sides}"
            sign = "-" if term.sign < 0 else "+"
            parts.append(body if not parts and sign == "+" else f"{sign}{body}")
        return "".join(parts)

    @property
    def has_dice(self) -> bool:
        return any(not term.is_constant for term in self.terms)


@dataclass(frozen=True)
class Die:
    sides: int
    value: int
    sign: int = 1
    kept: bool = True


@dataclass(frozen=True)
class Roll:
    kind: str
    expression: str
    dice: list[Die]
    modifier: int
    total: int
    mode: str = "normal"
    natural: int | None = None
    critical: bool = False
    fumble: bool = False
    doubled: bool = False
    follow_ups: list[str] = field(default_factory=list)


def parse(text: str) -> Expression:
    compact = re.sub(r"\s+", "", (text or "").translate(MINUS_SIGNS)).lower()
    if not compact or len(compact) > MAX_EXPRESSION_LENGTH:
        raise DiceError("empty or too long expression")
    if not EXPRESSION_RE.fullmatch(compact):
        raise DiceError(f"invalid dice expression '{text}'")
    terms = []
    for sign, count, sides, constant in TERM_RE.findall(compact):
        direction = -1 if sign == "-" else 1
        if constant:
            value = int(constant)
            if value > MAX_CONSTANT:
                raise DiceError("constant too large")
            terms.append(Term(direction, value))
            continue
        dice, faces = int(count or 1), int(sides)
        if dice < 1 or not 2 <= faces <= MAX_SIDES:
            raise DiceError("invalid die")
        terms.append(Term(direction, dice, faces))
    if len(terms) > MAX_TERMS or sum(t.count for t in terms if not t.is_constant) > MAX_DICE:
        raise DiceError("too many dice")
    return Expression(tuple(terms))


def parse_modifier(text: str | None) -> int | None:
    match = MODIFIER_RE.match((text or "").translate(MINUS_SIGNS))
    if match is None:
        return None
    rest = (text or "")[match.end() :].strip()
    if rest and not rest.startswith("("):
        return None
    value = int(match.group(2))
    return -value if match.group(1) == "-" else value


def format_modifier(value: int) -> str:
    return f"+{value}" if value >= 0 else f"-{-value}"


def dice_in_text(text: str | None) -> list[str]:
    found = []
    for match in DICE_TEXT_RE.finditer(text or ""):
        try:
            found.append(parse(match.group(0)).text)
        except DiceError:
            continue
    return found


def split_dice(text: str | None) -> list[tuple[str, str | None]]:
    segments: list[tuple[str, str | None]] = []
    position = 0
    source = text or ""
    for match in DICE_TEXT_RE.finditer(source):
        try:
            expression = parse(match.group(0)).text
        except DiceError:
            continue
        if match.start() > position:
            segments.append((source[position : match.start()], None))
        segments.append((match.group(0), expression))
        position = match.end()
    if position < len(source):
        segments.append((source[position:], None))
    return segments


def roll_expression(
    expression: Expression, rng: random.Random, *, kind: str = "damage", critical: bool = False
) -> Roll:
    dice: list[Die] = []
    modifier = 0
    for term in expression.terms:
        if term.is_constant:
            modifier += term.sign * term.count
            continue
        count = term.count * 2 if critical else term.count
        dice.extend(Die(term.sides, rng.randint(1, term.sides), term.sign) for _ in range(count))
    total = sum(die.sign * die.value for die in dice) + modifier
    if kind == "damage":
        total = max(total, 0)
    return Roll(kind, expression.text, dice, modifier, total, doubled=critical)


def roll_d20(modifier: int, rng: random.Random, *, kind: str, mode: str = "normal") -> Roll:
    if mode not in MODES:
        raise DiceError("unknown mode")
    first = rng.randint(1, 20)
    if mode == "normal":
        dice = [Die(20, first)]
        natural = first
    else:
        second = rng.randint(1, 20)
        natural = max(first, second) if mode == "advantage" else min(first, second)
        keep_first = first == natural
        dice = [Die(20, first, kept=keep_first), Die(20, second, kept=not keep_first)]
    attack = kind == "attack"
    return Roll(
        kind,
        f"1d20{format_modifier(modifier)}",
        dice,
        modifier,
        natural + modifier,
        mode=mode,
        natural=natural,
        critical=attack and natural == 20,
        fumble=attack and natural == 1,
    )


def roll(
    text: str,
    rng: random.Random,
    *,
    kind: str = "damage",
    mode: str = "normal",
    critical: bool = False,
) -> Roll:
    if kind not in ROLL_KINDS:
        raise DiceError("unknown roll kind")
    if kind in D20_KINDS:
        modifier = parse_modifier(text)
        if modifier is None:
            expression = parse(text)
            if expression.has_dice:
                raise DiceError("a d20 test takes a modifier")
            modifier = sum(term.sign * term.count for term in expression.terms)
        return roll_d20(modifier, rng, kind=kind, mode=mode)
    expression = parse(text)
    if not expression.has_dice:
        raise DiceError("no dice to roll")
    return roll_expression(expression, rng, kind=kind, critical=critical)
