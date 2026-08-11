"""The stooq terminal application."""

from __future__ import annotations

import webbrowser
from datetime import date, timedelta

import pandas as pd
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Label,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option
from textual_plotext import PlotextPlot

from . import analytics, backtest, portfolio, signals, store
from .budget import RequestBudget
from .categories import CATEGORIES, DEFAULT_VIEW, WATCHLIST_KEY, by_key
from .charts import multi_line_chart, price_chart
from .client import StooqClient, StooqError
from .scrape import QuoteRow, SearchHit, parse_category, parse_search
from .themes import STOOQ_DARK, STOOQ_LIGHT

VIEW_KEYS = [c.key for c in CATEGORIES] + [WATCHLIST_KEY]
RANGE_MONTHS = {"1": 1, "3": 3, "6": 6, "y": 12, "5": 60}
SORT_MODES = ["default", "chg desc", "chg asc", "last desc", "name asc"]


def fmt_num(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "-"
    if abs(value) >= 10000:
        return f"{value:,.0f}"
    return f"{value:,.{decimals}f}"


def fmt_price(value: float | None) -> str:
    """Price formatting that suits index levels, futures and FX rates alike:
    the smaller the quote, the more decimals it needs to stay meaningful."""
    if value is None:
        return "-"
    magnitude = abs(value)
    if magnitude >= 10000:
        return f"{value:,.0f}"
    if magnitude >= 100:
        return f"{value:,.2f}"
    if magnitude >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def change_text(pct: float | None, absolute: float | None, app: App) -> tuple[Text, Text]:
    theme = app.current_theme
    up = theme.variables.get("up-color", "green")
    down = theme.variables.get("down-color", "red")
    if pct is None:
        return Text("-"), Text("-")
    color = up if pct > 0 else down if pct < 0 else None
    sign = "+" if pct > 0 else ""
    pct_text = Text(f"{sign}{pct:.2f}%", style=color or "")
    abs_text = Text(
        f"{sign}{absolute:,.4g}" if absolute is not None else "-", style=color or ""
    )
    return pct_text, abs_text


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss_help", "Close")]

    HELP_TEXT = """\
[b]Navigation[/b]
  1-6           Market views (Commodities, Indices, FX, Equities, Bonds, Macro)
  7             Watchlist
  [ and ]       Previous / next page of the current view
  Enter         Open the selected symbol (chart and statistics)
  Ctrl+L or /   Focus the search bar
  Escape        Close a screen or the search suggestions

[b]Lists[/b]
  a             Add selected symbol to the watchlist
  x             Remove selected symbol from the watchlist
  b             Toggle selected symbol in the analytics basket
  s             Cycle table sorting
  r             Refresh data

[b]Analytics and portfolio[/b]
  A             Open the analytics screen (uses the basket)
  P             Open the portfolio screen (signals, weights, backtest)
  a / x         Add or remove basket symbols (inside analytics)
  w             Cycle rolling window (30 / 60 / 90 / 120 days)
  t             Cycle history span (1 / 2 / 3 / 5 years)

[b]Portfolio (P)[/b]
  g             Strategy: momentum rotation or buy and hold
  m             Weighting: equal, inverse vol, risk parity, min variance,
                max Sharpe, momentum weighted
  k / p         Momentum signal kind / lookback period
  n             How many holdings a rotation keeps
  f             Rebalance frequency: monthly, quarterly, yearly
  v             Risk overlay: none, volatility target, VaR target
  t             History span used for signals and the backtest
  S             Allow short positions
  F             Absolute momentum filter (falling assets to cash)

[b]Symbol view[/b]
  1 3 6 y 5     Chart range: 1m, 3m, 6m, 1y, 5y
  o             Open this symbol on stooq.com in your browser

[b]General[/b]
  F6            Toggle light / dark theme
  L             Cycle the daily request budget (60 / 120 / 200 / 300)
  ?             This help
  q or Ctrl+Q   Quit

[b]About the request budget[/b]
  Stooq caps how much history an address may download per day. The terminal
  counts its own history requests and stops short of that cap, so browsing,
  search and cached symbols keep working no matter what. Only downloading
  new history costs anything; market views and search are always free.
"""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-panel"):
            yield Label("stooq terminal - keys", id="help-title")
            yield Static(self.HELP_TEXT, id="help-text")

    def action_dismiss_help(self) -> None:
        self.dismiss()


class AddSymbolScreen(ModalScreen[SearchHit | None]):
    """Small search dialog that resolves to a chosen symbol."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str = "Add symbol") -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="add-panel"):
            yield Label(self._title, id="add-title")
            yield Input(placeholder="Type a name or ticker, Enter to pick", id="add-input")
            yield OptionList(id="add-options")

    def on_mount(self) -> None:
        self.query_one("#add-input", Input).focus()
        self._hits: list[SearchHit] = []
        self._timer = None

    @on(Input.Changed, "#add-input")
    def _debounce(self, event: Input.Changed) -> None:
        if self._timer is not None:
            self._timer.stop()
        query = event.value.strip()
        if len(query) < 2:
            self.query_one("#add-options", OptionList).clear_options()
            return
        self._timer = self.set_timer(0.35, lambda: self._search(query))

    @work(thread=True, exclusive=True, group="modal-search")
    def _search(self, query: str) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        try:
            hits = parse_search(app.client.search(query))[:9]
        except StooqError:
            hits = []
        self.app.call_from_thread(self._show_hits, hits)

    def _show_hits(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        options = self.query_one("#add-options", OptionList)
        options.clear_options()
        for hit in hits:
            label = f"{hit.symbol.upper():<12} {hit.name[:34]:<36} {hit.market}"
            options.add_option(Option(label, id=hit.symbol))
        if hits:
            options.highlighted = 0

    @on(Input.Submitted, "#add-input")
    def _submit(self) -> None:
        options = self.query_one("#add-options", OptionList)
        if self._hits and options.option_count:
            index = options.highlighted or 0
            self.dismiss(self._hits[index])
        else:
            self.dismiss(None)

    @on(OptionList.OptionSelected, "#add-options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        for hit in self._hits:
            if hit.symbol == event.option.id:
                self.dismiss(hit)
                return
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        options = self.query_one("#add-options", OptionList)
        if event.key in ("down", "up") and options.option_count:
            delta = 1 if event.key == "down" else -1
            current = options.highlighted or 0
            options.highlighted = max(0, min(options.option_count - 1, current + delta))
            event.stop()


class PickSymbolScreen(ModalScreen[str | None]):
    """Pick one symbol from a fixed list (used to remove basket entries)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, symbols: list[str]) -> None:
        super().__init__()
        self._title = title
        self._symbols = symbols

    def compose(self) -> ComposeResult:
        with Vertical(id="add-panel"):
            yield Label(self._title, id="add-title")
            yield OptionList(
                *[Option(sym.upper(), id=sym) for sym in self._symbols], id="pick-options"
            )

    @on(OptionList.OptionSelected, "#pick-options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Yes or no before spending a noticeable slice of the daily budget."""

    BINDINGS = [
        Binding("escape,n", "no", "No"),
        Binding("enter,y", "yes", "Yes"),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-panel"):
            yield Label(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            yield Label("[b]Enter[/b] or [b]y[/b] to continue,  [b]Esc[/b] or [b]n[/b] to cancel",
                        id="confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class DetailScreen(Screen):
    """Price chart and statistics for one symbol."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("1", "range('1')", "1m", show=False),
        Binding("3", "range('3')", "3m", show=False),
        Binding("6", "range('6')", "6m", show=False),
        Binding("y", "range('y')", "1y"),
        Binding("5", "range('5')", "5y", show=False),
        Binding("a", "watch", "Watch"),
        Binding("b", "basket", "Basket"),
        Binding("o", "open_site", "Open on stooq.com"),
        Binding("r", "reload", "Refresh"),
    ]

    def __init__(self, symbol: str, name: str) -> None:
        super().__init__()
        self.symbol = symbol.lower()
        self.display_name = name or symbol.upper()
        self.range_key = "y"
        self.df: pd.DataFrame | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="detail-header")
        with Horizontal(id="detail-body"):
            yield PlotextPlot(id="detail-chart")
            yield Static("", id="detail-stats")
        yield Static("", id="detail-status")
        yield Footer()

    def on_mount(self) -> None:
        self._set_header()
        self._load()

    def _set_header(self) -> None:
        ranges = {"1": "1 month", "3": "3 months", "6": "6 months", "y": "1 year", "5": "5 years"}
        self.query_one("#detail-header", Label).update(
            f"[b]{self.display_name}[/b]  [dim]{self.symbol.upper()}  "
            f"daily close, {ranges[self.range_key]}[/dim]"
        )

    def _status(self, text: str) -> None:
        self.query_one("#detail-status", Static).update(text)

    @work(thread=True, exclusive=True, group="detail")
    def _load(self) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        months = RANGE_MONTHS[self.range_key]
        # Fetch exactly the window being charted. A one month view then costs
        # a single request instead of a whole year's worth of pages.
        start = store.start_for_days(int(months * 30.44))
        self.app.call_from_thread(self._begin_loading)
        try:
            df = store.get_history(
                app.client,
                self.symbol,
                start=start,
                budget=app.budget,
                progress=lambda msg: self.app.call_from_thread(
                    self._status, f"Loading {msg}"
                ),
            )
        except (StooqError, store.BudgetExhausted) as exc:
            self.app.call_from_thread(self._fail, str(exc))
            return
        self.app.call_from_thread(self._show_history, df)

    def _begin_loading(self) -> None:
        self.query_one("#detail-chart", PlotextPlot).loading = True
        self._status("Loading price history...")

    def _fail(self, message: str) -> None:
        self.query_one("#detail-chart", PlotextPlot).loading = False
        self._status(f"[red]{message}[/red]")

    def _show_history(self, df: pd.DataFrame) -> None:
        widget = self.query_one("#detail-chart", PlotextPlot)
        widget.loading = False
        if df is None or df.empty:
            self._status("No price history available for this symbol.")
            return
        self.df = df
        months = RANGE_MONTHS[self.range_key]
        cutoff = date.today() - timedelta(days=int(months * 30.44))
        view = df[df["date"] >= cutoff]
        if view.empty:
            view = df
        dark = self.app.current_theme.dark
        price_chart(
            widget.plt,
            list(view["date"]),
            list(view["close"]),
            f"{self.symbol.upper()} close",
            dark,
        )
        widget.refresh()
        self._render_stats(df, view)
        budget = self.app.budget  # type: ignore[attr-defined]
        used = f" {budget.spent}/{budget.effective_limit} req today." if budget.spent else ""
        self._status(
            f"{len(view)} sessions shown, {len(df)} cached.{used} "
            "Keys: 1 3 6 y 5 range, a watch, b basket, o open site."
        )

    def _render_stats(self, df: pd.DataFrame, view: pd.DataFrame) -> None:
        closes = df["close"].astype(float)
        last = closes.iloc[-1]
        prev = closes.iloc[-2] if len(closes) > 1 else last
        chg = (last / prev - 1.0) * 100.0 if prev else 0.0
        rets = analytics.log_returns(df.set_index("date")[["close"]].rename(
            columns={"close": self.symbol.upper()}
        ))
        vol30 = (
            float(rets.tail(30).std().iloc[0]) * (252 ** 0.5) * 100.0
            if len(rets) >= 10
            else float("nan")
        )
        year = df[df["date"] >= date.today() - timedelta(days=365)]
        hi52 = float(year["close"].max()) if not year.empty else float("nan")
        lo52 = float(year["close"].min()) if not year.empty else float("nan")
        period_ret = (
            (view["close"].iloc[-1] / view["close"].iloc[0] - 1.0) * 100.0
            if len(view) > 1
            else 0.0
        )
        # Futures series report a volume column of zeros rather than omitting it.
        volume = df["volume"].dropna()
        mean_vol = float(volume.tail(30).mean()) if not volume.empty else 0.0
        avg_vol = f"{mean_vol:,.0f}" if mean_vol > 0 else "-"
        vol_text = f"{vol30:.1f}% ann" if vol30 == vol30 else "-"
        theme = self.app.current_theme
        up = theme.variables.get("up-color", "green")
        down = theme.variables.get("down-color", "red")
        color = up if chg >= 0 else down
        self.query_one("#detail-stats", Static).update(
            f"[b]Last[/b]        {fmt_price(last)}\n"
            f"[b]Day[/b]         [{color}]{chg:+.2f}%[/]\n"
            f"[b]Period[/b]      {period_ret:+.2f}%\n"
            f"[b]Vol 30d[/b]     {vol_text}\n"
            f"[b]52w high[/b]    {fmt_price(hi52)}\n"
            f"[b]52w low[/b]     {fmt_price(lo52)}\n"
            f"[b]Avg volume[/b]  {avg_vol}\n"
            f"[b]First date[/b]  {df['date'].iloc[0]}\n"
            f"[b]Last date[/b]   {df['date'].iloc[-1]}"
        )

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_range(self, key: str) -> None:
        if key not in RANGE_MONTHS:
            return
        self.range_key = key
        self._set_header()
        self._load()

    def action_reload(self) -> None:
        self._load()

    def action_watch(self) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        app.add_to_watchlist(self.symbol, self.display_name)

    def action_basket(self) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        app.toggle_basket(self.symbol)

    def action_open_site(self) -> None:
        webbrowser.open(f"https://stooq.com/q/?s={self.symbol}")


class AnalyticsScreen(Screen):
    """Correlations, rolling correlations, GARCH volatility, and summary stats
    for the basket of selected symbols."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("a", "add_symbol", "Add"),
        Binding("x", "remove_symbol", "Remove"),
        Binding("w", "cycle_window", "Window"),
        Binding("t", "cycle_years", "Span"),
        Binding("r", "recompute", "Recompute"),
    ]

    WINDOWS = [30, 60, 90, 120]
    YEARS = [1, 2, 3, 5]

    def compose(self) -> ComposeResult:
        yield Label("", id="analytics-header")
        with TabbedContent(id="analytics-tabs"):
            with TabPane("Correlation", id="tab-corr"):
                yield DataTable(id="corr-table")
            with TabPane("Rolling correlation", id="tab-roll"):
                yield PlotextPlot(id="roll-chart")
            with TabPane("Volatility (GARCH)", id="tab-garch"):
                with Horizontal(id="garch-body"):
                    yield PlotextPlot(id="garch-chart")
                    yield DataTable(id="garch-table")
            with TabPane("Statistics", id="tab-stats"):
                yield DataTable(id="stats-table")
        yield Static("", id="analytics-status")
        yield Footer()

    def on_mount(self) -> None:
        for table_id in ("#corr-table", "#garch-table", "#stats-table"):
            table = self.query_one(table_id, DataTable)
            table.zebra_stripes = True
            table.cursor_type = "row"
        self._update_header()
        self._recompute()

    # -- state helpers ------------------------------------------------------

    @property
    def state(self) -> store.AppState:
        return self.app.state  # type: ignore[attr-defined]

    def _update_header(self) -> None:
        basket = ", ".join(s.upper() for s in self.state.basket) or "empty"
        self.query_one("#analytics-header", Label).update(
            f"[b]Analytics[/b]  [dim]basket:[/dim] {basket}   "
            f"[dim]window:[/dim] {self.state.rolling_window}d   "
            f"[dim]span:[/dim] {self.state.history_years}y"
        )

    def _status(self, text: str) -> None:
        self.query_one("#analytics-status", Static).update(text)

    # -- compute ------------------------------------------------------------

    CONFIRM_THRESHOLD = 12  # requests, above which we ask before spending

    def _recompute(self, confirmed: bool = False) -> None:
        if len(self.state.basket) < 2:
            self._status(
                "Add at least two symbols to the basket: press a here, or b on any "
                "symbol in the market views."
            )
            return
        app: StooqApp = self.app  # type: ignore[assignment]
        start = store.start_for_days(int(self.state.history_years * 365.25))
        cost = sum(store.estimate_pages(sym, start) for sym in self.state.basket)
        if cost == 0:
            # Everything needed is already cached, so this is free.
            self._compute()
            return
        if not confirmed and cost > self.CONFIRM_THRESHOLD:
            self.app.push_screen(
                ConfirmScreen(
                    "Download history?",
                    f"This needs about {cost} requests for "
                    f"{len(self.state.basket)} symbols over {self.state.history_years}y.\n"
                    f"Budget today: {app.budget.spent}/{app.budget.effective_limit} used, "
                    f"{app.budget.remaining} left.\n\n"
                    "Shorten the span with t, or remove symbols with x, to spend less.",
                ),
                lambda ok: self._recompute(confirmed=True) if ok else None,
            )
            return
        self._compute()

    @work(thread=True, exclusive=True, group="analytics")
    def _compute(self) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        symbols = list(self.state.basket)
        years = self.state.history_years
        window = self.state.rolling_window
        start = store.start_for_days(int(years * 365.25))
        self.app.call_from_thread(self._begin_loading)
        histories: dict[str, pd.DataFrame] = {}
        try:
            for i, sym in enumerate(symbols, 1):
                self.app.call_from_thread(
                    self._status, f"Loading history {i}/{len(symbols)}: {sym.upper()}"
                )
                histories[sym] = store.get_history(
                    app.client, sym, start=start, budget=app.budget
                )
        except (StooqError, store.BudgetExhausted) as exc:
            self.app.call_from_thread(self._fail, str(exc))
            return

        self.app.call_from_thread(self._status, "Computing correlations...")
        closes = analytics.align_closes(histories)
        if closes.empty or len(closes) < 30 or closes.shape[1] < 2:
            self.app.call_from_thread(
                self._fail,
                "Not enough overlapping history between these symbols to run analytics.",
            )
            return
        rets = analytics.log_returns(closes)
        corr = analytics.corr_matrix(rets)
        roll = analytics.rolling_corr(rets, window)
        stats = analytics.summary_stats(closes)

        self.app.call_from_thread(self._status, "Fitting GARCH(1,1) models...")
        garch_series: dict[str, pd.Series] = {}
        garch_params: dict[str, dict] = {}
        for col in rets.columns:
            try:
                series, params = analytics.garch_vol(rets[col])
                garch_series[col] = series
                garch_params[col] = params
            except Exception:
                continue

        self.app.call_from_thread(
            self._show_results, corr, roll, stats, garch_series, garch_params, window
        )

    def _begin_loading(self) -> None:
        self.query_one("#roll-chart", PlotextPlot).loading = True
        self.query_one("#garch-chart", PlotextPlot).loading = True
        self.query_one("#corr-table", DataTable).loading = True
        self.query_one("#stats-table", DataTable).loading = True

    def _end_loading(self) -> None:
        self.query_one("#roll-chart", PlotextPlot).loading = False
        self.query_one("#garch-chart", PlotextPlot).loading = False
        self.query_one("#corr-table", DataTable).loading = False
        self.query_one("#stats-table", DataTable).loading = False

    def _fail(self, message: str) -> None:
        self._end_loading()
        self._status(f"[red]{message}[/red]")

    def _show_results(
        self,
        corr: pd.DataFrame,
        roll: pd.DataFrame,
        stats: pd.DataFrame,
        garch_series: dict[str, pd.Series],
        garch_params: dict[str, dict],
        window: int,
    ) -> None:
        self._end_loading()
        dark = self.app.current_theme.dark

        corr_table = self.query_one("#corr-table", DataTable)
        corr_table.clear(columns=True)
        corr_table.add_column("")
        for col in corr.columns:
            corr_table.add_column(col)
        for row_sym in corr.index:
            cells: list = [Text(str(row_sym), style="bold")]
            for col in corr.columns:
                value = corr.loc[row_sym, col]
                style = "bold" if row_sym == col else ""
                cells.append(Text(f"{value:+.2f}", style=style))
            corr_table.add_row(*cells)

        roll_chart = self.query_one("#roll-chart", PlotextPlot)
        if not roll.empty:
            shown = roll.iloc[:, :6]
            multi_line_chart(
                roll_chart.plt,
                shown,
                f"Rolling {window}d correlation of daily log returns",
                "Correlation",
                dark,
                hline=0.0,
            )
            roll_chart.refresh()

        garch_chart = self.query_one("#garch-chart", PlotextPlot)
        if garch_series:
            frame = pd.DataFrame(garch_series)
            multi_line_chart(
                garch_chart.plt,
                frame,
                "GARCH(1,1) conditional volatility, annualized",
                "Volatility (%)",
                dark,
            )
            garch_chart.refresh()

        garch_table = self.query_one("#garch-table", DataTable)
        garch_table.clear(columns=True)
        for col in ("Symbol", "omega", "alpha", "beta", "a+b", "LR vol"):
            garch_table.add_column(col)
        for sym, params in garch_params.items():
            # An integrated fit (alpha + beta at or above 1) has no finite
            # long-run variance, so there is no level to report.
            long_run = params["long_run_vol"]
            long_run_text = f"{long_run:.1f}%" if long_run == long_run else "-"
            garch_table.add_row(
                Text(sym, style="bold"),
                f"{params['omega']:.4f}",
                f"{params['alpha']:.3f}",
                f"{params['beta']:.3f}",
                f"{params['persistence']:.3f}",
                long_run_text,
            )

        stats_table = self.query_one("#stats-table", DataTable)
        stats_table.clear(columns=True)
        headers = [
            ("Symbol", "symbol"),
            ("Last", "last"),
            ("Ann ret %", "ann_return_pct"),
            ("Ann vol %", "ann_vol_pct"),
            ("Sharpe", "sharpe"),
            ("Skew", "skew"),
            ("Kurtosis", "kurtosis"),
            ("Max DD %", "max_dd_pct"),
            ("Obs", "obs"),
        ]
        for title, _ in headers:
            stats_table.add_column(title)
        for _, row in stats.iterrows():
            stats_table.add_row(
                Text(str(row["symbol"]), style="bold"),
                fmt_num(row["last"], 2),
                f"{row['ann_return_pct']:+.1f}",
                f"{row['ann_vol_pct']:.1f}",
                f"{row['sharpe']:+.2f}",
                f"{row['skew']:+.2f}",
                f"{row['kurtosis']:+.2f}",
                f"{row['max_dd_pct']:.1f}",
                str(int(row["obs"])),
            )

        pairs = "all pairs" if roll.shape[1] <= 6 else "first 6 pairs"
        self._status(
            f"Done. {len(corr)} symbols, rolling chart shows {pairs}. "
            "Keys: a add, x remove, w window, t span, r recompute."
        )

    # -- actions ------------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_add_symbol(self) -> None:
        def done(hit: SearchHit | None) -> None:
            if hit is None:
                return
            if hit.symbol not in self.state.basket:
                self.state.basket.append(hit.symbol)
                store.save_state(self.state)
            self._update_header()
            self._recompute()

        self.app.push_screen(AddSymbolScreen("Add symbol to basket"), done)

    def action_remove_symbol(self) -> None:
        if not self.state.basket:
            return

        def done(symbol: str | None) -> None:
            if symbol and symbol in self.state.basket:
                self.state.basket.remove(symbol)
                store.save_state(self.state)
            self._update_header()
            self._recompute()

        self.app.push_screen(
            PickSymbolScreen("Remove from basket", list(self.state.basket)), done
        )

    def action_cycle_window(self) -> None:
        current = self.state.rolling_window
        idx = self.WINDOWS.index(current) if current in self.WINDOWS else 0
        self.state.rolling_window = self.WINDOWS[(idx + 1) % len(self.WINDOWS)]
        store.save_state(self.state)
        self._update_header()
        self._recompute()

    def action_cycle_years(self) -> None:
        current = self.state.history_years
        idx = self.YEARS.index(current) if current in self.YEARS else 1
        self.state.history_years = self.YEARS[(idx + 1) % len(self.YEARS)]
        store.save_state(self.state)
        self._update_header()
        self._recompute()

    def action_recompute(self) -> None:
        self._recompute()


class PortfolioScreen(Screen):
    """Momentum signals, target weights and a backtest for the basket."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("g", "cycle('strategy')", "Strategy"),
        Binding("m", "cycle('method')", "Weighting"),
        Binding("k", "cycle('signal')", "Signal"),
        Binding("p", "cycle('lookback')", "Lookback"),
        Binding("n", "cycle('top_n')", "Hold N"),
        Binding("f", "cycle('frequency')", "Rebal"),
        Binding("v", "cycle('overlay')", "Overlay"),
        Binding("t", "cycle('span')", "Span"),
        Binding("S", "toggle_shorts", "Shorts", show=False),
        Binding("F", "toggle_filter", "Abs filter", show=False),
        Binding("r", "recompute", "Recompute"),
    ]

    LOOKBACKS = [63, 126, 252, 504]
    TOP_N = [1, 2, 3, 5, 8]
    SPANS = [2, 3, 5, 8]
    CONFIRM_THRESHOLD = 12

    def __init__(self) -> None:
        super().__init__()
        self.closes: pd.DataFrame | None = None
        self.result: backtest.BacktestResult | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="pf-header")
        yield Label("", id="pf-config")
        with TabbedContent(id="pf-tabs"):
            with TabPane("Signals", id="tab-signals"):
                yield DataTable(id="signal-table")
            with TabPane("Weights", id="tab-weights"):
                with Horizontal(id="weights-body"):
                    yield DataTable(id="weight-table")
                    yield Static("", id="weight-summary")
            with TabPane("Backtest", id="tab-backtest"):
                with Horizontal(id="backtest-body"):
                    yield PlotextPlot(id="bt-chart")
                    yield Static("", id="bt-stats")
        yield Static("", id="pf-status")
        yield Footer()

    def on_mount(self) -> None:
        for table_id in ("#signal-table", "#weight-table"):
            table = self.query_one(table_id, DataTable)
            table.zebra_stripes = True
            table.cursor_type = "row"
        self._update_header()
        self._recompute()

    # -- state --------------------------------------------------------------

    @property
    def state(self) -> store.AppState:
        return self.app.state  # type: ignore[attr-defined]

    @property
    def spec(self) -> signals.SignalSpec:
        return signals.SignalSpec(
            kind=self.state.signal_kind,
            lookback=self.state.signal_lookback,
        )

    def _update_header(self) -> None:
        state = self.state
        basket = ", ".join(s.upper() for s in state.basket) or "empty"
        self.query_one("#pf-header", Label).update(
            f"[b]Portfolio[/b]  [dim]basket:[/dim] {basket}"
        )
        strategy = backtest.STRATEGY_LABELS.get(state.strategy, state.strategy)
        method = portfolio.METHOD_LABELS.get(state.weight_method, state.weight_method)
        signal = signals.KIND_LABELS.get(state.signal_kind, state.signal_kind)
        overlay = portfolio.OVERLAY_LABELS.get(state.overlay, state.overlay)
        bits = [
            f"[dim]strategy[/dim] {strategy}",
            f"[dim]weights[/dim] {method}",
            f"[dim]signal[/dim] {signal} ({state.signal_lookback}d)",
        ]
        if state.strategy == "rotation":
            bits.append(f"[dim]hold[/dim] {state.top_n}")
        bits.append(f"[dim]rebal[/dim] {backtest.FREQUENCY_LABELS.get(state.rebalance)}")
        if state.overlay != "none":
            bits.append(f"[dim]overlay[/dim] {overlay}")
        if state.allow_shorts:
            bits.append("[dim]shorts[/dim] on")
        bits.append(f"[dim]span[/dim] {state.portfolio_years}y")
        self.query_one("#pf-config", Label).update("   ".join(bits))

    def _status(self, text: str) -> None:
        self.query_one("#pf-status", Static).update(text)

    # -- compute ------------------------------------------------------------

    def _recompute(self, confirmed: bool = False) -> None:
        if len(self.state.basket) < 1:
            self._status(
                "The basket is empty. Press b on any symbol in the market views to "
                "add it, then come back here."
            )
            return
        app: StooqApp = self.app  # type: ignore[assignment]
        start = store.start_for_days(int(self.state.portfolio_years * 365.25))
        cost = sum(store.estimate_pages(s, start) for s in self.state.basket)
        if cost > 0 and not confirmed and cost > self.CONFIRM_THRESHOLD:
            self.app.push_screen(
                ConfirmScreen(
                    "Download history?",
                    f"A {self.state.portfolio_years} year backtest of "
                    f"{len(self.state.basket)} symbols needs about {cost} requests.\n"
                    f"Budget today: {app.budget.spent}/{app.budget.effective_limit} used, "
                    f"{app.budget.remaining} left.\n\n"
                    "Shorten the span with t to spend less.",
                ),
                lambda ok: self._recompute(confirmed=True) if ok else None,
            )
            return
        self._compute()

    @work(thread=True, exclusive=True, group="portfolio")
    def _compute(self) -> None:
        app: StooqApp = self.app  # type: ignore[assignment]
        state = self.state
        symbols = list(state.basket)
        start = store.start_for_days(int(state.portfolio_years * 365.25))
        self.app.call_from_thread(self._begin_loading)

        histories: dict[str, pd.DataFrame] = {}
        try:
            for i, sym in enumerate(symbols, 1):
                self.app.call_from_thread(
                    self._status, f"Loading history {i}/{len(symbols)}: {sym.upper()}"
                )
                histories[sym] = store.get_history(
                    app.client, sym, start=start, budget=app.budget
                )
        except (StooqError, store.BudgetExhausted) as exc:
            self.app.call_from_thread(self._fail, str(exc))
            return

        closes = analytics.align_closes(histories)
        if closes.empty:
            self.app.call_from_thread(
                self._fail, "No overlapping price history for these symbols."
            )
            return
        closes.index = pd.to_datetime(closes.index)

        spec = self.spec
        self.app.call_from_thread(self._status, "Computing signals and weights...")
        scores = signals.compute(closes, spec)

        # Live target weights use the same code path the backtest uses at each
        # rebalance, so what you see is what the strategy would actually do.
        if state.strategy == "rotation":
            chosen = signals.select_top(scores, state.top_n, state.absolute_filter)
        else:
            chosen = list(closes.columns)

        rets = analytics.log_returns(closes)
        if chosen:
            weights_result = portfolio.compute_weights(
                method=state.weight_method,
                returns=rets[chosen],
                scores=scores.reindex(chosen),
                long_only=not state.allow_shorts,
                overlay=state.overlay,
                vol_target=state.vol_target,
                var_target=state.var_target,
            )
        else:
            weights_result = portfolio.WeightResult(
                pd.Series(dtype=float),
                state.weight_method,
                cash=1.0,
                note="no symbol passed the absolute momentum filter, so the "
                "strategy would hold cash",
            )

        self.app.call_from_thread(self._status, "Running backtest...")
        result = backtest.run(
            closes,
            strategy=state.strategy,
            method=state.weight_method,
            spec=spec,
            top_n=state.top_n,
            frequency=state.rebalance,
            long_only=not state.allow_shorts,
            overlay=state.overlay,
            vol_target=state.vol_target,
            var_target=state.var_target,
            absolute_filter=state.absolute_filter,
            progress=lambda msg: self.app.call_from_thread(
                self._status, f"Backtesting: {msg}"
            ),
        )
        benchmark = backtest.buy_and_hold_benchmark(closes)
        self.app.call_from_thread(
            self._show, closes, scores, chosen, weights_result, result, benchmark
        )

    def _begin_loading(self) -> None:
        for wid in ("#signal-table", "#weight-table"):
            self.query_one(wid, DataTable).loading = True
        self.query_one("#bt-chart", PlotextPlot).loading = True
        self._status("Loading...")

    def _end_loading(self) -> None:
        for wid in ("#signal-table", "#weight-table"):
            self.query_one(wid, DataTable).loading = False
        self.query_one("#bt-chart", PlotextPlot).loading = False

    def _fail(self, message: str) -> None:
        self._end_loading()
        self._status(f"[red]{message}[/red]")

    # -- render -------------------------------------------------------------

    def _show(self, closes, scores, chosen, weights_result, result, benchmark) -> None:
        self._end_loading()
        self.closes = closes
        self.result = result
        theme = self.app.current_theme
        up = theme.variables.get("up-color", "green")
        down = theme.variables.get("down-color", "red")

        self._show_signals(scores, chosen, up, down)
        self._show_weights(weights_result, chosen, scores, up, down)
        self._show_backtest(result, benchmark)

        note = f" {weights_result.note}." if weights_result.note else ""
        self._status(
            f"{len(closes)} aligned sessions from {closes.index[0].date()}."
            f"{note} Keys: g strategy, m weights, k signal, n hold, f rebal, v overlay."
        )

    def _show_signals(self, scores, chosen, up, down) -> None:
        table = self.query_one("#signal-table", DataTable)
        table.clear(columns=True)
        for col in ("Symbol", "Signal", "Rank", "Status"):
            table.add_column(col)
        ranks = signals.rank(scores)
        for sym in scores.sort_values(ascending=False, na_position="last").index:
            value = scores[sym]
            if value != value:  # NaN
                score_text = Text("-")
            else:
                colour = up if value > 0 else down if value < 0 else ""
                score_text = Text(f"{value:+.2%}", style=colour)
            held = sym in chosen
            status = Text("hold", style=up) if held else Text("out", style="dim")
            table.add_row(
                Text(str(sym), style="bold"),
                score_text,
                str(int(ranks[sym])) if ranks[sym] == ranks[sym] else "-",
                status,
            )

    def _show_weights(self, result, chosen, scores, up, down) -> None:
        table = self.query_one("#weight-table", DataTable)
        table.clear(columns=True)
        for col in ("Symbol", "Weight", "Risk share", "Signal"):
            table.add_column(col)
        weights = result.weights
        contributions = result.diagnostics.get("risk_contributions")
        if weights.empty:
            table.add_row(Text("cash", style="bold"), "100.00%", "-", "-")
        else:
            for sym in weights.sort_values(ascending=False).index:
                weight = weights[sym]
                risk = (
                    f"{contributions[sym]:.1%}"
                    if contributions is not None and sym in contributions.index
                    else "-"
                )
                score = scores.get(sym, float("nan"))
                style = up if weight > 0 else down if weight < 0 else "dim"
                table.add_row(
                    Text(str(sym), style="bold"),
                    Text(f"{weight:.2%}", style=style),
                    risk,
                    f"{score:+.2%}" if score == score else "-",
                )
            if result.cash > 1e-6:
                table.add_row(Text("cash", style="dim"), f"{result.cash:.2%}", "-", "-")

        diag = result.diagnostics
        vol = diag.get("vol", float("nan"))
        exp = diag.get("expected_return", float("nan"))
        sharpe = diag.get("sharpe", float("nan"))
        var = diag.get("var_95_1d", float("nan"))
        self.query_one("#weight-summary", Static).update(
            "[b]Target portfolio[/b]\n\n"
            f"Positions     {diag.get('n', 0)}\n"
            f"Invested      {result.gross:.1%}\n"
            f"Cash          {result.cash:.1%}\n\n"
            f"Forecast vol  {vol:.1%} ann\n"
            f"Expected ret  {exp:+.1%} ann\n"
            f"Sharpe        {sharpe:+.2f}\n"
            f"VaR 95% 1d    {var:.2%}\n\n"
            "[dim]Forecasts use the shrunk\n"
            "covariance of the selected span.\n"
            "Expected return is the sample\n"
            "mean, a weak predictor. Treat\n"
            "these as risk scale, not as a\n"
            "promise.[/dim]"
        )

    def _show_backtest(self, result, benchmark) -> None:
        widget = self.query_one("#bt-chart", PlotextPlot)
        stats_widget = self.query_one("#bt-stats", Static)
        if not result.ok:
            stats_widget.update(f"[b]Backtest[/b]\n\n{result.note}")
            widget.plt.clear_figure()
            widget.refresh()
            return
        dark = self.app.current_theme.dark
        frame = pd.DataFrame({"Strategy": result.equity})
        if benchmark is not None and not benchmark.empty:
            aligned = benchmark.reindex(result.equity.index).dropna()
            if not aligned.empty:
                frame["Equal weight buy and hold"] = aligned / aligned.iloc[0]
        multi_line_chart(
            widget.plt,
            frame,
            "Growth of 1.00",
            "Value",
            dark,
            hline=1.0,
        )
        widget.refresh()

        s = result.stats
        theme = self.app.current_theme
        up = theme.variables.get("up-color", "green")
        down = theme.variables.get("down-color", "red")
        ret_colour = up if s.get("total_return", 0) >= 0 else down
        stats_widget.update(
            "[b]Backtest[/b]\n\n"
            f"Total return  [{ret_colour}]{s.get('total_return', 0):+.1%}[/]\n"
            f"CAGR          {s.get('cagr', float('nan')):+.2%}\n"
            f"Volatility    {s.get('vol', float('nan')):.1%}\n"
            f"Sharpe        {s.get('sharpe', float('nan')):+.2f}\n"
            f"Sortino       {s.get('sortino', float('nan')):+.2f}\n"
            f"Max drawdown  {s.get('max_drawdown', float('nan')):.1%}\n"
            f"Calmar        {s.get('calmar', float('nan')):+.2f}\n"
            f"Hit rate      {s.get('hit_rate', float('nan')):.1%}\n\n"
            f"Rebalances    {s.get('rebalances', 0)}\n"
            f"Turnover      {s.get('turnover_per_year', float('nan')):.1f}x per year\n"
            f"Avg positions {s.get('avg_positions', float('nan')):.1f}\n"
            f"Avg cash      {s.get('cash_share', float('nan')):.1%}\n"
            f"Period        {s.get('years', 0):.1f} years\n\n"
            "[dim]Point in time: weights are set\n"
            "from data up to each rebalance\n"
            "and earn the next day's return.\n"
            "No costs, slippage or taxes.[/dim]"
        )

    # -- actions ------------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def _cycle_value(self, options: list, current):
        idx = options.index(current) if current in options else -1
        return options[(idx + 1) % len(options)]

    def action_cycle(self, what: str) -> None:
        state = self.state
        if what == "strategy":
            state.strategy = self._cycle_value(backtest.STRATEGIES, state.strategy)
        elif what == "method":
            state.weight_method = self._cycle_value(portfolio.METHODS, state.weight_method)
        elif what == "signal":
            state.signal_kind = self._cycle_value(signals.KINDS, state.signal_kind)
        elif what == "lookback":
            state.signal_lookback = self._cycle_value(self.LOOKBACKS, state.signal_lookback)
        elif what == "top_n":
            state.top_n = self._cycle_value(self.TOP_N, state.top_n)
        elif what == "frequency":
            state.rebalance = self._cycle_value(backtest.FREQUENCIES, state.rebalance)
        elif what == "overlay":
            state.overlay = self._cycle_value(portfolio.OVERLAYS, state.overlay)
        elif what == "span":
            state.portfolio_years = self._cycle_value(self.SPANS, state.portfolio_years)
        store.save_state(state)
        self._update_header()
        self._recompute()

    def action_toggle_shorts(self) -> None:
        self.state.allow_shorts = not self.state.allow_shorts
        store.save_state(self.state)
        self._update_header()
        self._recompute()

    def action_toggle_filter(self) -> None:
        self.state.absolute_filter = not self.state.absolute_filter
        store.save_state(self.state)
        self.notify(
            "Absolute momentum filter "
            + ("on: falling assets go to cash." if self.state.absolute_filter
               else "off: always fully invested in the top ranked."),
            timeout=4,
        )
        self._recompute()

    def action_recompute(self) -> None:
        self._recompute()


class StooqApp(App):
    """Market views, search, and analytics over Stooq data."""

    TITLE = "stooq"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("ctrl+l,slash", "focus_search", "Search"),
        Binding("1", "view('commodities')", "Cmdty", show=False),
        Binding("2", "view('indices')", "Idx", show=False),
        Binding("3", "view('fx')", "FX", show=False),
        Binding("4", "view('equities')", "Eq", show=False),
        Binding("5", "view('bonds')", "Bond", show=False),
        Binding("6", "view('macro')", "Macro", show=False),
        Binding("7", "view('watchlist')", "Watchlist"),
        Binding("left_square_bracket", "page(-1)", "Prev page", show=False),
        Binding("right_square_bracket", "page(1)", "Next page", show=False),
        Binding("a", "watch_selected", "Watch"),
        Binding("x", "unwatch_selected", "Unwatch", show=False),
        Binding("b", "basket_selected", "Basket"),
        Binding("A", "analytics", "Analytics"),
        Binding("P", "portfolio", "Portfolio"),
        Binding("s", "cycle_sort", "Sort", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("L", "cycle_limit", "Budget", show=False),
        Binding("f6", "toggle_theme", "Theme"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.state = store.load_state()
        self.client = StooqClient(store.cookie_path())
        self.budget = RequestBudget(store.budget_path())
        if self.state.daily_request_limit:
            self.budget.set_limit(self.state.daily_request_limit)
        self.rows: list[QuoteRow] = []
        self.page = 1
        self.max_page = 1
        self.sort_mode = 0
        self._search_hits: list[SearchHit] = []
        self._search_timer = None

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("[b]stooq[/b]", id="logo")
            with Horizontal(id="tabbar"):
                for cat in CATEGORIES:
                    yield Label(cat.label, id=f"tab-{cat.key}", classes="tab")
                yield Label("Watchlist", id=f"tab-{WATCHLIST_KEY}", classes="tab")
            yield Label("", id="page-indicator")
        yield Input(
            placeholder="Search symbols and instruments  (Ctrl+L, Enter to open)",
            id="search",
        )
        yield OptionList(id="suggestions")
        yield DataTable(id="quotes")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(STOOQ_LIGHT)
        self.register_theme(STOOQ_DARK)
        self.theme = (
            self.state.theme if self.state.theme in ("stooq-light", "stooq-dark") else "stooq-light"
        )
        table = self.query_one("#quotes", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self.query_one("#suggestions", OptionList).display = False
        if self.state.view not in VIEW_KEYS:
            self.state.view = DEFAULT_VIEW
        self._activate_view(self.state.view)
        table.focus()

    # -- view loading -------------------------------------------------------

    def _activate_view(self, key: str, page: int = 1) -> None:
        self.state.view = key
        store.save_state(self.state)
        self.page = page
        self.sort_mode = 0
        for tab in self.query(".tab"):
            tab.remove_class("active")
        self.query_one(f"#tab-{key}", Label).add_class("active")
        if key == WATCHLIST_KEY:
            self._load_watchlist()
        else:
            category = by_key(key)
            if category:
                self._load_category(category.stooq_id, page)

    def status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _page_indicator(self) -> None:
        label = self.query_one("#page-indicator", Label)
        parts = []
        if self.state.view != WATCHLIST_KEY and self.max_page > 1:
            parts.append(f"page {self.page}/{self.max_page}")
        parts.append(self._budget_badge())
        label.update("   ".join(parts))

    def _budget_badge(self) -> str:
        """Only worth showing once some of the day's allowance is gone."""
        spent, limit = self.budget.spent, self.budget.effective_limit
        if self.budget.blocked_today:
            return "[red]quota reached[/red]"
        if spent == 0:
            return ""
        style = "red" if self.budget.remaining <= 5 else "dim"
        return f"[{style}]{spent}/{limit} req[/{style}]"

    @work(thread=True, exclusive=True, group="load")
    def _load_category(self, category_id: int, page: int) -> None:
        self.call_from_thread(self._begin_table_loading, "Loading market data...")
        try:
            html = self.client.category_page(category_id, page)
            rows, max_page = parse_category(html)
        except StooqError as exc:
            self.call_from_thread(self._fail_table, str(exc))
            return
        self.call_from_thread(self._fill_table, rows, max_page)

    @work(thread=True, exclusive=True, group="load")
    def _load_watchlist(self) -> None:
        items = list(self.state.watchlist)
        if not items:
            self.call_from_thread(self._fill_table, [], 1)
            self.call_from_thread(
                self.status,
                "Watchlist is empty. Press a on any symbol, or search and press a.",
            )
            return
        self.call_from_thread(self._begin_table_loading, "Refreshing watchlist quotes...")
        rows: list[QuoteRow] = []
        for i, item in enumerate(items, 1):
            symbol, name = item["symbol"], item.get("name", "")
            self.call_from_thread(
                self.status, f"Quotes {i}/{len(items)}: {symbol.upper()}"
            )
            row = QuoteRow(symbol, name, None, None, None, "")
            try:
                hits = parse_search(self.client.search(symbol))
                for hit in hits:
                    if hit.symbol == symbol:
                        row.name = name or hit.name
                        try:
                            row.last = float(hit.last.replace(",", ""))
                        except ValueError:
                            pass
                        try:
                            row.change_pct = float(hit.change_pct.replace("%", ""))
                        except ValueError:
                            pass
                        break
            except StooqError:
                pass
            rows.append(row)
        self.call_from_thread(self._fill_table, rows, 1)

    def _begin_table_loading(self, message: str) -> None:
        self.query_one("#quotes", DataTable).loading = True
        self.status(message)

    def _fail_table(self, message: str) -> None:
        self.query_one("#quotes", DataTable).loading = False
        self.status(f"[red]{message}[/red]")

    def _fill_table(self, rows: list[QuoteRow], max_page: int) -> None:
        self.rows = rows
        self.max_page = max_page
        self._render_rows()
        table = self.query_one("#quotes", DataTable)
        table.loading = False
        self._page_indicator()
        view = self.state.view
        if view == WATCHLIST_KEY:
            if rows:
                self.status(
                    f"Watchlist: {len(rows)} symbols. Enter opens, x removes, b baskets."
                )
        else:
            label = by_key(view).label if by_key(view) else view
            self.status(
                f"{label}: {len(rows)} instruments on this page. "
                "Enter opens a symbol, ? for all keys."
            )

    def _sorted_rows(self) -> list[QuoteRow]:
        mode = SORT_MODES[self.sort_mode]
        rows = list(self.rows)
        if mode == "chg desc":
            rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else -1e9,
                      reverse=True)
        elif mode == "chg asc":
            rows.sort(key=lambda r: r.change_pct if r.change_pct is not None else 1e9)
        elif mode == "last desc":
            rows.sort(key=lambda r: r.last if r.last is not None else -1e9, reverse=True)
        elif mode == "name asc":
            rows.sort(key=lambda r: r.name.lower())
        return rows

    def _render_rows(self) -> None:
        table = self.query_one("#quotes", DataTable)
        table.clear(columns=True)
        table.add_column("Symbol", key="symbol")
        table.add_column("Name", width=38, key="name")
        table.add_column("Last", key="last")
        table.add_column("Change %", key="pct")
        table.add_column("Change", key="abs")
        table.add_column("Updated", key="when")
        in_watch = set(self.state.watchlist_symbols())
        in_basket = set(self.state.basket)
        for row in self._sorted_rows():
            marks = ""
            if row.symbol in in_watch:
                marks += "*"
            if row.symbol in in_basket:
                marks += "+"
            symbol_text = Text(row.symbol.upper())
            if marks:
                symbol_text.append(f" {marks}", style="dim")
            pct_text, abs_text = change_text(row.change_pct, row.change_abs, self)
            table.add_row(
                symbol_text,
                row.name[:38],
                fmt_price(row.last),
                pct_text,
                abs_text,
                row.when,
                key=row.symbol,
            )

    # -- selection helpers --------------------------------------------------

    def _selected_row(self) -> QuoteRow | None:
        table = self.query_one("#quotes", DataTable)
        if not self.rows or table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        for row in self.rows:
            if row.symbol == row_key.value:
                return row
        return None

    def add_to_watchlist(self, symbol: str, name: str) -> None:
        symbol = symbol.lower()
        if symbol in self.state.watchlist_symbols():
            self.notify(f"{symbol.upper()} is already on the watchlist.", timeout=3)
            return
        self.state.watchlist.append({"symbol": symbol, "name": name})
        store.save_state(self.state)
        self.notify(f"Added {symbol.upper()} to the watchlist.", timeout=3)
        if self.state.view == WATCHLIST_KEY:
            self._load_watchlist()
        else:
            self._render_rows()

    def remove_from_watchlist(self, symbol: str) -> None:
        symbol = symbol.lower()
        before = len(self.state.watchlist)
        self.state.watchlist = [
            item for item in self.state.watchlist if item["symbol"] != symbol
        ]
        if len(self.state.watchlist) != before:
            store.save_state(self.state)
            self.notify(f"Removed {symbol.upper()} from the watchlist.", timeout=3)
        if self.state.view == WATCHLIST_KEY:
            self._load_watchlist()
        else:
            self._render_rows()

    def toggle_basket(self, symbol: str) -> None:
        symbol = symbol.lower()
        if symbol in self.state.basket:
            self.state.basket.remove(symbol)
            self.notify(f"{symbol.upper()} removed from analytics basket.", timeout=3)
        else:
            self.state.basket.append(symbol)
            self.notify(
                f"{symbol.upper()} added to analytics basket "
                f"({len(self.state.basket)} total). Press A to analyze.",
                timeout=4,
            )
        store.save_state(self.state)
        self._render_rows()

    # -- search -------------------------------------------------------------

    @on(Input.Changed, "#search")
    def _search_changed(self, event: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        query = event.value.strip()
        if len(query) < 2:
            self._hide_suggestions()
            return
        self._search_timer = self.set_timer(0.35, lambda: self._run_search(query))

    @work(thread=True, exclusive=True, group="search")
    def _run_search(self, query: str) -> None:
        try:
            hits = parse_search(self.client.search(query))[:9]
        except StooqError:
            hits = []
        self.call_from_thread(self._show_suggestions, hits)

    def _show_suggestions(self, hits: list[SearchHit]) -> None:
        self._search_hits = hits
        options = self.query_one("#suggestions", OptionList)
        options.clear_options()
        if not hits:
            options.display = False
            return
        for hit in hits:
            change = hit.change_pct or ""
            label = (
                f"{hit.symbol.upper():<12} {hit.name[:36]:<38} "
                f"{hit.market[:10]:<11} {hit.last:>10} {change:>8}"
            )
            options.add_option(Option(label, id=hit.symbol))
        options.display = True
        options.highlighted = 0

    def _hide_suggestions(self) -> None:
        self._search_hits = []
        options = self.query_one("#suggestions", OptionList)
        options.clear_options()
        options.display = False

    def _hit_by_id(self, option_id: str | None) -> SearchHit | None:
        for hit in self._search_hits:
            if hit.symbol == option_id:
                return hit
        return None

    @on(Input.Submitted, "#search")
    def _search_submitted(self) -> None:
        options = self.query_one("#suggestions", OptionList)
        if self._search_hits and options.display:
            index = options.highlighted or 0
            hit = self._search_hits[min(index, len(self._search_hits) - 1)]
            self._open_hit(hit)

    @on(OptionList.OptionSelected, "#suggestions")
    def _suggestion_selected(self, event: OptionList.OptionSelected) -> None:
        hit = self._hit_by_id(event.option.id)
        if hit:
            self._open_hit(hit)

    def _open_hit(self, hit: SearchHit) -> None:
        self._hide_suggestions()
        self.query_one("#search", Input).value = ""
        self.query_one("#quotes", DataTable).focus()
        self.push_screen(DetailScreen(hit.symbol, hit.name))

    def on_key(self, event) -> None:
        search = self.query_one("#search", Input)
        options = self.query_one("#suggestions", OptionList)
        if search.has_focus and options.display and options.option_count:
            if event.key in ("down", "up"):
                delta = 1 if event.key == "down" else -1
                current = options.highlighted or 0
                options.highlighted = max(
                    0, min(options.option_count - 1, current + delta)
                )
                event.stop()
            elif event.key == "escape":
                self._hide_suggestions()
                self.query_one("#quotes", DataTable).focus()
                event.stop()
        elif search.has_focus and event.key == "escape":
            search.value = ""
            self.query_one("#quotes", DataTable).focus()
            event.stop()

    # -- table interaction --------------------------------------------------

    @on(DataTable.RowSelected, "#quotes")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        symbol = event.row_key.value
        for row in self.rows:
            if row.symbol == symbol:
                self.push_screen(DetailScreen(row.symbol, row.name))
                return

    # -- actions ------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_view(self, key: str) -> None:
        if key in VIEW_KEYS:
            self._activate_view(key)

    def action_page(self, delta: int) -> None:
        if self.state.view == WATCHLIST_KEY:
            return
        new_page = max(1, min(self.max_page, self.page + delta))
        if new_page != self.page:
            self._activate_view(self.state.view, page=new_page)

    def action_refresh(self) -> None:
        self._activate_view(self.state.view, page=self.page)

    LIMITS = [60, 120, 200, 300]

    def action_cycle_limit(self) -> None:
        current = self.budget.effective_limit
        nxt = next((v for v in self.LIMITS if v > current), self.LIMITS[0])
        self.budget.set_limit(nxt)
        self.state.daily_request_limit = nxt
        store.save_state(self.state)
        self._page_indicator()
        capped = self.budget.effective_limit
        note = (
            f" (held at {capped}, the safe share of Stooq's observed limit)"
            if capped != nxt
            else ""
        )
        self.notify(
            f"Daily request budget set to {nxt}{note}. "
            f"{self.budget.remaining} left today.",
            timeout=5,
        )

    def action_watch_selected(self) -> None:
        row = self._selected_row()
        if row:
            self.add_to_watchlist(row.symbol, row.name)

    def action_unwatch_selected(self) -> None:
        row = self._selected_row()
        if row:
            self.remove_from_watchlist(row.symbol)

    def action_basket_selected(self) -> None:
        row = self._selected_row()
        if row:
            self.toggle_basket(row.symbol)

    def action_analytics(self) -> None:
        self.push_screen(AnalyticsScreen())

    def action_portfolio(self) -> None:
        self.push_screen(PortfolioScreen())

    def action_cycle_sort(self) -> None:
        self.sort_mode = (self.sort_mode + 1) % len(SORT_MODES)
        self._render_rows()
        self.status(f"Sort: {SORT_MODES[self.sort_mode]}")

    def action_toggle_theme(self) -> None:
        self.theme = "stooq-dark" if self.theme == "stooq-light" else "stooq-light"
        self.state.theme = self.theme
        store.save_state(self.state)
        self._render_rows()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


def run() -> None:
    StooqApp().run()
