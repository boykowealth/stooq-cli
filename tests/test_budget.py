"""The self-imposed daily request cap."""

import json
from datetime import date, timedelta

from stooq_cli.budget import (
    DEFAULT_DAILY_LIMIT,
    MIN_LEARNED_LIMIT,
    RESERVE_FRACTION,
    RequestBudget,
)


def make(tmp_path, **state) -> RequestBudget:
    path = tmp_path / "budget.json"
    if state:
        state.setdefault("day", date.today().isoformat())
        path.write_text(json.dumps(state))
    return RequestBudget(str(path))


def test_starts_empty(tmp_path):
    b = make(tmp_path)
    assert b.spent == 0
    assert b.effective_limit == DEFAULT_DAILY_LIMIT
    assert b.remaining == DEFAULT_DAILY_LIMIT


def test_spending_accumulates_and_persists(tmp_path):
    b = make(tmp_path)
    assert b.try_spend(5)
    assert b.spent == 5
    reopened = RequestBudget(str(tmp_path / "budget.json"))
    assert reopened.spent == 5


def test_cap_refuses_overspend(tmp_path):
    b = make(tmp_path, spent=0, limit=10)
    assert b.try_spend(10)
    assert not b.try_spend(1)
    assert b.spent == 10
    assert b.remaining == 0


def test_partial_request_is_all_or_nothing(tmp_path):
    b = make(tmp_path, spent=8, limit=10)
    assert not b.try_spend(5)  # would exceed, so nothing is taken
    assert b.spent == 8
    assert b.try_spend(2)


def test_rolls_over_on_a_new_day(tmp_path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    b = make(tmp_path, day=yesterday, spent=99, limit=120)
    assert b.spent == 0
    assert b.remaining == 120


def test_observed_limit_survives_rollover_and_lowers_cap(tmp_path):
    """Learning Stooq's real limit is the whole point, so it must outlive
    the daily reset."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    b = make(tmp_path, day=yesterday, spent=50, limit=120, observed_limit=50)
    assert b.spent == 0
    assert b.effective_limit == int(50 * RESERVE_FRACTION)
    assert b.effective_limit < 50  # a reserve is always kept


def test_record_block_learns_the_limit(tmp_path):
    b = make(tmp_path, spent=0, limit=120)
    b.try_spend(40)
    b.record_block()
    assert b.blocked_today
    assert b.effective_limit == int(40 * RESERVE_FRACTION)


def test_record_block_keeps_the_lowest_observation(tmp_path):
    b = make(tmp_path, spent=0, limit=200, observed_limit=60)
    b.try_spend(40)
    b.record_block()
    reopened = RequestBudget(str(tmp_path / "budget.json"))
    assert reopened._state.observed_limit == 40


def test_block_at_zero_spend_does_not_brick_the_budget(tmp_path):
    """Stooq's quota day rolls over on its own clock, so the first request of
    a local day can be refused on yesterday's allowance. Learning from that
    would cap us near zero forever."""
    b = make(tmp_path, spent=0, limit=120)
    b.record_block()
    assert b.blocked_today
    assert b._state.observed_limit is None
    assert b.effective_limit == DEFAULT_DAILY_LIMIT

    # Tomorrow must start clean rather than crippled.
    path = tmp_path / "budget.json"
    data = json.loads(path.read_text())
    data["day"] = (date.today() - timedelta(days=1)).isoformat()
    path.write_text(json.dumps(data))
    tomorrow = RequestBudget(str(path))
    assert tomorrow.remaining == DEFAULT_DAILY_LIMIT
    assert not tomorrow.blocked_today


def test_learned_limit_never_falls_below_a_usable_floor(tmp_path):
    b = make(tmp_path, spent=0, limit=120)
    b.try_spend(6)
    b.record_block()
    assert b._state.observed_limit >= MIN_LEARNED_LIMIT


def test_user_limit_cannot_exceed_observed_reserve(tmp_path):
    b = make(tmp_path, spent=0, limit=120, observed_limit=50)
    b.set_limit(300)
    assert b.effective_limit == int(50 * RESERVE_FRACTION)


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text("{broken")
    b = RequestBudget(str(path))
    assert b.spent == 0
    assert b.effective_limit == DEFAULT_DAILY_LIMIT
