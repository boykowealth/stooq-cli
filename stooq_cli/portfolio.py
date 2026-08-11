"""Position sizing and portfolio weighting.

Each method turns a set of return series (and optionally a momentum score)
into target weights. Everything is defensive: covariance estimates are shrunk
so the optimizers stay well posed on short samples, and any optimizer that
fails to converge falls back to a simpler method and says so in `note` rather
than raising. A weighting screen should never be able to break the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

TRADING_DAYS = 252

METHODS = [
    "equal",
    "inverse_vol",
    "risk_parity",
    "min_variance",
    "max_sharpe",
    "momentum",
]

METHOD_LABELS = {
    "equal": "Equal weight",
    "inverse_vol": "Inverse volatility",
    "risk_parity": "Risk parity (equal risk contribution)",
    "min_variance": "Minimum variance",
    "max_sharpe": "Maximum Sharpe (tangency)",
    "momentum": "Momentum weighted",
}

METHOD_HELP = {
    "equal": "Every position the same size. Hard to beat and never concentrates by accident.",
    "inverse_vol": (
        "Size inversely to each asset's own volatility, so a quiet asset gets "
        "more. Ignores correlation."
    ),
    "risk_parity": (
        "Solves for weights where every position contributes the same share of "
        "portfolio risk. Accounts for correlation, unlike inverse volatility."
    ),
    "min_variance": "The lowest variance combination. Tends to concentrate in quiet, "
    "diversifying assets.",
    "max_sharpe": (
        "The tangency portfolio: highest expected return per unit of risk. "
        "Powerful but sensitive, since expected returns are estimated from a "
        "short history."
    ),
    "momentum": "Weights proportional to the momentum score, so the strongest gets the most.",
}

OVERLAYS = ["none", "vol_target", "var_target"]

OVERLAY_LABELS = {
    "none": "None (fully invested)",
    "vol_target": "Volatility target",
    "var_target": "Value at Risk target",
}

OVERLAY_HELP = {
    "none": "Weights are used as calculated, summing to fully invested.",
    "vol_target": (
        "Scales total exposure so forecast portfolio volatility matches your "
        "target, holding the remainder in cash. Never levers above 100 percent."
    ),
    "var_target": (
        "Scales exposure so the portfolio's one day 95 percent Value at Risk "
        "matches your budget, holding the remainder in cash."
    ),
}


@dataclass
class WeightResult:
    weights: pd.Series
    method: str
    cash: float = 0.0
    gross: float = 1.0
    diagnostics: dict = field(default_factory=dict)
    note: str = ""

    @property
    def invested(self) -> float:
        return float(self.weights.abs().sum())


# -- covariance --------------------------------------------------------------

def shrunk_covariance(returns: pd.DataFrame, shrinkage: float = 0.15) -> pd.DataFrame:
    """Annualized covariance, pulled toward a diagonal target.

    Sample covariance is noisy and often near-singular on the short histories
    this app works with, which makes optimizers produce wild weights. Shrinking
    toward the diagonal keeps them stable and invertible.
    """
    sample = returns.cov() * TRADING_DAYS
    if sample.empty:
        return sample
    target = pd.DataFrame(
        np.diag(np.diag(sample.values)), index=sample.index, columns=sample.columns
    )
    blended = (1.0 - shrinkage) * sample + shrinkage * target
    # A small ridge guarantees positive definiteness even in degenerate cases.
    ridge = 1e-10 * np.eye(len(blended))
    return blended + ridge


def annualized_vol(returns: pd.DataFrame) -> pd.Series:
    return returns.std() * np.sqrt(TRADING_DAYS)


def portfolio_vol(weights: pd.Series, cov: pd.DataFrame) -> float:
    w = weights.reindex(cov.index).fillna(0.0).values
    return float(np.sqrt(max(0.0, w @ cov.values @ w)))


def risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> pd.Series:
    """Each position's share of total portfolio risk."""
    w = weights.reindex(cov.index).fillna(0.0).values
    total = np.sqrt(max(1e-18, w @ cov.values @ w))
    marginal = cov.values @ w
    contrib = w * marginal / total
    return pd.Series(contrib / contrib.sum() if contrib.sum() else contrib, index=cov.index)


# -- individual methods ------------------------------------------------------

def _normalize(weights: pd.Series, long_only: bool) -> pd.Series:
    weights = weights.fillna(0.0)
    if long_only:
        weights = weights.clip(lower=0.0)
    total = weights.abs().sum()
    if total <= 0:
        return pd.Series(1.0 / max(1, len(weights)), index=weights.index)
    return weights / total


