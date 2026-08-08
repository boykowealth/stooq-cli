"""Built-in market views, each backed by a stooq.com table page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    stooq_id: int


CATEGORIES: list[Category] = [
    Category("commodities", "Commodities", 557),
    Category("indices", "Indices", 510),
    Category("fx", "FX", 511),
    Category("equities", "Equities", 518),
    Category("bonds", "Bonds", 550),
    Category("macro", "Macro", 539),
]

WATCHLIST_KEY = "watchlist"
DEFAULT_VIEW = "commodities"


def by_key(key: str) -> Category | None:
    for cat in CATEGORIES:
        if cat.key == key:
            return cat
    return None
