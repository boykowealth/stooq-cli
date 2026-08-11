"""Momentum signal correctness, checked against hand-computable series."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stooq_cli import signals


def frame(series: dict, n: int) -> pd.DataFrame:
    idx = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame(series, index=pd.to_datetime(idx))


def test_total_return_is_exact():
    closes = frame({"A": np.linspace(100.0, 200.0, 101)}, 101)
    got = signals.total_return(closes, lookback=100)
    assert got["A"] == pytest.approx(1.0)  # doubled


def test_12_1_skips_the_recent_month():
    """The last 21 days must not affect a 12-1 score."""
    base = list(np.linspace(100.0, 150.0, 200))
    calm = base + [150.0] * 21
    spike = base + [900.0] * 21
    a = frame({"A": calm}, len(calm))
    b = frame({"A": spike}, len(spike))
    score_a = signals.momentum_12_1(a, lookback=200, skip=21)
    score_b = signals.momentum_12_1(b, lookback=200, skip=21)
    assert score_a["A"] == pytest.approx(score_b["A"])


def test_total_return_does_see_the_recent_month():
    """Sanity check that the previous test is testing something real."""
    base = list(np.linspace(100.0, 150.0, 200))
    a = frame({"A": base + [150.0] * 21}, 221)
    b = frame({"A": base + [900.0] * 21}, 221)
    assert signals.total_return(a, 220)["A"] != pytest.approx(
        signals.total_return(b, 220)["A"]
    )


def test_risk_adjusted_prefers_the_steadier_path():
    rng = np.random.default_rng(0)
    n = 253
    steady = 100 * np.exp(np.cumsum(np.full(n, 0.001)))
    noise = rng.normal(0, 0.05, n)
    erratic = 100 * np.exp(np.cumsum(np.full(n, 0.001) + noise - noise.mean()))
    closes = frame({"STEADY": steady, "ERRATIC": erratic}, n)
    scores = signals.risk_adjusted(closes, lookback=252)
    assert scores["STEADY"] > scores["ERRATIC"]


def test_moving_average_gap_sign():
    rising = frame({"A": np.linspace(100.0, 200.0, 210)}, 210)
    falling = frame({"A": np.linspace(200.0, 100.0, 210)}, 210)
    assert signals.moving_average_gap(rising, 200)["A"] > 0
    assert signals.moving_average_gap(falling, 200)["A"] < 0


def test_rank_orders_strongest_first():
    scores = pd.Series({"A": 0.5, "B": -0.2, "C": 0.9})
    ranks = signals.rank(scores)
    assert ranks["C"] == 1
    assert ranks["A"] == 2
    assert ranks["B"] == 3


def test_select_top_respects_positive_filter():
    scores = pd.Series({"A": 0.5, "B": -0.2, "C": 0.9, "D": -0.8})
    assert signals.select_top(scores, 3, require_positive=True) == ["C", "A"]
    assert signals.select_top(scores, 3, require_positive=False) == ["C", "A", "B"]


def test_select_top_can_return_nothing():
    """Everything falling must be allowed to mean 'hold cash'."""
    scores = pd.Series({"A": -0.5, "B": -0.2})
    assert signals.select_top(scores, 2, require_positive=True) == []


def test_signals_survive_short_and_empty_input():
    assert signals.compute(pd.DataFrame(), signals.SignalSpec()).empty
    tiny = frame({"A": [100.0, 101.0]}, 2)
    for kind in signals.KINDS:
        out = signals.compute(tiny, signals.SignalSpec(kind=kind, lookback=252))
        assert len(out) == 1  # a value per symbol, NaN is acceptable


def test_nan_scores_rank_last_not_first():
    scores = pd.Series({"A": np.nan, "B": 0.1})
    assert signals.select_top(scores, 2, require_positive=True) == ["B"]
