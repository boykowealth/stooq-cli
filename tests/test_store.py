"""History cache behaviour, including degradation when the quota is spent."""

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from stooq_cli import store
from stooq_cli.client import StooqQuotaExceeded


@pytest.fixture
def cache(tmp_path):
    with patch("stooq_cli.store.cache_dir", return_value=str(tmp_path)):
        yield tmp_path


def make_frame(start: date, days: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(days)],
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": [100.0 + i for i in range(days)],
            "volume": 10.0,
        }
    )


def test_roundtrip_cache(cache):
    frame = make_frame(date(2026, 1, 1), 10)
    store._save_cached("cl.f", frame)
    loaded = store._load_cached("cl.f")
    assert len(loaded) == 10
    assert loaded["date"].iloc[0] == date(2026, 1, 1)
    assert loaded["close"].iloc[-1] == 109.0


def test_missing_cache_returns_none(cache):
    assert store._load_cached("nothing.f") is None


def test_symbols_with_caret_get_safe_filenames(cache):
    frame = make_frame(date(2026, 1, 1), 3)
    store._save_cached("^spx", frame)
    assert store._load_cached("^spx") is not None


def test_quota_falls_back_to_cache(cache):
    """A spent quota must still serve what was cached earlier."""
    recent = date.today() - timedelta(days=400)
    store._save_cached("cl.f", make_frame(recent, 300))

    with patch.object(
        store, "_fetch_range", side_effect=StooqQuotaExceeded("limit")
    ):
        df = store.get_history(object(), "cl.f", years=2)

    assert not df.empty
    assert len(df) == 300


def test_quota_with_no_cache_raises(cache):
    with patch.object(
        store, "_fetch_range", side_effect=StooqQuotaExceeded("limit")
    ):
        with pytest.raises(StooqQuotaExceeded):
            store.get_history(object(), "unknown.f", years=2)


def test_partial_pages_kept_when_quota_hits_midway(cache):
    """Pages fetched before the limit was hit must not be discarded."""
    calls = {"n": 0}

    class FakeClient:
        def quote_history_page(self, symbol, d1, d2, page):
            calls["n"] += 1
            if calls["n"] > 2:
                raise StooqQuotaExceeded("limit")
            return "PAGE"

    def fake_parse(html):
        base = date(2026, 1, 1) + timedelta(days=40 * (calls["n"] - 1))
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
        return bars, 7

    with patch("stooq_cli.store.parse_history", side_effect=fake_parse):
        df = store._fetch_range(
            FakeClient(), "cl.f", date(2026, 1, 1), date(2026, 6, 1), None
        )

    assert len(df) == 80


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    with patch("stooq_cli.store.STATE_PATH", str(path)), patch(
        "stooq_cli.store.data_dir", return_value=str(tmp_path)
    ):
        state = store.AppState()
        state.watchlist = [{"symbol": "cl.f", "name": "Crude Oil WTI"}]
        state.basket = ["cl.f", "gc.f"]
        state.theme = "stooq-dark"
        store.save_state(state)
        loaded = store.load_state()
    assert loaded.theme == "stooq-dark"
    assert loaded.basket == ["cl.f", "gc.f"]
    assert loaded.watchlist_symbols() == ["cl.f"]


def test_corrupt_state_falls_back_to_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    with patch("stooq_cli.store.STATE_PATH", str(path)):
        state = store.load_state()
    assert state.theme == "stooq-light"
    assert state.basket == []
