"""Backtest engine, with particular attention to lookahead bias.

A backtest that peeks at future prices will always look brilliant and always
be worthless, so the invariance test below is the most important one here.
"""

import numpy as np
import pandas as pd
import pytest

from stooq_cli import backtest, signals

SHORT = signals.SignalSpec(kind="total_return", lookback=60, skip=0)


def prices(spec: dict, n: int = 500, seed: int = 0) -> pd.DataFrame:
    """Geometric series with per-asset drift and volatility."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    cols = {}
    for name, (drift, vol) in spec.items():
        steps = rng.normal(drift, vol, n)
        cols[name] = 100 * np.exp(np.cumsum(steps))
    return pd.DataFrame(cols, index=idx)


def test_no_lookahead_truncation_invariance():
    """The decisive test: results before a cutoff must not change when data
    after that cutoff is added. If future prices leaked into past decisions,
    these two runs would differ."""
    full = prices({"A": (0.0008, 0.012), "B": (0.0002, 0.010), "C": (-0.0003, 0.015)}, n=600)
    cutoff = full.index[400]
    truncated = full.loc[:cutoff]

    run_full = backtest.run(full, strategy="rotation", method="equal", spec=SHORT, top_n=2)
    run_trunc = backtest.run(truncated, strategy="rotation", method="equal", spec=SHORT, top_n=2)

    assert run_full.ok and run_trunc.ok
    overlap = run_trunc.returns.index.intersection(run_full.returns.index)
    assert len(overlap) > 100, "not enough overlap to be a meaningful check"
    pd.testing.assert_series_equal(
        run_full.returns.loc[overlap],
        run_trunc.returns.loc[overlap],
        check_names=False,
        rtol=1e-12,
    )


def test_weights_are_applied_the_day_after_they_are_set():
    """Weights decided at the close of day t must earn day t+1's return."""
    frame = prices({"A": (0.001, 0.01), "B": (0.0005, 0.01)}, n=400)
    result = backtest.run(frame, strategy="buy_and_hold", method="equal", spec=SHORT)
    daily = frame.pct_change().fillna(0.0)
    expected = (
        result.weights_history.shift(1).fillna(0.0) * daily.loc[result.weights_history.index]
    ).sum(axis=1)
    pd.testing.assert_series_equal(result.returns, expected, check_names=False)


def test_rotation_goes_to_cash_when_everything_falls():
    """The absolute momentum filter must be able to hold nothing at all."""
    falling = prices({"A": (-0.002, 0.008), "B": (-0.003, 0.008), "C": (-0.0025, 0.008)}, n=500)
    result = backtest.run(
        falling, strategy="rotation", method="equal", spec=SHORT, top_n=2, absolute_filter=True
    )
    assert result.ok
    assert result.stats["cash_share"] > 0.8, "should be mostly in cash"
    assert abs(result.stats["total_return"]) < 0.05, "cash should not move much"


def test_rotation_without_filter_stays_invested_while_falling():
    """Contrast with the previous test: ranking alone always holds something."""
    falling = prices({"A": (-0.002, 0.008), "B": (-0.003, 0.008), "C": (-0.0025, 0.008)}, n=500)
    result = backtest.run(
        falling, strategy="rotation", method="equal", spec=SHORT, top_n=2, absolute_filter=False
    )
    assert result.stats["cash_share"] < 0.2
    assert result.stats["total_return"] < 0, "fully invested in falling assets must lose"


def test_rotation_picks_the_strong_asset():
    frame = prices({"WINNER": (0.0015, 0.008), "LOSER": (-0.0015, 0.008)}, n=500)
    result = backtest.run(frame, strategy="rotation", method="equal", spec=SHORT, top_n=1)
    held = result.weights_history
    assert held["WINNER"].mean() > held["LOSER"].mean()
    assert result.stats["total_return"] > 0


def test_buy_and_hold_holds_everything():
    frame = prices({"A": (0.0008, 0.01), "B": (0.0004, 0.01), "C": (0.0006, 0.01)}, n=500)
    result = backtest.run(frame, strategy="buy_and_hold", method="equal", spec=SHORT)
    assert result.stats["avg_positions"] == pytest.approx(3.0, abs=0.01)
    assert result.stats["cash_share"] == pytest.approx(0.0, abs=1e-6)


def test_rotation_turns_over_more_than_buy_and_hold():
    frame = prices({"A": (0.001, 0.02), "B": (0.001, 0.02), "C": (0.001, 0.02)}, n=600, seed=5)
    rot = backtest.run(frame, strategy="rotation", method="equal", spec=SHORT, top_n=1)
    bnh = backtest.run(frame, strategy="buy_and_hold", method="equal", spec=SHORT)
    assert rot.stats["turnover_per_year"] > bnh.stats["turnover_per_year"]


def test_equity_curve_is_consistent_with_returns():
    frame = prices({"A": (0.0008, 0.01), "B": (0.0004, 0.012)}, n=500)
    result = backtest.run(frame, strategy="buy_and_hold", method="equal", spec=SHORT)
    rebuilt = (1.0 + result.returns).cumprod()
    pd.testing.assert_series_equal(result.equity, rebuilt, check_names=False)


def test_stats_are_sane():
    frame = prices({"A": (0.0008, 0.01), "B": (0.0004, 0.012)}, n=600)
    stats = backtest.run(frame, strategy="buy_and_hold", method="equal", spec=SHORT).stats
    assert stats["vol"] > 0
    assert -1.0 <= stats["max_drawdown"] <= 0.0
    assert 0.0 <= stats["hit_rate"] <= 1.0
    assert stats["years"] > 0
    assert stats["observations"] > 0


def test_frequency_changes_rebalance_count():
    frame = prices({"A": (0.0008, 0.01), "B": (0.0004, 0.012)}, n=600)
    monthly = backtest.run(frame, spec=SHORT, frequency="ME", method="equal")
    yearly = backtest.run(frame, spec=SHORT, frequency="YE", method="equal")
    assert monthly.rebalances > yearly.rebalances


@pytest.mark.parametrize("method", ["equal", "inverse_vol", "risk_parity", "min_variance"])
def test_every_weighting_method_runs_in_a_backtest(method):
    frame = prices({"A": (0.0008, 0.01), "B": (0.0004, 0.012), "C": (0.0006, 0.02)}, n=500)
    result = backtest.run(frame, strategy="rotation", method=method, spec=SHORT, top_n=2)
    assert result.ok
    assert result.weights_history.notna().all().all()
    # Long-only: never short, never levered.
    assert (result.weights_history >= -1e-9).all().all()
    assert (result.weights_history.sum(axis=1) <= 1.0 + 1e-6).all()


def test_insufficient_history_reports_instead_of_crashing():
    frame = prices({"A": (0.001, 0.01)}, n=30)
    result = backtest.run(frame, spec=signals.SignalSpec(lookback=252))
    assert not result.ok
    assert "Not enough history" in result.note


def test_empty_input_reports_instead_of_crashing():
    result = backtest.run(pd.DataFrame())
    assert not result.ok
    assert result.note


def test_single_asset_backtest_works():
    frame = prices({"ONLY": (0.0008, 0.01)}, n=500)
    result = backtest.run(frame, strategy="rotation", method="equal", spec=SHORT, top_n=1)
    assert result.ok
