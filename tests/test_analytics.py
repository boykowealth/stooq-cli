"""Analytics tests on synthetic data."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stooq_cli import analytics


def make_history(seed: int, n: int = 600, drift: float = 0.0002) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, n)
    closes = 100.0 * np.exp(np.cumsum(rets))
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "date": days,
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": 1000,
        }
    )


@pytest.fixture
def closes():
    hists = {"aaa": make_history(1), "bbb": make_history(2), "ccc": make_history(3)}
    return analytics.align_closes(hists)


def test_align_closes(closes):
    assert list(closes.columns) == ["AAA", "BBB", "CCC"]
    assert len(closes) == 600
    assert closes.index.is_monotonic_increasing


def test_align_closes_partial_overlap():
    a = make_history(1, n=300)
    b = make_history(2, n=600)
    joined = analytics.align_closes({"a": a, "b": b})
    assert len(joined) == 300


def test_align_closes_empty():
    assert analytics.align_closes({}).empty
    assert analytics.align_closes({"a": pd.DataFrame()}).empty


def test_corr_matrix(closes):
    rets = analytics.log_returns(closes)
    corr = analytics.corr_matrix(rets)
    assert corr.shape == (3, 3)
    assert np.allclose(np.diag(corr), 1.0)
    assert (corr.abs() <= 1.0 + 1e-9).all().all()


def test_perfect_correlation():
    base = make_history(7)
    double = base.copy()
    double["close"] = base["close"] * 2.0
    closes = analytics.align_closes({"x": base, "y": double})
    corr = analytics.corr_matrix(analytics.log_returns(closes))
    assert corr.loc["X", "Y"] == pytest.approx(1.0)


def test_rolling_corr(closes):
    rets = analytics.log_returns(closes)
    roll = analytics.rolling_corr(rets, 60)
    assert list(roll.columns) == ["AAA / BBB", "AAA / CCC", "BBB / CCC"]
    assert len(roll) > 0
    finite = roll.dropna()
    assert (finite.abs() <= 1.0 + 1e-9).all().all()


def test_garch_vol(closes):
    rets = analytics.log_returns(closes)
    series, params = analytics.garch_vol(rets["AAA"])
    assert len(series) == len(rets)
    assert (series > 0).all()
    assert 0 <= params["alpha"] <= 1
    assert 0 <= params["beta"] <= 1
    # Annualized vol should be in a sane band for 1 percent daily noise.
    assert 5 < series.mean() < 40


def test_summary_stats(closes):
    stats = analytics.summary_stats(closes)
    assert len(stats) == 3
    assert set(stats["symbol"]) == {"AAA", "BBB", "CCC"}
    assert (stats["ann_vol_pct"] > 0).all()
    assert (stats["max_dd_pct"] <= 0).all()
    assert (stats["obs"] == 599).all()


def test_max_drawdown_known_value():
    closes = pd.Series([100.0, 120.0, 60.0, 90.0])
    assert analytics.max_drawdown(closes) == pytest.approx(-0.5)
