"""Momentum signals.

Every function takes a wide frame of daily closes (index: date, one column per
symbol) and returns a value per symbol as of the last row, so the same code
serves both the live signal table and each rebalance date in a backtest.

Lookbacks are in trading days: 21 is about a month, 252 about a year.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MONTH = 21

# Signal kinds offered in the interface, in the order they are shown.
KINDS = [
    "total_return",
    "12_1",
    "risk_adjusted",
    "moving_average",
]

KIND_LABELS = {
    "total_return": "Total return",
    "12_1": "12-1 (skip recent month)",
    "risk_adjusted": "Risk adjusted (return / vol)",
    "moving_average": "Trend (price vs moving average)",
}

KIND_HELP = {
    "total_return": "Simple return over the lookback. The plainest momentum measure.",
    "12_1": (
        "Return over the lookback but skipping the most recent month, the "
        "classic academic definition. Skipping avoids the short-term reversal "
        "that tends to follow a sharp move."
    ),
    "risk_adjusted": (
        "Lookback return divided by realized volatility, so a steady climb "
        "ranks above an equally large but erratic one."
    ),
    "moving_average": (
        "How far price sits above its moving average, as a percentage. A "
        "trend-following measure rather than a return ranking."
    ),
}


@dataclass(frozen=True)
class SignalSpec:
    kind: str = "12_1"
    lookback: int = 252
    skip: int = MONTH
    ma_window: int = 200

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def min_rows(self) -> int:
        """Rows of history needed before this signal can be computed."""
        if self.kind == "moving_average":
            return self.ma_window + 1
        if self.kind == "12_1":
            return self.lookback + 1
        return self.lookback + 1


def _pct_change_over(closes: pd.DataFrame, start_offset: int, end_offset: int) -> pd.Series:
    """Return between two points counted back from the last row.

    `start_offset` is the older point, `end_offset` the newer one, both as a
    number of rows back from the end (0 meaning the last row).
    """
    if len(closes) <= start_offset:
        return pd.Series(np.nan, index=closes.columns, dtype=float)
    start = closes.iloc[-(start_offset + 1)]
    end = closes.iloc[-(end_offset + 1)]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (end / start) - 1.0
    return out.replace([np.inf, -np.inf], np.nan).astype(float)


def total_return(closes: pd.DataFrame, lookback: int = TRADING_DAYS) -> pd.Series:
    return _pct_change_over(closes, lookback, 0)


def momentum_12_1(
    closes: pd.DataFrame, lookback: int = TRADING_DAYS, skip: int = MONTH
) -> pd.Series:
    """Return from `lookback` days ago up to `skip` days ago."""
    if skip <= 0:
        return total_return(closes, lookback)
    return _pct_change_over(closes, lookback, skip)


def risk_adjusted(closes: pd.DataFrame, lookback: int = TRADING_DAYS) -> pd.Series:
    """Lookback return divided by annualized volatility over the same window."""
    ret = total_return(closes, lookback)
    window = closes.tail(lookback + 1)
    if len(window) < 3:
        return pd.Series(np.nan, index=closes.columns, dtype=float)
    daily = np.log(window / window.shift(1)).dropna(how="all")
    vol = daily.std() * np.sqrt(TRADING_DAYS)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = ret / vol.replace(0.0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan).astype(float)


def moving_average_gap(closes: pd.DataFrame, window: int = 200) -> pd.Series:
    """Percentage distance of the latest price above its moving average."""
    if len(closes) < 2:
        return pd.Series(np.nan, index=closes.columns, dtype=float)
    effective = min(window, len(closes))
    ma = closes.tail(effective).mean()
    last = closes.iloc[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (last / ma.replace(0.0, np.nan)) - 1.0
    return out.replace([np.inf, -np.inf], np.nan).astype(float)


def compute(closes: pd.DataFrame, spec: SignalSpec) -> pd.Series:
    """Dispatch to the signal named by `spec`, as of the last row of `closes`."""
    if closes is None or closes.empty:
        return pd.Series(dtype=float)
    if spec.kind == "total_return":
        return total_return(closes, spec.lookback)
    if spec.kind == "12_1":
        return momentum_12_1(closes, spec.lookback, spec.skip)
    if spec.kind == "risk_adjusted":
        return risk_adjusted(closes, spec.lookback)
    if spec.kind == "moving_average":
        return moving_average_gap(closes, spec.ma_window)
    return total_return(closes, spec.lookback)


def absolute_filter(closes: pd.DataFrame, spec: SignalSpec, threshold: float = 0.0):
    """Which symbols pass absolute (time series) momentum.

    Cross-sectional ranking alone will always hold something, even when every
    candidate is falling. This filter is what lets a rotation sit in cash.
    """
    scores = compute(closes, spec)
    return scores > threshold


def rank(scores: pd.Series) -> pd.Series:
    """Cross-sectional ranking, 1 being the strongest. NaN scores rank last."""
    return scores.rank(ascending=False, na_option="bottom", method="min")


def select_top(scores: pd.Series, top_n: int, require_positive: bool = True) -> list[str]:
    """The `top_n` strongest symbols, optionally dropping non-positive scores."""
    valid = scores.dropna()
    if require_positive:
        valid = valid[valid > 0]
    if valid.empty:
        return []
    return list(valid.sort_values(ascending=False).head(max(0, top_n)).index)
