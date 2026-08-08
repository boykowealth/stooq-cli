"""Request thrift: the app must not spend more of Stooq's allowance than the
view actually needs, and must stop before the quota rather than after it."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from stooq_cli import store
from stooq_cli.budget import RequestBudget
from stooq_cli.client import StooqQuotaExceeded
from stooq_cli.store import BudgetExhausted


class CountingClient:
    """Returns one full page of bars per request and counts the calls."""

    def __init__(self, total_pages: int = 40):
        self.calls = 0
        self.total_pages = total_pages

    def quote_history_page(self, symbol, d1, d2, page):
        self.calls += 1
        return f"PAGE {page}"


def fake_parse_factory(total_pages: int, start: date):
    def fake_parse(html):
        page = int(html.split()[1])
        base = start + timedelta(days=40 * (page - 1))
        bars = [
            type(
                "Bar",
                (),
                {
                    "day": base + timedelta(days=i),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 100.0,
                    "volume": 1.0,
                },
            )()
            for i in range(40)
        ]
        return bars, total_pages

    return fake_parse


@pytest.fixture
def cache(tmp_path):
    with patch("stooq_cli.store.cache_dir", return_value=str(tmp_path)):
        yield tmp_path


def test_short_range_costs_far_less_than_a_long_one(cache):
    """The regression that caused lockouts: a one month chart used to pull a
    full year. It must now cost a single page."""
    start = store.start_for_days(30)
    client = CountingClient(total_pages=1)
    with patch("stooq_cli.store.parse_history", side_effect=fake_parse_factory(1, start)):
        store.get_history(client, "short.f", start=start)
    assert client.calls == 1


def test_budget_stops_the_page_loop(cache):
    budget = RequestBudget(str(cache / "budget.json"))
    budget.set_limit(3)
    start = store.start_for_days(2000)
    client = CountingClient(total_pages=40)
    with patch("stooq_cli.store.parse_history", side_effect=fake_parse_factory(40, start)):
        store.get_history(client, "long.f", start=start, budget=budget)
    assert client.calls == 3  # stopped at the cap, not at page 40
    assert budget.remaining == 0


def test_budget_exhausted_with_no_cache_raises(cache):
    budget = RequestBudget(str(cache / "budget.json"))
    budget.set_limit(1)
    assert budget.try_spend(1)  # use it all up first
    client = CountingClient()
    with pytest.raises(BudgetExhausted):
        store.get_history(client, "fresh.f", start=store.start_for_days(365), budget=budget)
    assert client.calls == 0  # nothing was requested from Stooq


class BlockingClient:
    def __init__(self):
        self.calls = 0

    def quote_history_page(self, symbol, d1, d2, page):
        self.calls += 1
        raise StooqQuotaExceeded("limit")


def test_stooq_block_after_real_use_lowers_the_cap(cache):
    """A refusal that follows meaningful spending is a genuine signal about
    Stooq's limit, so future days must budget below it."""
    budget = RequestBudget(str(cache / "budget.json"))
    budget.try_spend(40)  # a day's real work before the refusal

    with pytest.raises(StooqQuotaExceeded):
        store.get_history(
            BlockingClient(), "blocked.f", start=store.start_for_days(365), budget=budget
        )
    assert budget.blocked_today
    assert budget.effective_limit < 120


def test_stooq_block_on_first_request_is_not_learned_from(cache):
    """Refusal at near-zero spend means Stooq's day has not rolled over, not
    that our limit is tiny. Learning from it would brick the app."""
    budget = RequestBudget(str(cache / "budget.json"))

    with pytest.raises(StooqQuotaExceeded):
        store.get_history(
            BlockingClient(), "blocked.f", start=store.start_for_days(365), budget=budget
        )
    assert budget.blocked_today
    assert budget.effective_limit == 120  # unchanged, still usable tomorrow


def test_estimate_matches_actual_cost_when_uncached(cache):
    start = store.start_for_days(365)
    estimate = store.estimate_pages("uncached.f", start)
    client = CountingClient(total_pages=estimate)
    with patch(
        "stooq_cli.store.parse_history", side_effect=fake_parse_factory(estimate, start)
    ):
        store.get_history(client, "uncached.f", start=start)
    assert client.calls == estimate


def test_estimate_is_zero_when_fully_cached(cache):
    """A basket that is already cached must be free, so no confirmation is
    ever shown for it."""
    today = date.today()
    frame = store.pd.DataFrame(
        {
            "date": [today - timedelta(days=i) for i in range(400)][::-1],
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )
    store._save_cached("cached.f", frame)
    store._save_meta("cached.f", {"earliest": (today - timedelta(days=400)).isoformat()})
    assert store.estimate_pages("cached.f", store.start_for_days(365)) == 0


def test_cached_symbol_makes_no_requests(cache):
    today = date.today()
    frame = store.pd.DataFrame(
        {
            "date": [today - timedelta(days=i) for i in range(400)][::-1],
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )
    store._save_cached("cached.f", frame)
    store._save_meta("cached.f", {"earliest": (today - timedelta(days=400)).isoformat()})
    client = CountingClient()
    df = store.get_history(client, "cached.f", start=store.start_for_days(365))
    assert client.calls == 0
    assert not df.empty
