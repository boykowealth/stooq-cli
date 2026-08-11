# Stooq CLI

A quantitative markets terminal for [Stooq](https://stooq.com) data that runs entirely in your
terminal. Browse commodities, indices, FX, equities, bonds and macro series in clean tables,
chart any symbol, run correlation and volatility models across a basket you assemble as you go,
and size a portfolio with momentum signals, standard weighting methodologies and a point in time
backtest.

Type `stooq`, and the markets are there.

```
 stooq   Commodities  Indices  FX  Equities  Bonds  Macro  Watchlist        page 1/7
 Search symbols and instruments  (Ctrl+L, Enter to open)

 Symbol        Name                                    Last    Change %   Change   Updated
 CL.F          Crude Oil WTI                          77.09      -1.52%    -1.19    Aug 7
 CB.F          Crude Oil Brent - ICE                  83.55      +1.29%    +1.06    Aug 7
 GC.F          Gold                                4,401.30      +0.44%   +19.40    Aug 7
 HG.F          Copper                                  5.12      -0.87%    -0.04    Aug 7
 NG.F          Natural Gas                              4.38     +2.11%    +0.09    Aug 7

 Commodities: 100 instruments on this page. Enter opens a symbol, ? for all keys.
```

## Features

- **Six market views out of the box.** Commodities (the default), Indices, FX, Equities, Bonds
  and Macro, each paged so long lists stay navigable. Switch with the number keys.
- **Practical search.** Type a name or a ticker and results appear as you type, with the
  instrument name, market and current quote. Enter opens the chart.
- **Watchlist.** Press `a` on anything to keep it; the watchlist is its own view and survives
  restarts.
- **Symbol view.** A labelled price chart with selectable range (1 month through 5 years)
  alongside last price, period return, 30-day realized volatility, 52-week range and volume.
- **Analytics on a basket you build.** Press `b` to add any symbol to the basket, then `A` to open:
  - a correlation matrix of daily log returns,
  - rolling pairwise correlations over a window you can cycle,
  - GARCH(1,1) conditional volatility with the fitted parameters,
  - a summary table of annualized return, volatility, Sharpe, skew, kurtosis and max drawdown.
- **Portfolio construction and momentum rotation.** Press `P` on the same basket to get momentum
  rankings, target position sizes under the weighting method of your choice, and a point in time
  backtest of the whole rule set against an equal weight benchmark.
- **Loading states everywhere.** Fetches and model fits show progress rather than freezing, and
  every screen reports what it is doing on its status line.
- **A cache that respects the source.** Daily history is stored locally, so only the days you are
  missing are ever requested. Reopening a symbol is instant.
- **Light and dark.** An off-white and off-black palette with a single blue accent. `F6` toggles,
  and your choice is remembered.
- **It does not break.** Network failures, missing series and Stooq's daily request limit all
  produce a clear message on the status line, never a traceback. When the limit is reached,
  cached symbols keep working.

## Requirements

Python 3.10 or newer, and a terminal that supports 256 colours (essentially all modern ones).

## Installation

The recommended route puts a `stooq` command on your `PATH` in its own isolated environment:

```bash
# Install pipx once, if you do not already have it
python3 -m pip install --user pipx && python3 -m pipx ensurepath

# Install stooq-cli
pipx install git+https://github.com/boykowealth/stooq-cli.git
```

To install from a local clone instead:

```bash
git clone https://github.com/boykowealth/stooq-cli.git
pipx install ./stooq-cli
```

<details>
<summary>Prefer plain pip?</summary>

```bash
pip install --user git+https://github.com/boykowealth/stooq-cli.git
```

Make sure your user scripts directory is on your `PATH`, then run `stooq`.
</details>

## Usage

```bash
stooq              # launch the terminal
stooq --version    # print the version
stooq --help       # command line help
```

Everything inside is keyboard driven. Press `?` at any time for the full list.

### Navigation

| Key | Action |
| --- | --- |
| `1` to `6` | Market views: Commodities, Indices, FX, Equities, Bonds, Macro |
| `7` | Watchlist |
| `[` and `]` | Previous and next page of the current view |
| `Enter` | Open the selected symbol |
| `Ctrl+L` or `/` | Focus the search bar |
| `Escape` | Close a screen, or dismiss search suggestions |

### Lists

| Key | Action |
| --- | --- |
| `a` | Add the selected symbol to the watchlist |
| `x` | Remove the selected symbol from the watchlist |
| `b` | Add or remove the selected symbol from the analytics basket |
| `s` | Cycle sorting: default, change descending or ascending, last, name |
| `r` | Refresh |

Symbols on your watchlist are marked with `*`, and those in the analytics basket with `+`.

### Symbol view

| Key | Action |
| --- | --- |
| `1` `3` `6` `y` `5` | Chart range: 1 month, 3 months, 6 months, 1 year, 5 years |
| `a` | Add to watchlist |
| `b` | Add to or remove from the analytics basket |
| `o` | Open this symbol on stooq.com in your browser |
| `r` | Refresh |

### Analytics

| Key | Action |
| --- | --- |
| `A` | Open analytics for the current basket (from anywhere) |
| `a` | Search for and add a symbol to the basket |
| `x` | Remove a symbol from the basket |
| `w` | Cycle the rolling window: 30, 60, 90, 120 days |
| `t` | Cycle the history span: 1, 2, 3, 5 years |
| `r` | Recompute |

Use the tabs to move between the correlation matrix, rolling correlations, GARCH volatility and
the summary statistics table.

### Portfolio

Press `P` from anywhere. Every control recomputes the signals, the target weights and the
backtest together, so you always see one consistent strategy.

| Key | Action |
| --- | --- |
| `g` | Strategy: momentum rotation, or buy and hold with rebalancing |
| `m` | Weighting: equal, inverse volatility, risk parity, minimum variance, maximum Sharpe, momentum weighted |
| `k` | Momentum signal: total return, 12-1, risk adjusted, trend versus moving average |
| `p` | Signal lookback: 63, 126, 252 or 504 trading days |
| `n` | How many holdings a rotation keeps |
| `f` | Rebalance frequency: monthly, quarterly, yearly |
| `v` | Risk overlay: none, volatility target, Value at Risk target |
| `t` | History span used for signals and the backtest |
| `c` | Trading costs: commission, slippage and tax |
| `S` | Allow short positions |
| `F` | Absolute momentum filter, which sends falling assets to cash |
| `r` | Recompute |

### General

| Key | Action |
| --- | --- |
| `F6` | Toggle light and dark |
| `L` | Cycle the daily request budget: 60, 120, 200, 300 |
| `?` | Keyboard help |
| `q` or `Ctrl+Q` | Quit |

## Analytics detail

All models run on daily log returns, computed from the aligned closes of every symbol in the
basket. Alignment is an inner join on date, so only sessions where every symbol traded are used.
This matters when you mix instruments with different holiday calendars: adding a series with a
short overlap shortens the sample for everything.

**Correlation matrix.** Pearson correlation of daily log returns over the full aligned sample.

**Rolling correlation.** Pairwise correlations over a trailing window, so you can see a
relationship break down rather than only its average. With more than four symbols the chart shows
the first six pairs to stay readable; the correlation matrix still covers all of them.

**GARCH(1,1).** Fitted per symbol with a constant mean, using the `arch` package. The chart shows
conditional volatility annualized to a percentage. The table reports omega, alpha, beta, their
persistence (alpha plus beta) and the implied long-run volatility. When persistence reaches or
exceeds one, the process is integrated and has no finite long-run variance, so that cell shows a
dash rather than a misleading number.

**Summary statistics.** Annualized return and volatility (252 trading days), Sharpe ratio against
a zero risk-free rate, skew, excess kurtosis, maximum drawdown, and the number of observations.

## Portfolio methodology

### Momentum signals

| Signal | Definition |
| --- | --- |
| Total return | Simple return over the lookback |
| 12-1 | Return over the lookback excluding the most recent month |
| Risk adjusted | Lookback return divided by realized volatility over the same window |
| Trend | Percentage distance of price above its moving average |

The 12-1 form is the classic academic definition. Skipping the most recent month avoids the
short-term reversal that often follows a sharp move, and the test suite pins this property by
checking that a violent spike in the final month leaves a 12-1 score unchanged.

Ranking is cross-sectional: symbols compete against each other. The **absolute momentum filter**
is separate and is what allows the strategy to hold nothing. Ranking alone will always hold
something, even when every candidate is falling, so with the filter on, any symbol whose own
momentum has turned negative goes to cash instead.

### Weighting methodologies

| Method | What it solves for |
| --- | --- |
| Equal weight | Every position the same size |
| Inverse volatility | Size inversely to each asset's own volatility, ignoring correlation |
| Risk parity | Weights where every position contributes an equal share of portfolio risk |
| Minimum variance | The lowest variance combination |
| Maximum Sharpe | The tangency portfolio, highest return per unit of risk |
| Momentum weighted | Proportional to the momentum score |

The last four are solved numerically with SLSQP under a fully invested constraint and optional
long-only bounds. Covariance is shrunk toward its diagonal before use, because sample covariance
on short histories is noisy and often near-singular, which is what makes naive optimizers produce
wild, unstable weights. If an optimizer fails to converge, the method falls back to inverse
volatility and says so on the status line rather than failing.

### Risk overlays

A volatility target or Value at Risk target scales total exposure to hit the chosen risk level,
holding the remainder as cash. Value at Risk is one day at 95 percent under a normal assumption,
which understates tail risk in exactly the markets where it matters most. **Overlays only ever
scale exposure down, never above fully invested**, so this de-risks but never adds leverage.

### Backtesting

The engine is strictly point in time. At each rebalance the signal and covariance see only data up
to and including that date, and the resulting weights earn returns from the following day onward.
This is the difference between a backtest that means something and one that merely looks good, so
the test suite verifies it by truncation invariance: results before a cutoff must be identical
whether or not later data exists. If future prices leaked into past decisions, they would differ.

Reported statistics are total return, CAGR, volatility, Sharpe, Sortino, maximum drawdown, Calmar,
hit rate, turnover per year, average positions and average cash. An equal weight buy and hold line
is plotted alongside the strategy as a yardstick.

Positions drift with prices between rebalances. Only at a rebalance are they traded back to
target, which is what makes turnover, and therefore cost, meaningful.

### Trading costs, slippage and tax

Press `c` on the portfolio screen to edit all three. They are applied to the backtest, and the
stats panel reports gross return, net return and the drag between them, split into commission and
tax.

| Parameter | Default | Meaning |
| --- | --- | --- |
| Commission | 5 bps | Charged on the notional traded |
| Slippage | 5 bps | The gap between the price you assumed and the price you got |
| Tax | 0 percent | Charged on realized gains at each rebalance |

The defaults come to 10 bps per unit of notional traded, roughly 20 bps on a round trip, which is
realistic for a retail account in liquid instruments. Illiquid names, small caps and wide spreads
cost considerably more, so raise them if that is what you trade. Tax defaults to zero because it
depends entirely on your jurisdiction and account type.

Costs are charged on the day each new position starts earning, matching the rule that weights
decided at a close take effect the next day. A rebalance falling on the last day of the data is
therefore free, because the position it would have opened never existed.

Two things to keep in mind about the tax figure. It is **an approximation without lot tracking**:
at each rebalance it charges the tax rate on the share of the book being sold multiplied by the
gain since the previous rebalance. There is no distinction between short and long term rates and
no loss carryforward. It also correctly charges nothing on a losing strategy, since there are no
gains to tax.

The remaining honest limitation is that **expected returns in the maximum Sharpe optimizer are
sample means**, a notoriously weak predictor of future returns. That method is the most sensitive
to estimation error of the six, and its weights will move a lot with the span you choose. Treat
the forecast panel as a guide to risk scale, not as a prediction.

## How it works

Stooq does not offer a public API, so `stooq` reads the same pages a browser does:

1. **Session.** Stooq protects its pages with a small proof-of-work check. The client computes the
   same SHA-256 nonce a browser would, exchanges it for a session cookie, and reuses that cookie
   across runs.
2. **Fetch.** Category tables, historical quote pages and the search endpoint are requested with a
   minimum interval between calls, so the terminal stays a polite client.
3. **Parse.** Pages are parsed defensively with BeautifulSoup. A row that does not match the
   expected shape is skipped rather than crashing a view.
4. **Cache.** Daily history is written to a CSV per symbol, and later runs request only the date
   range that is actually missing.

### The daily request limit

Stooq caps how much historical data an anonymous address can pull per day. The terminal is built
so that this never locks you out of the app itself.

**Only downloading new history costs anything.** Market views, category tables, search and quotes
are not subject to the limit, and neither is anything already in your cache. Even with the daily
allowance completely spent, you can still browse every view, search, and open any symbol you have
looked at before.

**The app stops before Stooq does.** It counts its own history requests against a daily budget of
120 by default and refuses to go over, keeping a reserve rather than discovering the ceiling by
getting cut off. The counter appears in the top right once you have used some of it. Press `L` to
cycle the budget between 60, 120, 200 and 300.

**The budget learns.** If Stooq ever refuses a request anyway, the spend at that moment is
recorded as an upper bound on the real limit, and later days stay below it. A refusal that arrives
before any meaningful spending is ignored on purpose: Stooq's day rolls over on its own clock, not
your local midnight, so the first request of your day can be refused on yesterday's allowance, and
treating that as the real limit would cap the app near zero permanently.

**Requests are proportional to what you ask for.** Stooq serves history 40 rows per page with no
way to request more, so cost scales with the span you chart: roughly one request per month of
daily data. A one month chart costs one request, a year costs about seven, five years about
thirty-two. The cache means you pay this only once per symbol, and only for days you do not
already have. Before an analytics run that needs more than a dozen requests, the app tells you the
estimated cost and asks first.

The practical advice: charting and browsing are cheap, and re-opening symbols is free. Pulling
five years of daily history for a large basket in one sitting is the only thing that will make a
dent.

Data is retrieved from Stooq for personal use. Any commercial use is prohibited by Stooq's terms.
Check them before relying on this for anything else.

## Configuration

There is nothing to configure to get started. State is written under your platform's standard
directories:

| What | Location (Linux) |
| --- | --- |
| Watchlist, basket, theme, settings | `~/.local/share/stooq-cli/state.json` |
| Daily request budget and learned limit | `~/.local/share/stooq-cli/budget.json` |
| Price history cache and cookies | `~/.cache/stooq-cli/` |

macOS and Windows use their respective equivalents. Deleting the cache directory is always safe;
it will be rebuilt on demand.

## Development

```bash
git clone https://github.com/boykowealth/stooq-cli.git
cd stooq-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                 # run the test suite
ruff check .           # lint
python -m stooq_cli    # run from source
```

The test suite runs offline. Parser tests work against saved Stooq pages in `tests/fixtures`,
analytics tests use synthetic series with known properties, and the app tests drive the real UI
through Textual's pilot with the network stubbed out.

## Project layout

| Module | Responsibility |
| --- | --- |
| `client.py` | HTTP session, proof-of-work handshake, pacing, refusal detection |
| `budget.py` | Daily request cap, with adaptive learning of Stooq's real limit |
| `scrape.py` | Parsers for category tables, historical quotes and search |
| `store.py` | History cache and persisted application state |
| `analytics.py` | Returns, correlations, GARCH, summary statistics |
| `signals.py` | Momentum signals, ranking and the absolute momentum filter |
| `portfolio.py` | Weighting methodologies, optimizers and risk overlays |
| `backtest.py` | Point in time strategy replay and performance statistics |
| `charts.py` | Themed, labelled plots |
| `app.py` | Screens, key bindings and the terminal itself |

## Roadmap

- Export the current view, weights or backtest to CSV
- Additional volatility models (EGARCH, GJR-GARCH)
- Principal component analysis across a basket
- Walk-forward and out-of-sample testing
- Custom user-defined views

## License

MIT, Brayden Boyko