def equal_weight(symbols) -> pd.Series:
    symbols = list(symbols)
    if not symbols:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / len(symbols), index=symbols)


def inverse_vol_weight(returns: pd.DataFrame) -> pd.Series:
    vol = annualized_vol(returns).replace(0.0, np.nan)
    inv = 1.0 / vol
    if inv.isna().all():
        return equal_weight(returns.columns)
    return _normalize(inv.fillna(0.0), long_only=True)


def momentum_weight(scores: pd.Series, long_only: bool = True) -> pd.Series:
    """Weights proportional to score. With long-only, negatives drop out."""
    s = scores.copy().fillna(0.0)
    if long_only:
        s = s.clip(lower=0.0)
        if s.sum() <= 0:
            return equal_weight(scores.index)
    return _normalize(s, long_only)


def _bounds(n: int, long_only: bool, max_weight: float):
    lo = 0.0 if long_only else -max_weight
    return [(lo, max_weight)] * n


def _solve(objective, n: int, long_only: bool, max_weight: float):
    """Shared SLSQP setup: fully invested, bounded weights."""
    x0 = np.full(n, 1.0 / n)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=_bounds(n, long_only, max_weight),
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-10},
    )
    return result


def min_variance_weight(
    cov: pd.DataFrame, long_only: bool = True, max_weight: float = 1.0
) -> tuple[pd.Series, str]:
    n = len(cov)
    if n == 0:
        return pd.Series(dtype=float), "no data"
    if n == 1:
        return pd.Series([1.0], index=cov.index), ""
    matrix = cov.values

    def objective(w):
        return w @ matrix @ w

    result = _solve(objective, n, long_only, max_weight)
    if not result.success or not np.all(np.isfinite(result.x)):
        return inverse_vol_weight_from_cov(cov), "optimizer did not converge, used inverse vol"
    return pd.Series(result.x, index=cov.index), ""


def inverse_vol_weight_from_cov(cov: pd.DataFrame) -> pd.Series:
    vol = pd.Series(np.sqrt(np.diag(cov.values)), index=cov.index).replace(0.0, np.nan)
    inv = (1.0 / vol).fillna(0.0)
    return _normalize(inv, long_only=True)


def max_sharpe_weight(
    expected: pd.Series,
    cov: pd.DataFrame,
    long_only: bool = True,
    max_weight: float = 1.0,
    risk_free: float = 0.0,
) -> tuple[pd.Series, str]:
    n = len(cov)
    if n == 0:
        return pd.Series(dtype=float), "no data"
    if n == 1:
        return pd.Series([1.0], index=cov.index), ""
    mu = expected.reindex(cov.index).fillna(0.0).values
    matrix = cov.values

    if long_only and np.all(mu <= risk_free):
        # No asset beats cash, so a tangency portfolio is not meaningful.
        return min_variance_weight(cov, long_only, max_weight)[0], (
            "no asset beat the risk free rate, used minimum variance"
        )

    def negative_sharpe(w):
        ret = w @ mu - risk_free
        vol = np.sqrt(max(1e-18, w @ matrix @ w))
        return -ret / vol

    result = _solve(negative_sharpe, n, long_only, max_weight)
    if not result.success or not np.all(np.isfinite(result.x)):
        return inverse_vol_weight_from_cov(cov), "optimizer did not converge, used inverse vol"
    return pd.Series(result.x, index=cov.index), ""


def risk_parity_weight(
    cov: pd.DataFrame, max_weight: float = 1.0
) -> tuple[pd.Series, str]:
    """Equal risk contribution. Always long-only; the concept needs positive
    weights to be meaningful."""
    n = len(cov)
    if n == 0:
        return pd.Series(dtype=float), "no data"
    if n == 1:
        return pd.Series([1.0], index=cov.index), ""
    matrix = cov.values
    target = 1.0 / n

    def objective(w):
        total_var = max(1e-18, w @ matrix @ w)
        contrib = w * (matrix @ w) / total_var
        return float(np.sum((contrib - target) ** 2))

    result = _solve(objective, n, long_only=True, max_weight=max_weight)
    if not result.success or not np.all(np.isfinite(result.x)):
        return inverse_vol_weight_from_cov(cov), (
            "optimizer did not converge, used inverse vol"
        )
    return pd.Series(result.x, index=cov.index), ""


# -- overlays ----------------------------------------------------------------

