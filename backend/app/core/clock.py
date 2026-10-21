"""Nigerian time: schools, due dates and quiet hours all run on Lagos time (WAT, no daylight saving)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def lagos_today() -> date:
    return datetime.now(LAGOS).date()
