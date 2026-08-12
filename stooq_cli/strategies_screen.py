"""Strategies screen: shows the saved multi-portfolio strategies (currently the default
Canadian Sector + Commodity Rotation) and their latest computed target weights, read
straight from the app's own data directory — the same place `strategies/can_lev_rotation`
writes to after each run (manual or the Wednesday cron job). This screen never computes
anything itself; it is a read-only view onto the last run.

Deliberately reads the saved JSON directly (via `platformdirs`, already a core dependency)
rather than importing the `strategies` package, so this screen works in a normal `pipx`
install of Stooq CLI where `strategies/` (a repo-only research package, not shipped in the
wheel) isn't on the import path — only its *output* needs to be reachable.
"""

from __future__ import annotations

import json
import os

from platformdirs import user_data_dir
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, Static

APP_NAME = "stooq-cli"
DEFAULT_STRATEGY_NAME = "canadian_sector_commodity_rotation"


def _strategies_dir() -> str:
    return os.path.join(user_data_dir(APP_NAME), "strategies")


def _list_saved() -> list[str]:
    d = _strategies_dir()
    if not os.path.isdir(d):
        return []
    return sorted(
        f[:-5] for f in os.listdir(d)
        if f.endswith(".json") and not f.endswith("_last_run.json")
    )


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class StrategiesScreen(Screen):
    """Read-only view of saved strategies and their most recent target weights."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("", id="strat-header")
        with Horizontal(id="strat-body"):
            yield DataTable(id="strat-weight-table")
            yield Static("", id="strat-summary")
        yield Static("", id="strat-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#strat-weight-table", DataTable)
        table.zebra_stripes = True
        table.cursor_type = "row"
        self._load()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _load(self) -> None:
        header = self.query_one("#strat-header", Label)
        status = self.query_one("#strat-status", Static)
        table = self.query_one("#strat-weight-table", DataTable)
        summary = self.query_one("#strat-summary", Static)
        table.clear(columns=True)

        names = _list_saved()
        if not names:
            header.update("[b]Strategies[/b]")
            status.update(
                "No saved strategy yet. From the repo root: "
                "`python -m strategies.can_lev_rotation.run` builds and saves the "
                "default Canadian Sector + Commodity Rotation strategy."
            )
            return

        name = DEFAULT_STRATEGY_NAME if DEFAULT_STRATEGY_NAME in names else names[0]
        cfg = _load_json(os.path.join(_strategies_dir(), f"{name}.json")) or {}
        run = _load_json(os.path.join(_strategies_dir(), f"{name}_last_run.json"))

        header.update(f"[b]Strategies[/b]  [dim]saved:[/dim] {name}")

        if run is None:
            status.update(
                f"'{name}' is saved but has no run yet. Run "
                "`python -m strategies.can_lev_rotation.run` to compute target weights."
            )
            return

        table.add_columns("Ticker", "Fund", "State", "Weight")
        winner = run.get("winner_variant", "shorts_enabled")
        wt_key = "wt shorts-on" if winner == "shorts_enabled" else "wt long-only"
        for row in run.get("current_weights", []):
            w = row.get(wt_key, 0.0) or 0.0
            table.add_row(
                row.get("ticker", ""), row.get("fund", ""),
                row.get("state (shorts on)", "-"), f"{w:+.1%}",
            )

        m = run.get(winner, {})

        def pct(key: str) -> str:
            v = m.get(key)
            return f"{v:.2%}" if isinstance(v, (int, float)) else "n/a"

        def ratio(key: str) -> str:
            v = m.get(key)
            return f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"

        lines = [
            f"[b]{cfg.get('description', name)}[/b]", "",
            f"[dim]variant live[/dim] {winner}",
            f"[dim]window[/dim] {run.get('window', {}).get('start', '?')} "
            f"→ {run.get('window', {}).get('end', '?')}",
            f"[dim]generated[/dim] {run.get('generated_at', '?')}", "",
            f"[dim]CAGR[/dim] {pct('cagr')}",
            f"[dim]Ann. vol[/dim] {pct('ann_vol')}",
            f"[dim]Sharpe[/dim] {ratio('sharpe')}",
            f"[dim]Max drawdown[/dim] {pct('max_drawdown')}",
            f"[dim]VaR 95% (1d)[/dim] {pct('var_95_1d')}",
            f"[dim]CVaR 95% (1d)[/dim] {pct('cvar_95_1d')}",
            "",
            f"PDF: {run.get('pdf_path', '?')}",
        ]
        summary.update("\n".join(lines))
        status.update("Read-only — refreshed by the Wednesday cron job or a manual run.")
