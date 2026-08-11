"""Strategy replay.

Two strategies share one engine:

  buy_and_hold  weights every asset once per rebalance and holds
  rotation      ranks by momentum each rebalance, holds the top N, and sits
                in cash for any slot whose absolute momentum has turned down

The engine is strictly point in time. At each rebalance date the signal and
covariance see only rows up to and including that date, and the resulting
weights earn returns from the *following* day onward. Getting this wrong is
the classic way a backtest flatters itself, so `tests/test_backtest.py` pins
the behaviour with a series that is only predictable in hindsight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import portfolio, signals

TRADING_DAYS = 252

STRATEGIES = ["buy_and_hold", "rotation"]

STRATEGY_LABELS = {
    "buy_and_hold": "Buy and hold with rebalancing",
    "rotation": "Momentum rotation",
}

STRATEGY_HELP = {
    "buy_and_hold": (
        "Holds every symbol in the basket, restoring target weights at each "
        "rebalance. Drift between rebalances is left alone."
    ),
    "rotation": (
        "Ranks the basket by momentum at each rebalance and holds only the "
        "strongest N. Symbols whose absolute momentum has turned negative are "
        "dropped to cash rather than merely underweighted."
    ),
}

FREQUENCIES = ["ME", "QE", "YE"]

FREQUENCY_LABELS = {
    "ME": "Monthly",
    "QE": "Quarterly",
    "YE": "Yearly",
}


@dataclass
class BacktestResult:
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    weights_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    stats: dict = field(default_factory=dict)
    rebalances: int = 0
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.equity.empty


def rebalance_dates(index: pd.DatetimeIndex, frequency: str) -> list:
    """The last available trading day of each period."""
    if len(index) == 0:
        return []
    series = pd.Series(range(len(index)), index=index)
    try:
        grouped = series.resample(frequency).last().dropna()
    except (ValueError, TypeError):
        return [index[-1]]
    return [index[int(i)] for i in grouped.values]


def performance_stats(returns: pd.Series, equity: pd.Series) -> dict:
    """Standard summary of a return stream. Returns are simple, not log."""
    if returns.empty or equity.empty:
        return {}
    periods = len(returns)
    years = periods / TRADING_DAYS
    total = float(equity.iloc[-1] / equity.iloc[0]) if equity.iloc[0] else 1.0
    cagr = total ** (1 / years) - 1.0 if years > 0 and total > 0 else float("nan")
    vol = float(returns.std() * np.sqrt(TRADING_DAYS))
    sharpe = (float(returns.mean() * TRADING_DAYS) / vol) if vol > 0 else float("nan")
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd = float(drawdown.min())
    downside = returns[returns < 0]
    downside_vol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = (
        (float(returns.mean() * TRADING_DAYS) / downside_vol) if downside_vol > 0 else float("nan")
    )
    calmar = (cagr / abs(max_dd)) if max_dd < 0 and cagr == cagr else float("nan")
    return {
        "total_return": total - 1.0,
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "best_day": float(returns.max()),
        "worst_day": float(returns.min()),
        "hit_rate": float((returns > 0).mean()),
        "years": years,
        "observations": periods,
    }


def _target_weights(
    window: pd.DataFrame,
    strategy: str,
    method: str,
    spec: signals.SignalSpec,
    top_n: int,
    long_only: bool,
    max_weight: float,
    overlay: str,
    vol_target: float,
    var_target: float,
    absolute_filter: bool,
) -> pd.Series:
    """Weights decided from `window`, which ends at the rebalance date."""
    returns = np.log(window / window.shift(1)).dropna(how="any")
    if returns.empty:
        return pd.Series(0.0, index=window.columns)

    scores = signals.compute(window, spec)

    if strategy == "rotation":
        chosen = signals.select_top(scores, top_n, require_positive=absolute_filter)
        if not chosen:
            # Nothing qualifies, so the period is spent in cash.
            return pd.Series(0.0, index=window.columns)
        returns = returns[chosen]
        scores = scores.reindex(chosen)

    result = portfolio.compute_weights(
        method=method,
        returns=returns,
        scores=scores,
        long_only=long_only,
        max_weight=max_weight,
        overlay=overlay,
        vol_target=vol_target,
        var_target=var_target,
    )
    return result.weights.reindex(window.columns).fillna(0.0)


def run(
    closes: pd.DataFrame,
    strategy: str = "rotation",
    method: str = "equal",
    spec: signals.SignalSpec | None = None,
    top_n: int = 3,
    frequency: str = "ME",
    long_only: bool = True,
    max_weight: float = 1.0,
    overlay: str = "none",
    vol_target: float = 0.10,
    var_target: float = 0.02,
    absolute_filter: bool = True,
    progress=None,
) -> BacktestResult:
    """Replay `strategy` over `closes` and report performance.

    `closes` must be a wide frame of aligned daily closes.
    """
    spec = spec or signals.SignalSpec()
    if closes is None or closes.empty or closes.shape[1] == 0:
        return BacktestResult(note="No price history to test.")

    frame = closes.copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()

    warmup = spec.min_rows()
    if len(frame) <= warmup + 5:
        return BacktestResult(
            note=(
                f"Not enough history: this signal needs about {warmup} trading days "
                f"of warm-up and only {len(frame)} are available. Shorten the "
                "lookback or extend the history span."
            )
        )

    daily = frame.pct_change().fillna(0.0)
    dates = frame.index

    # Only rebalance once the signal has enough history behind it.
    candidates = [d for d in rebalance_dates(dates, frequency) if dates.get_loc(d) >= warmup]
    if not candidates:
        return BacktestResult(note="No rebalance dates fall after the signal warm-up period.")

    weights_by_date: dict = {}
    for i, when in enumerate(candidates):
        if progress and i % 5 == 0:
            progress(f"rebalance {i + 1}/{len(candidates)}")
        position = dates.get_loc(when)
        # Strictly point in time: the window ends at the rebalance date, and
        # these weights are applied from the next bar onward.
        window = frame.iloc[: position + 1]
        weights_by_date[when] = _target_weights(
            window, strategy, method, spec, top_n, long_only,
            max_weight, overlay, vol_target, var_target, absolute_filter,
        )

    start = dates.get_loc(candidates[0]) + 1
    if start >= len(dates):
        return BacktestResult(note="The first rebalance falls on the last day of history.")

    active = dates[start:]
    held = pd.DataFrame(0.0, index=active, columns=frame.columns)
    current = weights_by_date[candidates[0]]
    schedule = {d: w for d, w in weights_by_date.items()}
    turnover_total = 0.0
    previous = pd.Series(0.0, index=frame.columns)
    turnover_total += float((current - previous).abs().sum()) / 2.0
    previous = current

    for day in active:
        if day in schedule:
            new = schedule[day]
            turnover_total += float((new - previous).abs().sum()) / 2.0
            previous = new
            current = new
        held.loc[day] = current.values

    # Weights set at the close of day t earn day t+1's return, so shifting the
    # held frame by one day is what keeps the test honest.
    strategy_returns = (held.shift(1).fillna(0.0) * daily.loc[active]).sum(axis=1)
    equity = (1.0 + strategy_returns).cumprod()

    stats = performance_stats(strategy_returns, equity)
    stats["turnover_per_year"] = (
        turnover_total / stats["years"] if stats.get("years") else float("nan")
    )
    stats["rebalances"] = len(candidates)
    stats["avg_positions"] = float((held.abs() > 1e-6).sum(axis=1).mean())
    stats["cash_share"] = float((1.0 - held.abs().sum(axis=1)).clip(lower=0).mean())

    return BacktestResult(
        equity=equity,
        returns=strategy_returns,
        weights_history=held,
        stats=stats,
        rebalances=len(candidates),
    )


def buy_and_hold_benchmark(closes: pd.DataFrame) -> pd.Series:
    """Equal weight, never rebalanced. The honest yardstick for a strategy."""
    if closes is None or closes.empty:
        return pd.Series(dtype=float)
    frame = closes.copy()
    frame.index = pd.to_datetime(frame.index)
    normalized = frame / frame.iloc[0]
    return normalized.mean(axis=1)
