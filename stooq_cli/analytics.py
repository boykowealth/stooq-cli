"""Quantitative analytics over daily close series.

Everything operates on a wide DataFrame of closes (index: date, one column
per symbol) built by `align_closes`. Returns are daily log returns.
"""

from __future__ import annotations

import itertools
import warnings

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def align_closes(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Inner-join close series from per-symbol history frames on date."""
    series = {}
    for symbol, df in histories.items():
        if df is None or df.empty:
            continue
        s = df.set_index("date")["close"].astype(float)
        s = s[~s.index.duplicated(keep="last")]
        series[symbol.upper()] = s
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).dropna(how="any")
    return wide.sort_index()


def log_returns(closes: pd.DataFrame) -> pd.DataFrame:
    rets = np.log(closes / closes.shift(1))
    return rets.dropna(how="any")


def corr_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.corr()


def rolling_corr(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    """Pairwise rolling correlations; columns labeled 'A / B'."""
    pairs = list(itertools.combinations(returns.columns, 2))
    out = {}
    for a, b in pairs:
        out[f"{a} / {b}"] = returns[a].rolling(window).corr(returns[b])
    return pd.DataFrame(out).dropna(how="all")


def realized_vol(returns: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """Annualized rolling realized volatility, in percent."""
    return returns.rolling(window).std() * np.sqrt(TRADING_DAYS) * 100.0


def garch_vol(returns: pd.Series) -> tuple[pd.Series, dict]:
    """Fit GARCH(1,1) and return annualized conditional volatility (percent)
    plus fitted parameters."""
    from arch import arch_model

    scaled = returns.dropna() * 100.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = arch_model(scaled, mean="Constant", vol="GARCH", p=1, q=1, rescale=False)
        result = model.fit(disp="off", show_warning=False)
    cond = result.conditional_volatility * np.sqrt(TRADING_DAYS)
    omega = float(result.params.get("omega", np.nan))
    alpha = float(result.params.get("alpha[1]", np.nan))
    beta = float(result.params.get("beta[1]", np.nan))
    persistence = alpha + beta
    if 0 < persistence < 1:
        long_run = float(np.sqrt(omega / (1 - persistence) * TRADING_DAYS))
    else:
        long_run = float("nan")
    params = {
        "omega": omega,
        "alpha": alpha,
        "beta": beta,
        "persistence": persistence,
        "long_run_vol": long_run,
        "loglik": float(result.loglikelihood),
    }
    return pd.Series(cond, index=scaled.index), params


def max_drawdown(closes: pd.Series) -> float:
    running_max = closes.cummax()
    drawdown = closes / running_max - 1.0
    return float(drawdown.min())


def summary_stats(closes: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol performance and risk summary."""
    rets = log_returns(closes)
    rows = []
    for col in closes.columns:
        r = rets[col]
        ann_ret = float(r.mean() * TRADING_DAYS)
        ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))
        sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
        rows.append(
            {
                "symbol": col,
                "last": float(closes[col].iloc[-1]),
                "ann_return_pct": ann_ret * 100.0,
                "ann_vol_pct": ann_vol * 100.0,
                "sharpe": sharpe,
                "skew": float(r.skew()),
                "kurtosis": float(r.kurt()),
                "max_dd_pct": max_drawdown(closes[col]) * 100.0,
                "obs": int(r.count()),
            }
        )
    return pd.DataFrame(rows)
