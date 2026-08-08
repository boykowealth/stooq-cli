"""App smoke tests using Textual's pilot, with the network stubbed out."""

import os
from unittest.mock import patch

import pytest

from stooq_cli.app import StooqApp
from stooq_cli.store import AppState

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def app(tmp_path):
    category_html = load("fix_commodities.html")
    with (
        patch("stooq_cli.app.store.load_state", return_value=AppState()),
        patch("stooq_cli.app.store.save_state"),
        patch("stooq_cli.app.store.cookie_path", return_value=str(tmp_path / "c.txt")),
        patch(
            "stooq_cli.client.StooqClient.category_page", return_value=category_html
        ),
        patch(
            "stooq_cli.client.StooqClient.search",
            return_value="window.cmp_r('CL.F~Crude Oil WTI~Cmdt Fut~77.09~-1.52%~2')",
        ),
    ):
        yield StooqApp()


async def test_launch_and_table(app):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        table = app.query_one("#quotes")
        assert table.row_count > 50
        assert app.state.view == "commodities"


async def test_switch_views_and_sort(app):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("3")
        await pilot.pause(0.5)
        assert app.state.view == "fx"
        await pilot.press("s")
        assert app.sort_mode == 1
        await pilot.press("7")
        await pilot.pause(0.5)
        assert app.state.view == "watchlist"


async def test_watch_and_basket(app):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("a")
        assert len(app.state.watchlist) == 1
        await pilot.press("b")
        assert len(app.state.basket) == 1
        await pilot.press("b")
        assert len(app.state.basket) == 0


async def test_help_and_theme(app):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        assert app.screen.__class__.__name__ == "HelpScreen"
        await pilot.press("escape")
        await pilot.pause(0.2)
        before = app.theme
        await pilot.press("f6")
        assert app.theme != before
