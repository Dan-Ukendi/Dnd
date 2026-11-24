from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_wa_date(value: object) -> datetime | None:
    if not isinstance(value, dict) or not isinstance(value.get("date"), str):
        return None
    try:
        naive = datetime.strptime(value["date"], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    try:
        zone = ZoneInfo(str(value.get("timezone") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return naive.replace(tzinfo=zone).astimezone(UTC)
