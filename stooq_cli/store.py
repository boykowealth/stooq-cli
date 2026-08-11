"""Disk state (watchlist, basket, theme) and the historical data cache.

History is cached as one CSV per symbol under the platform cache directory,
so analytics reruns are instant and Stooq is only asked for missing days.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from platformdirs import user_cache_dir, user_data_dir

from .client import StooqClient, StooqQuotaExceeded
from .scrape import parse_history

APP_NAME = "stooq-cli"
ROWS_PER_PAGE = 40  # Stooq's fixed page size; there is no way to ask for more
MAX_HISTORY_PAGES = 40  # so up to roughly six years of daily bars per fetch


class BudgetExhausted(Exception):
    """The local self-imposed request cap was reached before Stooq's."""


def cache_dir() -> str:
    path = user_cache_dir(APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def data_dir() -> str:
    path = user_data_dir(APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def cookie_path() -> str:
    return os.path.join(cache_dir(), "cookies.txt")


def budget_path() -> str:
    return os.path.join(data_dir(), "budget.json")


# -- persisted app state -----------------------------------------------------

STATE_PATH = os.path.join(data_dir(), "state.json")


@dataclass
class AppState:
    theme: str = "stooq-light"
    view: str = "commodities"
    watchlist: list[dict] = field(default_factory=list)
    basket: list[str] = field(default_factory=list)
    rolling_window: int = 60
    history_years: int = 1
    daily_request_limit: int = 0  # 0 means use the built-in default
    # Portfolio and signal settings
    strategy: str = "rotation"
    weight_method: str = "equal"
    signal_kind: str = "12_1"
    signal_lookback: int = 252
    top_n: int = 3
    rebalance: str = "ME"
    overlay: str = "none"
    allow_shorts: bool = False
    absolute_filter: bool = True
    portfolio_years: int = 3
    vol_target: float = 0.10
    var_target: float = 0.02
    # Trading frictions. Defaults are realistic for retail in liquid names;
    # tax is off by default because it depends entirely on your jurisdiction.
    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    tax_rate: float = 0.0

    def watchlist_symbols(self) -> list[str]:
        return [item["symbol"] for item in self.watchlist]


def load_state() -> AppState:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        state = AppState()
        for key, value in raw.items():
            if hasattr(state, key):
                setattr(state, key, value)
        return state
    except (OSError, ValueError):
        return AppState()


def save_state(state: AppState) -> None:
    payload = json.dumps(state.__dict__, indent=2)
    try:
        fd, tmp = tempfile.mkstemp(dir=data_dir(), prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


# -- history cache -----------------------------------------------------------

def _hist_path(symbol: str) -> str:
    safe = re.sub(r"[^a-z0-9_.^-]", "_", symbol.lower()).replace("^", "_")
    return os.path.join(cache_dir(), f"hist_{safe}.csv")


def _meta_path(symbol: str) -> str:
    return _hist_path(symbol).replace(".csv", ".meta.json")


def _load_meta(symbol: str) -> dict:
    try:
        with open(_meta_path(symbol), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_meta(symbol: str, meta: dict) -> None:
    try:
        with open(_meta_path(symbol), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
    except OSError:
        pass


def _load_cached(symbol: str) -> pd.DataFrame | None:
    path = _hist_path(symbol)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        df["date"] = df["date"].dt.date
        return df
    except (OSError, ValueError, KeyError):
        return None


def _save_cached(symbol: str, df: pd.DataFrame) -> None:
    try:
        df.to_csv(_hist_path(symbol), index=False)
    except OSError:
        pass


def _fetch_range(
    client: StooqClient,
    symbol: str,
    start: date,
    end: date,
    progress=None,
    budget=None,
) -> pd.DataFrame:
    d1, d2 = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    rows: list[dict] = []
    page = 1
    total = 1
    while page <= total and page <= MAX_HISTORY_PAGES:
        if budget is not None and not budget.try_spend(1):
            # Our own cap, hit before Stooq's. Stop with whatever we have.
            if rows:
                break
            raise BudgetExhausted(
                "The local daily request budget is used up, so no new history "
                "was downloaded. Cached symbols still work. Raise the cap with "
                "the L key if you want to spend more of Stooq's allowance."
            )
        if progress:
            progress(f"{symbol}: page {page}/{total}")
        try:
            html = client.quote_history_page(symbol, d1, d2, page)
        except StooqQuotaExceeded:
            if budget is not None:
                budget.record_block()
            # Keep whatever pages already came back; only a completely empty
            # result is worth surfacing as an error to the caller.
            if rows:
                break
            raise
        bars, total = parse_history(html)
        for bar in bars:
            rows.append(
                {
                    "date": bar.day,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
            )
        if not bars:
            break
        page += 1
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def start_for_days(days: int) -> date:
    """The earliest date a view covering `days` calendar days needs."""
    return date.today() - timedelta(days=max(1, days))


def estimate_pages(symbol: str, start: date) -> int:
    """How many requests fetching `symbol` back to `start` would still cost,
    given what is already cached. Used to warn before expensive operations."""
    symbol = symbol.lower()
    today = date.today()
    cached = _load_cached(symbol)
    meta = _load_meta(symbol)
    earliest_requested = date.fromisoformat(meta["earliest"]) if "earliest" in meta else None

    def pages_between(a: date, b: date) -> int:
        if b <= a:
            return 0
        sessions = (b - a).days * (5 / 7)
        return max(1, math.ceil(sessions / ROWS_PER_PAGE))

    if cached is None or cached.empty:
        return min(MAX_HISTORY_PAGES, pages_between(start, today))

    total = 0
    have_start = cached["date"].min()
    have_end = cached["date"].max()
    if have_start > start and (earliest_requested is None or earliest_requested > start):
        total += pages_between(start, have_start)
    if have_end < today - timedelta(days=1):
        total += pages_between(have_end, today)
    return min(MAX_HISTORY_PAGES, total)


def get_history(
    client: StooqClient,
    symbol: str,
    years: float | None = None,
    progress=None,
    start: date | None = None,
    budget=None,
) -> pd.DataFrame:
    """Return daily bars for `symbol`, newest last, using the cache when fresh.

    Only the span missing from the cache is requested from Stooq. Pass `start`
    to fetch exactly the window a view needs; a one month chart then costs a
    single request rather than a full year's worth of pages.
    """
    symbol = symbol.lower()
    today = date.today()
    if start is None:
        start = today - timedelta(days=int((years if years is not None else 2) * 365.25))
    cached = _load_cached(symbol)
    meta = _load_meta(symbol)
    earliest_requested = date.fromisoformat(meta["earliest"]) if "earliest" in meta else None

    if cached is not None and not cached.empty:
        have_start = cached["date"].min()
        have_end = cached["date"].max()
        frames = [cached]
        need_backfill = have_start > start and (
            earliest_requested is None or earliest_requested > start
        )
        # Serving a slightly stale cache beats failing outright, so a spent
        # quota only downgrades what we can top up, never what we already have.
        try:
            if need_backfill:
                frames.append(
                    _fetch_range(
                        client, symbol, start, have_start - timedelta(days=1), progress, budget
                    )
                )
            if have_end < today - timedelta(days=1):
                frames.append(
                    _fetch_range(
                        client, symbol, have_end + timedelta(days=1), today, progress, budget
                    )
                )
        except (StooqQuotaExceeded, BudgetExhausted):
            pass
        df = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset="date")
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        df = _fetch_range(client, symbol, start, today, progress, budget)

    if not df.empty:
        _save_cached(symbol, df)
        if earliest_requested is None or start < earliest_requested:
            _save_meta(symbol, {"earliest": start.isoformat()})
    mask = df["date"] >= start
    return df.loc[mask].reset_index(drop=True)
