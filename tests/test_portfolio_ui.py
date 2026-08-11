"""Portfolio screen, driven through Textual's pilot with data stubbed out."""

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from stooq_cli import backtest, portfolio, signals
from stooq_cli.app import PortfolioScreen, StooqApp
from stooq_cli.store import AppState

FIXTURE = "tests/fixtures/fix_commodities.html"


def history(seed: int, n: int = 900, drift: float = 0.0005) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, n)))
    start = date.today() - timedelta(days=n)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(n)],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 1000.0,
        }
    )


@pytest.fixture
def app(tmp_path):
    with open(FIXTURE, encoding="utf-8") as fh:
        category_html = fh.read()
    state = AppState()
    state.basket = ["aaa.f", "bbb.f", "ccc.f"]
    state.portfolio_years = 2
    state.signal_lookback = 126
    histories = {"aaa.f": history(1, drift=0.0009), "bbb.f": history(2, drift=0.0002),
                 "ccc.f": history(3, drift=-0.0004)}
    with (
        patch("stooq_cli.app.store.load_state", return_value=state),
        patch("stooq_cli.app.store.save_state"),
        patch("stooq_cli.app.store.cookie_path", return_value=str(tmp_path / "c.txt")),
        patch("stooq_cli.app.store.budget_path", return_value=str(tmp_path / "b.json")),
        patch("stooq_cli.app.store.estimate_pages", return_value=0),
        patch("stooq_cli.app.store.get_history", side_effect=lambda c, s, **k: histories[s]),
        patch("stooq_cli.client.StooqClient.category_page", return_value=category_html),
    ):
        yield StooqApp()


async def settle(pilot, app, limit: int = 200) -> str:
    for _ in range(limit):
        await pilot.pause(0.1)
        try:
            text = str(app.screen.query_one("#pf-status").render())
        except Exception:
            continue
        if "aligned sessions" in text or "[red]" in text or "empty" in text:
            return text
    return "TIMEOUT"


async def test_portfolio_screen_renders(app):
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.4)
        app.push_screen(PortfolioScreen())
        text = await settle(pilot, app)
        assert text != "TIMEOUT"
        assert app.screen.query_one("#signal-table").row_count == 3
        assert app.screen.query_one("#weight-table").row_count >= 1
        assert app.screen.result is not None and app.screen.result.ok


async def test_buy_and_hold_equal_weight_is_evenly_split(app):
    """A case with a hand-checkable answer: three symbols, equal weight."""
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.4)
        app.state.strategy = "buy_and_hold"
        app.state.weight_method = "equal"
        app.push_screen(PortfolioScreen())
        await settle(pilot, app)
        weights = app.screen.query_one("#weight-table")
        assert weights.row_count == 3
        # Every displayed weight should read 33.33%.
        shown = [str(weights.get_row_at(i)[1]) for i in range(3)]
        assert all(cell.startswith("33.3") for cell in shown), shown


async def test_rotation_holds_only_the_top_n(app):
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.4)
        app.state.strategy = "rotation"
        app.state.top_n = 1
        app.push_screen(PortfolioScreen())
        await settle(pilot, app)
        signal_table = app.screen.query_one("#signal-table")
        held = [
            str(signal_table.get_row_at(i)[3])
            for i in range(signal_table.row_count)
        ]
        assert held.count("hold") <= 1


async def test_every_control_keeps_the_screen_alive(app):
    """The UI must survive any sequence of option changes."""
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.4)
        app.push_screen(PortfolioScreen())
        await settle(pilot, app)
        for key in ["m", "m", "k", "g", "n", "f", "v", "S", "F", "p"]:
            await pilot.press(key)
            assert await settle(pilot, app) != "TIMEOUT"
            assert app.screen.__class__.__name__ == "PortfolioScreen"


async def test_empty_basket_explains_itself(app):
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.4)
        app.state.basket = []
        app.push_screen(PortfolioScreen())
        for _ in range(40):
            await pilot.pause(0.1)
            text = str(app.screen.query_one("#pf-status").render())
            if "empty" in text:
                break
        assert "empty" in text
        assert app.screen.__class__.__name__ == "PortfolioScreen"


def test_labels_exist_for_every_option():
    """Every selectable option needs a human readable label, or the header
    would show a raw identifier."""
    for kind in signals.KINDS:
        assert kind in signals.KIND_LABELS
        assert kind in signals.KIND_HELP
    for method in portfolio.METHODS:
        assert method in portfolio.METHOD_LABELS
        assert method in portfolio.METHOD_HELP
    for overlay in portfolio.OVERLAYS:
        assert overlay in portfolio.OVERLAY_LABELS
    for strategy in backtest.STRATEGIES:
        assert strategy in backtest.STRATEGY_LABELS
        assert strategy in backtest.STRATEGY_HELP
    for freq in backtest.FREQUENCIES:
        assert freq in backtest.FREQUENCY_LABELS
