"""Helpers that draw onto a textual-plotext `plt` object.

Each helper fully resets the plot, applies theme colors, and labels axes.
Dates are passed as strings in plotext's date format.
"""

from __future__ import annotations

import pandas as pd

from .themes import chart_colors

DATE_FORM = "d/m/Y"


def _apply_theme(plt, colors: dict) -> None:
    plt.theme("clear")
    plt.canvas_color(colors["bg"])
    plt.axes_color(colors["bg"])
    plt.ticks_color(colors["fg"])


def _dates(index) -> list[str]:
    return [d.strftime("%d/%m/%Y") for d in index]


def price_chart(plt, dates, closes, title: str, dark: bool) -> None:
    colors = chart_colors(dark)
    plt.clear_figure()
    _apply_theme(plt, colors)
    plt.date_form(DATE_FORM)
    plt.plot(_dates(dates), list(closes), color=colors["accent"], marker="braille")
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Price")


def multi_line_chart(
    plt,
    frame: pd.DataFrame,
    title: str,
    ylabel: str,
    dark: bool,
    hline: float | None = None,
) -> None:
    colors = chart_colors(dark)
    plt.clear_figure()
    _apply_theme(plt, colors)
    plt.date_form(DATE_FORM)
    dates = _dates(frame.index)
    for i, col in enumerate(frame.columns):
        series = frame[col]
        mask = series.notna()
        if mask.sum() == 0:
            continue
        xs = [d for d, ok in zip(dates, mask, strict=True) if ok]
        ys = list(series[mask])
        color = colors["series"][i % len(colors["series"])]
        plt.plot(xs, ys, color=color, marker="braille", label=str(col))
    if hline is not None:
        plt.hline(hline, colors["grid"])
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel(ylabel)
