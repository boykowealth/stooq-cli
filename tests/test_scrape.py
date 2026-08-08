"""Parser tests against saved stooq.com fixture pages."""

import os
from datetime import date

import pytest

from stooq_cli.scrape import (
    parse_category,
    parse_history,
    parse_search,
    parse_title_name,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize(
    "fixture,min_rows",
    [
        ("fix_commodities.html", 80),
        ("fix_indices.html", 40),
        ("fix_fx.html", 80),
        ("fix_bonds.html", 10),
        ("fix_macro.html", 30),
    ],
)
def test_parse_category_rows(fixture, min_rows):
    rows, max_page = parse_category(load(fixture))
    assert len(rows) >= min_rows
    assert max_page >= 1
    for row in rows:
        assert row.symbol
        assert row.symbol == row.symbol.lower()
        assert row.name
    with_price = [r for r in rows if r.last is not None]
    assert len(with_price) > len(rows) * 0.8


def test_parse_category_pagination():
    _, max_page = parse_category(load("fix_fx.html"))
    assert max_page > 1


def test_parse_history():
    bars, max_page = parse_history(load("fix_history.html"))
    assert len(bars) == 40
    assert max_page > 1
    assert bars == sorted(bars, key=lambda b: b.day)
    newest = bars[-1]
    assert newest.day == date(2026, 8, 7)
    assert newest.close == pytest.approx(37.0)
    assert newest.open == pytest.approx(36.68)
    assert newest.high == pytest.approx(38.03)
    assert newest.low == pytest.approx(36.59)
    assert newest.volume == pytest.approx(2466174)


def test_parse_title_name():
    name = parse_title_name(load("fix_history.html"), "uco.us")
    assert name == "ProShares Ultra Bloomberg Crude Oil"


def test_parse_search():
    payload = (
        "window.cmp_r('CL.F~<b>Crude</b> Oil WTI~Cmdt Fut~77.09~-1.52%~2|"
        "CB.F~<b>Crude</b> Oil Brent - ICE~Cmdt Fut~83.55~1.29%~2')"
    )
    hits = parse_search(payload)
    assert len(hits) == 2
    assert hits[0].symbol == "cl.f"
    assert hits[0].name == "Crude Oil WTI"
    assert hits[0].market == "Cmdt Fut"
    assert hits[0].last == "77.09"
    assert hits[0].change_pct == "-1.52%"


def test_parse_search_garbage():
    assert parse_search("") == []
    assert parse_search("<html>not a payload</html>") == []


def test_parse_category_garbage():
    rows, max_page = parse_category("<html><body>nothing here</body></html>")
    assert rows == []
    assert max_page == 1


def test_parse_history_garbage():
    bars, _ = parse_history("<html><table><tr><td>x</td></tr></table></html>")
    assert bars == []
