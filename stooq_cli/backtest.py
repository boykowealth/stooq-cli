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


@dataclass(frozen=True)
class CostModel:
    """What trading actually costs.

    Commission and slippage are charged in basis points on the notional
    traded, so a rebalance that moves 40 percent of the portfolio pays the
    combined rate on 0.40. Defaults are realistic for a retail account in
    liquid instruments; illiquid or small-cap names cost considerably more.

    Tax is deliberately zero by default. When set, it is charged on realized
    gains at each rebalance, approximated as the share of the portfolio being
    sold multiplied by the gain since the previous rebalance. There is no lot
    tracking, no distinction between short and long term rates, and no loss
    carryforward, so treat it as an order of magnitude, not a tax return.
    """

    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    tax_rate: float = 0.0

    @property
    def rate(self) -> float:
        """Combined cost per unit of notional traded."""
        return max(0.0, (self.commission_bps + self.slippage_bps) / 10_000.0)

    @property
    def is_free(self) -> bool:
        return self.rate <= 0 and self.tax_rate <= 0

    def describe(self) -> str:
        parts = [f"{self.commission_bps:g}bp commission", f"{self.slippage_bps:g}bp slippage"]
        if self.tax_rate > 0:
            parts.append(f"{self.tax_rate:.0%} tax")
        return ", ".join(parts)

    def short(self) -> str:
        """Compact form for the config strip, where space is tight."""
        combined = self.commission_bps + self.slippage_bps
        text = f"{combined:g}bp"
        if self.tax_rate > 0:
            text += f" +{self.tax_rate:.0%} tax"
        return text


DEFAULT_COSTS = CostModel()


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


def performance_stats(returns: pd.Series, equity: pd.Series, initial: float = 1.0) -> dict:
    """Standard summary of a return stream. Returns are simple, not log.

    Total return is compounded from the return stream rather than read off the
    equity curve's endpoints, because the curve's first value already contains
    the first day's return and dividing by it would silently discard that day.
    """
    if returns.empty or equity.empty:
        return {}
    periods = len(returns)
    years = periods / TRADING_DAYS
    total = float((1.0 + returns).prod())
    cagr = total ** (1 / years) - 1.0 if years > 0 and total > 0 else float("nan")
    vol = float(returns.std() * np.sqrt(TRADING_DAYS))
    sharpe = (float(returns.mean() * TRADING_DAYS) / vol) if vol > 0 else float("nan")
    # The book started at `initial`, so the high water mark is never below it.
    running_max = equity.cummax().clip(lower=initial)
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
    costs: CostModel | None = None,
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

    costs = costs or DEFAULT_COSTS
    active = dates[start:]
    schedule = dict(weights_by_date.items())
    columns = frame.columns

    # Weights in force during each day, which is what earns that day's return.
    # They were decided at the previous close, so this stays point in time.
    held = pd.DataFrame(0.0, index=active, columns=columns)
    gross = pd.Series(0.0, index=active, dtype=float)
    cost_drag = pd.Series(0.0, index=active, dtype=float)
    tax_drag = pd.Series(0.0, index=active, dtype=float)

    current = weights_by_date[candidates[0]].reindex(columns).fillna(0.0)
    traded_total = float(current.abs().sum())

    equity = 1.0
    equity_at_last_rebalance = 1.0
    equity_curve = pd.Series(0.0, index=active, dtype=float)

    # Costs are charged on the day the new position starts earning, matching
    # the convention that weights decided at a close take effect next day. A
    # rebalance on the final day of history therefore costs nothing, because
    # the position it would have opened never existed.
    pending_cost = traded_total * costs.rate
    pending_tax = 0.0

    for day in active:
        if pending_cost or pending_tax:
            before = equity
            equity *= max(0.0, 1.0 - pending_cost - pending_tax)
            realized = (equity / before - 1.0) if before else 0.0
            split = pending_cost + pending_tax
            if split > 0:
                cost_drag.loc[day] = realized * (pending_cost / split)
                tax_drag.loc[day] = realized * (pending_tax / split)
            equity_at_last_rebalance = equity
            pending_cost = pending_tax = 0.0

        held.loc[day] = current.values
        day_returns = daily.loc[day]
        gross_return = float((current * day_returns).sum())
        gross.loc[day] = gross_return
        equity *= 1.0 + gross_return

        # Positions drift with prices between rebalances. Cash earns nothing,
        # so it simply becomes a larger or smaller share of the total.
        denominator = 1.0 + gross_return
        if abs(denominator) > 1e-12:
            current = (current * (1.0 + day_returns)) / denominator
            current = current.reindex(columns).fillna(0.0)

        if day in schedule:
            target = schedule[day].reindex(columns).fillna(0.0)
            traded = float((target - current).abs().sum())
            traded_total += traded
            pending_cost = traded * costs.rate

            if costs.tax_rate > 0 and equity_at_last_rebalance > 0:
                gain = equity / equity_at_last_rebalance - 1.0
                if gain > 0:
                    # Only the part of the book being sold realizes a gain.
                    sold = float((current - target).clip(lower=0.0).sum())
                    pending_tax = costs.tax_rate * sold * gain
            current = target

        equity_curve.loc[day] = equity

    net_returns = equity_curve.pct_change()
    if len(equity_curve) > 0:
        net_returns.iloc[0] = float(equity_curve.iloc[0] - 1.0)

    stats = performance_stats(net_returns, equity_curve)
    years = stats.get("years") or float("nan")
    # Turnover is conventionally one-way, so half the notional traded.
    stats["turnover_per_year"] = (traded_total / 2.0) / years if years else float("nan")
    stats["rebalances"] = len(candidates)
    stats["avg_positions"] = float((held.abs() > 1e-6).sum(axis=1).mean())
    stats["cash_share"] = float((1.0 - held.abs().sum(axis=1)).clip(lower=0).mean())

    gross_equity = (1.0 + gross).cumprod()
    stats["gross_total_return"] = float(gross_equity.iloc[-1] - 1.0)
    stats["cost_drag"] = float(stats["gross_total_return"] - stats["total_return"])
    stats["costs_paid"] = float(-cost_drag.sum())
    stats["tax_paid"] = float(-tax_drag.sum())
    stats["traded_notional"] = traded_total
    stats["cost_model"] = costs.describe()

    return BacktestResult(
        equity=equity_curve,
        returns=net_returns,
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
