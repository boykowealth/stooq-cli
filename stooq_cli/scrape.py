"""Parsers for stooq.com pages.

All parsers are defensive: a row that does not match the expected shape is
skipped rather than crashing the app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

_NUM_RE = re.compile(r"^[+\-]?[\d,]*\.?\d+$")
_DATE_RE = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$")


@dataclass
class QuoteRow:
    symbol: str
    name: str
    last: float | None
    change_pct: float | None
    change_abs: float | None
    when: str


@dataclass
class Bar:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


@dataclass
class SearchHit:
    symbol: str
    name: str
    market: str
    last: str
    change_pct: str


def _to_float(text: str) -> float | None:
    text = text.replace(",", "").replace("+", "").strip()
    if not text or not _NUM_RE.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _max_page(html: str) -> int:
    pages = [int(p) for p in re.findall(r"[?&]l=(\d+)", html)]
    return max(pages) if pages else 1


# -- category tables (stooq.com/t/?i=NNN) -----------------------------------

def parse_category(html: str) -> tuple[list[QuoteRow], int]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[QuoteRow] = []
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"^q/\?s="))
        if link is None:
            continue
        # Direct children only: outer layout rows wrap the whole page in one
        # cell and must not be mistaken for data rows.
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)]
        if len(cells) < 5:
            continue
        symbol = link.get_text(strip=True).lower()
        name = cells[1]
        last = _to_float(cells[2])
        if not name or last is None:
            # Rows from the page's top quote strip or other side tables.
            continue
        pct = next((c for c in cells if c.endswith("%")), "")
        change_pct = _to_float(pct.rstrip("%"))
        after_pct = cells.index(pct) + 1 if pct in cells else 4
        change_abs = _to_float(cells[after_pct]) if after_pct < len(cells) else None
        when = ""
        for c in cells[after_pct:]:
            if re.search(r"[A-Za-z]{3} \d|\d{1,2}:\d{2}", c):
                when = c
                break
        rows.append(QuoteRow(symbol, name, last, change_pct, change_abs, when))
    return rows, _max_page(html)


# -- historical data (stooq.com/q/d/?s=X) -----------------------------------

def parse_history(html: str) -> tuple[list[Bar], int]:
    soup = BeautifulSoup(html, "html.parser")
    bars: list[Bar] = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)]
        if len(cells) < 6:
            continue
        date_idx = next((i for i, c in enumerate(cells) if _DATE_RE.match(c)), None)
        if date_idx is None:
            continue
        # The history table numbers its rows; the cell before the date is that
        # counter. Rows from other tables on the page do not have one.
        if date_idx == 0 or not cells[date_idx - 1].isdigit():
            continue
        try:
            day = datetime.strptime(cells[date_idx], "%d %b %Y").date()
        except ValueError:
            continue
        values = cells[date_idx + 1:]
        # Layout: Open High Low Close [Change% Change] [Volume]
        if len(values) < 4:
            continue
        o, h, lo, c = (_to_float(v) for v in values[:4])
        if c is None:
            continue
        volume = None
        for v in values[4:]:
            if v.endswith("%") or v.startswith(("+", "-")):
                continue
            volume = _to_float(v)
            break
        bars.append(Bar(day, o, h, lo, c, volume))
    bars.sort(key=lambda b: b.day)
    return bars, _max_page(html)


def parse_title_name(html: str, symbol: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    if not match:
        return symbol.upper()
    title = match.group(1).strip()
    # "UCO.US - ProShares Ultra Bloomberg Crude Oil - Stooq"
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) >= 3:
        return " - ".join(parts[1:-1])
    return symbol.upper()


# -- search suggestions (stooq.com/cmp/?q=) ---------------------------------

def parse_search(payload: str) -> list[SearchHit]:
    match = re.search(r"cmp_r\('(.*?)'\)", payload, re.S)
    if not match:
        return []
    hits: list[SearchHit] = []
    for chunk in match.group(1).split("|"):
        fields = chunk.split("~")
        if len(fields) < 5:
            continue
        clean = [re.sub(r"</?b>", "", f).strip() for f in fields]
        symbol = clean[0].lower()
        if not symbol:
            continue
        hits.append(SearchHit(symbol, clean[1], clean[2], clean[3], clean[4]))
    return hits
