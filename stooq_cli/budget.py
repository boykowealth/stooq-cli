"""A self-imposed daily cap on quota-consuming requests.

Stooq limits how much historical data an anonymous address may pull per day,
and does not publish the number. Rather than discovering the limit by getting
locked out, the terminal counts its own history requests and stops short,
keeping a reserve so cached browsing always keeps working.

The cap adapts: if Stooq ever refuses us anyway, the spend at that moment is
recorded as an upper bound on the real limit, and later days budget below it.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date

# Conservative starting point, used until Stooq's real limit is observed.
DEFAULT_DAILY_LIMIT = 120

# Fraction of an observed limit we allow ourselves, so there is always a
# reserve left for browsing and for the next symbol you actually care about.
RESERVE_FRACTION = 0.8

# A refusal only tells us something about our own cap if we had actually spent
# a meaningful amount. Stooq's day rolls over on its clock, not local midnight,
# so the first request of a local day can be refused on yesterday's quota.
# Learning from that would teach a limit of nearly zero and brick the app.
MIN_OBSERVATION = 5

# However low the observation, never cap ourselves below a usable level.
MIN_LEARNED_LIMIT = 20


@dataclass
class BudgetState:
    day: str = ""
    spent: int = 0
    limit: int = DEFAULT_DAILY_LIMIT
    # Smallest spend at which Stooq has ever refused us. None until observed.
    observed_limit: int | None = None
    blocked_today: bool = False


@dataclass
class RequestBudget:
    """Tracks and caps quota-consuming requests for the current day."""

    path: str
    _state: BudgetState = field(default_factory=BudgetState)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            state = BudgetState()
            for key, value in raw.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            self._state = state
        except (OSError, ValueError):
            self._state = BudgetState()
        self._rollover()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._state.__dict__, fh, indent=2)
        except OSError:
            pass

    def _rollover(self) -> None:
        """Reset the counter when the calendar day changes. The learned limit
        deliberately survives the reset."""
        today = date.today().isoformat()
        if self._state.day != today:
            self._state.day = today
            self._state.spent = 0
            self._state.blocked_today = False
            self._save()

    # -- accounting ---------------------------------------------------------

    @property
    def effective_limit(self) -> int:
        """The cap we hold ourselves to: the user's limit, reduced to a
        reserve fraction of Stooq's real limit once that has been observed."""
        limit = self._state.limit
        observed = self._state.observed_limit
        if observed is not None:
            limit = min(limit, max(1, int(observed * RESERVE_FRACTION)))
        return limit

    @property
    def spent(self) -> int:
        self._rollover()
        return self._state.spent

    @property
    def remaining(self) -> int:
        return max(0, self.effective_limit - self.spent)

    @property
    def blocked_today(self) -> bool:
        self._rollover()
        return self._state.blocked_today

    def set_limit(self, limit: int) -> None:
        with self._lock:
            self._state.limit = max(1, int(limit))
            self._save()

    def try_spend(self, count: int = 1) -> bool:
        """Reserve `count` requests, or return False if that would exceed the
        cap. Callers must stop when this returns False."""
        with self._lock:
            self._rollover()
            if self._state.spent + count > self.effective_limit:
                return False
            self._state.spent += count
            self._save()
            return True

    def record_block(self) -> None:
        """Stooq refused us. Remember the spend as an upper bound on its real
        limit so future days stay under it.

        A refusal after very little spending means Stooq's own day has not
        rolled over yet, not that our limit is tiny, so it teaches us nothing
        and is deliberately not learned from.
        """
        with self._lock:
            self._rollover()
            self._state.blocked_today = True
            spent = self._state.spent
            if spent >= MIN_OBSERVATION:
                seen = max(MIN_LEARNED_LIMIT, spent)
                observed = self._state.observed_limit
                self._state.observed_limit = (
                    seen if observed is None else min(observed, seen)
                )
            self._save()

    def summary(self) -> str:
        if self.blocked_today:
            return f"requests {self.spent}/{self.effective_limit} (Stooq limit reached)"
        return f"requests {self.spent}/{self.effective_limit}"
