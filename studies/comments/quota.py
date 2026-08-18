"""Durable daily-quota ledger for the YouTube Data API.

The API grants 10,000 units per project per day, resetting at **midnight Pacific**,
and the collector is expected to run for weeks across restarts. Two properties matter:

- **The ledger is durable, not in-process.** A limiter that lives in memory resets
  every time the process does, which lets a crash-loop re-burst a full day's quota in
  minutes. Usage is committed to the database as it is spent.
- **Units are charged for calls that were actually billed.** Spend is recorded after a
  response comes back, so a retry storm against a 5xx does not consume the budget.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# The API's quota day is Pacific, regardless of where the collector runs.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")

DEFAULT_DAILY_UNITS = 10_000


def quota_day(now: datetime | None = None) -> str:
    """The ``YYYY-MM-DD`` Pacific date whose quota bucket ``now`` falls in."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(QUOTA_TZ).date().isoformat()


class QuotaLedger:
    """Tracks units spent per Pacific day against a budget.

    ``budget`` should sit a little under the project's real allowance so a miscount
    can't push the collector into a hard 403 mid-video.
    """

    def __init__(self, conn: sqlite3.Connection, budget: int = 9_500) -> None:
        if budget <= 0:
            raise ValueError("budget must be positive")
        self.conn = conn
        self.budget = budget

    def used(self, day: str | None = None) -> int:
        day = day or quota_day()
        row = self.conn.execute(
            "SELECT units_used FROM quota_ledger WHERE day_pt = ?", (day,)
        ).fetchone()
        return int(row[0]) if row else 0

    def remaining(self, day: str | None = None) -> int:
        return max(0, self.budget - self.used(day))

    def can_spend(self, units: int = 1, day: str | None = None) -> bool:
        return self.remaining(day) >= units

    def spend(self, units: int, day: str | None = None) -> int:
        """Record ``units`` against the day's bucket and commit. Returns the new total.

        Committing here rather than at the end of a video is deliberate: the ledger has
        to survive a kill -9 between two pages, or the next start re-bursts.
        """
        if units < 0:
            raise ValueError("units must be non-negative")
        day = day or quota_day()
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO quota_ledger (day_pt, units_used, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(day_pt) DO UPDATE SET
                units_used = units_used + excluded.units_used,
                updated_at = excluded.updated_at
            """,
            (day, units, now),
        )
        self.conn.commit()
        return self.used(day)

    def exhaust(self, day: str | None = None) -> None:
        """Mark the day fully spent after the API reports the quota gone.

        The API's own count is authoritative; ours can drift low if another process
        shares the key. When it says stop, the local ledger stops too.
        """
        day = day or quota_day()
        self.spend(max(0, self.remaining(day)), day)