# One day 95 percent VaR is 1.645 standard deviations under a normal assumption.
VAR_Z_95 = 1.6448536269514722


def apply_overlay(
    weights: pd.Series,
    cov: pd.DataFrame,
    overlay: str,
    vol_target: float = 0.10,
    var_target: float = 0.02,
) -> tuple[pd.Series, float, str]:
    """Scale gross exposure to hit a risk target, holding the rest in cash.

    Returns the scaled weights, the scale factor, and a note. Exposure is never
    scaled above 1.0, so this de-risks but never levers.
    """
    if overlay == "none" or weights.empty:
        return weights, 1.0, ""

    current_vol = portfolio_vol(weights, cov)
    if current_vol <= 0:
        return weights, 1.0, "risk estimate unavailable, overlay skipped"

    if overlay == "vol_target":
        scale = vol_target / current_vol
        detail = f"forecast vol {current_vol * 100:.1f}% scaled toward {vol_target * 100:.1f}%"
    elif overlay == "var_target":
        daily_vol = current_vol / np.sqrt(TRADING_DAYS)
        current_var = VAR_Z_95 * daily_vol
        scale = var_target / current_var if current_var > 0 else 1.0
        detail = f"1d 95% VaR {current_var * 100:.2f}% scaled toward {var_target * 100:.2f}%"
    else:
        return weights, 1.0, ""

    scale = float(min(1.0, max(0.0, scale)))
    return weights * scale, scale, detail


# -- entry point -------------------------------------------------------------

def compute_weights(
    method: str,
    returns: pd.DataFrame,
    scores: pd.Series | None = None,
    long_only: bool = True,
    max_weight: float = 1.0,
    overlay: str = "none",
    vol_target: float = 0.10,
    var_target: float = 0.02,
    risk_free: float = 0.0,
) -> WeightResult:
    """Target weights for `returns`' columns under the chosen method."""
    symbols = list(returns.columns)
    if not symbols:
        return WeightResult(pd.Series(dtype=float), method, note="no symbols")

    notes: list[str] = []
    if len(returns) < 20:
        notes.append("short history, estimates are rough")

    cov = shrunk_covariance(returns)

    if method == "equal" or len(symbols) == 1:
        weights = equal_weight(symbols)
    elif method == "inverse_vol":
        weights = inverse_vol_weight(returns)
    elif method == "risk_parity":
        weights, note = risk_parity_weight(cov, max_weight)
        if note:
            notes.append(note)
    elif method == "min_variance":
        weights, note = min_variance_weight(cov, long_only, max_weight)
        if note:
            notes.append(note)
    elif method == "max_sharpe":
        expected = returns.mean() * TRADING_DAYS
        weights, note = max_sharpe_weight(expected, cov, long_only, max_weight, risk_free)
        if note:
            notes.append(note)
    elif method == "momentum":
        if scores is None:
            weights = equal_weight(symbols)
            notes.append("no momentum scores available, used equal weight")
        else:
            weights = momentum_weight(scores.reindex(symbols), long_only)
    else:
        weights = equal_weight(symbols)
        notes.append(f"unknown method {method}, used equal weight")

    weights = weights.reindex(symbols).fillna(0.0)
    # Tiny numerical dust reads badly in a weights table.
    weights[weights.abs() < 1e-6] = 0.0
    weights = _normalize(weights, long_only)

    gross_before = float(weights.abs().sum())
    weights, scale, overlay_note = apply_overlay(
        weights, cov, overlay, vol_target, var_target
    )
    if overlay_note:
        notes.append(overlay_note)

    diagnostics = {
        "vol": portfolio_vol(weights, cov),
        "expected_return": float((returns.mean() * TRADING_DAYS * weights).sum()),
        "gross": float(weights.abs().sum()),
        "scale": scale,
        "n": int((weights.abs() > 1e-6).sum()),
    }
    vol = diagnostics["vol"]
    diagnostics["sharpe"] = (
        (diagnostics["expected_return"] - risk_free) / vol if vol > 0 else float("nan")
    )
    diagnostics["var_95_1d"] = VAR_Z_95 * vol / np.sqrt(TRADING_DAYS)
    if not weights.empty and gross_before > 0:
        diagnostics["risk_contributions"] = risk_contributions(weights, cov)

    return WeightResult(
        weights=weights,
        method=method,
        cash=float(max(0.0, 1.0 - weights.abs().sum())),
        gross=float(weights.abs().sum()),
        diagnostics=diagnostics,
        note="; ".join(notes),
    )
