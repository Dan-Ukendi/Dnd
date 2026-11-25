"""Move the campaign present from An 987 to An 829 (Echo revolt 80 years ago)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-25
"""

import logging
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

OFFSET = 158
OLD_PRESENT = 987
NEW_PRESENT = OLD_PRESENT - OFFSET
# C7 (An 781) is the last event dated on the imperial calendar rather than from the present.
LAST_FIXED_YEAR = 781

YearPair = tuple[int, int | None]

PRESENT_CODES = (
    *(f"C{number}" for number in range(19, 25)),
    *(f"S{number}" for number in range(1, 21)),
)
OLD_YEARS: dict[str, YearPair] = {
    "A4": (-485, 987),
    "B8": (751, None),
    "C1": (752, None),
    "C8": (958, None),
    "C9": (972, None),
    "C10": (979, None),
    "C11": (964, None),
    "C12": (981, None),
    "C13": (836, None),
    "C14": (933, None),
    "C15": (956, None),
    "C16": (968, None),
    "C17": (971, None),
    "C18": (972, None),
    **{code: (OLD_PRESENT, None) for code in PRESENT_CODES},
    "C23": (OLD_PRESENT, OLD_PRESENT),
}
RANGES: dict[str, YearPair] = {"B8": (757, 760), "C1": (757, 760)}


def _new_years(code: str, old: YearPair) -> YearPair:
    if code in RANGES:
        return RANGES[code]
    start, end = old
    if code == "A4":
        return start, None if end is None else end - OFFSET
    return start - OFFSET, None if end is None else end - OFFSET


TIMELINE: dict[str, tuple[YearPair, YearPair]] = {
    code: (old, _new_years(code, old)) for code, old in OLD_YEARS.items()
}

# Year mentions in free text: the dataset years that are relative to the campaign present.
TEXT_OLD_YEARS = (836, 933, 956, 958, 964, 968, 971, 972, 979, 981, 987)
YEAR_CONTEXT = r"(?P<lead>\b(?:[Ee]n|[Dd]e|[Vv]ers|[Dd]epuis|[Aa]nnée|l'an|l’an|[Aa]n))(?P<gap>\s+)"
TEXT_TABLES = (
    "character_profiles",
    "character_events",
    "character_event_people",
    "character_event_organizations",
    "character_relations",
    "character_items",
    "character_actions",
    "character_journal_entries",
    "timeline_events",
    "timeline_event_fiches",
    "artefacts",
    "lore_groups",
    "lore_stories",
    "lore_entries",
    "places",
    "positions",
)
LORE_YEAR_TABLES = ("lore_entries", "lore_stories")

events = sa.table(
    "timeline_events",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("start_year", sa.Integer),
    sa.column("end_year", sa.Integer),
)


def _shift_timeline(forward: bool) -> int:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(events.c.id, events.c.code, events.c.start_year, events.c.end_year).where(
            events.c.code.in_(TIMELINE)
        )
    ).all()
    changed = 0
    for row in rows:
        old, new = TIMELINE[row.code]
        source, target = (old, new) if forward else (new, old)
        start, end = row.start_year, row.end_year
        if row.code in RANGES:
            if (start, end) == source:
                start, end = target
        else:
            if start == source[0]:
                start = target[0]
            if source[1] is not None and end == source[1]:
                end = target[1]
        if (start, end) != (row.start_year, row.end_year):
            if end is not None and end < start:
                logger.warning("Year shift skipped for %s: inconsistent range", row.code)
                continue
            connection.execute(
                sa.update(events)
                .where(events.c.id == row.id)
                .values(start_year=start, end_year=end)
            )
            changed += 1
    return changed


def _shift_column(table: sa.TableClause, name: str, low: int, high: int, delta: int) -> sa.Case:
    column = table.c[name]
    return sa.case((column.between(low, high), column + delta), else_=column)


def _shift_years(table_name: str, names: Sequence[str], forward: bool, back_from: int) -> int:
    table = sa.table(table_name, *(sa.column(name, sa.Integer) for name in names))
    if forward:
        low, high, delta = NEW_PRESENT + 1, OLD_PRESENT, -OFFSET
    else:
        low, high, delta = back_from, NEW_PRESENT, OFFSET
    result = op.get_bind().execute(
        sa.update(table)
        .where(sa.or_(*(table.c[name].between(low, high) for name in names)))
        .values({name: _shift_column(table, name, low, high, delta) for name in names})
    )
    return result.rowcount or 0


def _text_pattern(years: Sequence[int]) -> re.Pattern[str]:
    numbers = "|".join(str(year) for year in years)
    return re.compile(YEAR_CONTEXT + rf"(?P<year>{numbers})\b")


def _shift_text(forward: bool) -> int:
    delta = -OFFSET if forward else OFFSET
    years = TEXT_OLD_YEARS if forward else tuple(year - OFFSET for year in TEXT_OLD_YEARS)
    pattern = _text_pattern(years)
    prefilter = r"\m(" + "|".join(str(year) for year in years) + r")\M"

    def replace(match: re.Match[str]) -> str:
        return f"{match['lead']}{match['gap']}{int(match['year']) + delta}"

    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing = set(inspector.get_table_names())
    changed = 0
    for table_name in TEXT_TABLES:
        if table_name not in existing:
            continue
        keys = inspector.get_pk_constraint(table_name)["constrained_columns"]
        columns = [
            column["name"]
            for column in inspector.get_columns(table_name)
            if isinstance(column["type"], sa.String) and column["name"] not in keys
        ]
        if not keys or not columns:
            continue
        table = sa.table(table_name, *(sa.column(name) for name in (*keys, *columns)))
        for name in columns:
            column = table.c[name]
            rows = connection.execute(
                sa.select(*(table.c[key] for key in keys), column).where(
                    column.regexp_match(prefilter)
                )
            ).all()
            for row in rows:
                value = row[-1]
                updated = pattern.sub(replace, value)
                if updated == value:
                    continue
                match_keys = (table.c[key] == row[index] for index, key in enumerate(keys))
                connection.execute(
                    sa.update(table).where(sa.and_(*match_keys)).values({name: updated})
                )
                changed += 1
    return changed


def _run(forward: bool) -> None:
    counts = {
        "timeline events": _shift_timeline(forward),
        "character events": _shift_years(
            "character_events", ("world_year",), forward, NEW_PRESENT + 1 - OFFSET
        ),
        "lore rows": sum(
            _shift_years(table, ("start_year", "end_year"), forward, LAST_FIXED_YEAR + 1)
            for table in LORE_YEAR_TABLES
        ),
        "text fields": _shift_text(forward),
    }
    logger.info(
        "Campaign present %s: %s",
        f"{OLD_PRESENT} -> {NEW_PRESENT}" if forward else f"{NEW_PRESENT} -> {OLD_PRESENT}",
        ", ".join(f"{count} {label}" for label, count in counts.items()),
    )


def upgrade() -> None:
    _run(forward=True)


def downgrade() -> None:
    _run(forward=False)
