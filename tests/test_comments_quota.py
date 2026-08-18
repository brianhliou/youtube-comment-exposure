"""Quota ledger: Pacific-day bucketing and durability across process restarts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from studies.comments import store
from studies.comments.quota import QuotaLedger, quota_day


def test_quota_day_uses_pacific_not_utc():
    # 06:00Z in August is 23:00 the previous day in Pacific daylight time.
    assert quota_day(datetime(2026, 8, 13, 6, 0, tzinfo=UTC)) == "2026-08-12"
    assert quota_day(datetime(2026, 8, 13, 8, 0, tzinfo=UTC)) == "2026-08-13"


def test_naive_datetimes_are_treated_as_utc():
    assert quota_day(datetime(2026, 8, 13, 6, 0)) == "2026-08-12"  # noqa: DTZ001


def test_spend_accumulates_and_bounds(tmp_path):
    conn = store.connect(tmp_path / "q.sqlite")
    ledger = QuotaLedger(conn, budget=10)
    assert ledger.remaining() == 10
    ledger.spend(4)
    ledger.spend(3)
    assert ledger.used() == 7
    assert ledger.can_spend(3)
    assert not ledger.can_spend(4)


def test_usage_survives_a_new_ledger_on_the_same_database(tmp_path):
    """The anti-re-burst property: a restart must not hand back a fresh budget."""
    path = tmp_path / "q.sqlite"
    conn = store.connect(path)
    QuotaLedger(conn, budget=100).spend(90)
    conn.close()

    reopened = store.connect(path)
    assert QuotaLedger(reopened, budget=100).remaining() == 10


def test_exhaust_zeroes_the_remaining_budget(tmp_path):
    conn = store.connect(tmp_path / "q.sqlite")
    ledger = QuotaLedger(conn, budget=50)
    ledger.spend(5)
    ledger.exhaust()
    assert ledger.remaining() == 0
    assert not ledger.can_spend(1)


def test_days_are_independent_buckets(tmp_path):
    conn = store.connect(tmp_path / "q.sqlite")
    ledger = QuotaLedger(conn, budget=10)
    ledger.spend(10, day="2026-08-12")
    assert ledger.remaining(day="2026-08-12") == 0
    assert ledger.remaining(day="2026-08-13") == 10


def test_rejects_nonsense_budgets(tmp_path):
    conn = store.connect(tmp_path / "q.sqlite")
    with pytest.raises(ValueError):
        QuotaLedger(conn, budget=0)
