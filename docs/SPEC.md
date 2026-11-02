# Why Did My Stock Move?

## 1. Purpose

Explain a US stock's price move by splitting it into market, sector, and
company-specific parts, with statistical uncertainty, and show what else
happened around it (headlines, company filings, insider trades).

Descriptive, not predictive. Not investment advice.

## 2. Scope

In scope
- US-listed common stocks in the Russell 3000 (IWV holdings snapshot), after a
  data-quality filter
- Daily and 5-minute price data
- Headlines tagged to tickers, SEC 8-K filings, SEC Form 4 insider filings

Out of scope
- Non-US markets (the data layer is designed so a market can be added later)
- Trading signals, price forecasts, sentiment scoring
- Detecting illegal insider trading (Form 4 is lawful, after-the-fact disclosure)
- Pairs trading, hedging tools, options

## 3. Data sources

| Source | Used for | Notes |
|---|---|---|
| Alpaca market data (free) | Daily and intraday bars, headlines | IEX feed unless SIP is shown to work; about 200 requests/min |
| SEC EDGAR | Form 4, 8-K, ticker to CIK | No key; User-Agent with contact email required; 10 requests/s |
| iShares IWV holdings | Universe and sectors | Dated snapshot committed to the repo |
| Select Sector SPDR ETFs | Sector factors | 11 ETFs via a sector-to-ETF table |

To verify before building on them
- [ ] Alpaca news works on the free plan
- [ ] IEX vs SIP daily-bar coverage for thin stocks
- [ ] 5-minute bar density on IEX
- [ ] IWV holdings file: columns, sector field, terms of use
- [ ] EDGAR requests succeed from the deployed host

## 4. Panels and statistics

| Panel | What the user sees | Statistics |
|---|---|---|
| Day decomposition | A day's return split into market, sector, unexplained | Two-factor regression, Newey-West uncertainty bands |
| Year attribution | A period's return split the same way | Cumulative attribution, date-range slider |
| Unusual-move score | How surprising the unexplained part is | Residual over recent volatility, empirical tail percentile, expected false alarms |
| Timeline | Headlines, 8-K filings, insider trades as markers | Event study around marker dates |
| Reaction chips | Abnormal move before and after each headline | Same factor model on 5-minute bars, time-of-day volatility |
| Insider panel | Trades sorted into informative and routine | Event study from the filing date, permutation test, pooled study |
| Peer grid | Live correlation grid of the closest stocks | Residual correlation, held-out validation, cointegration badge |
| Variance split and model health | Share of variance from market, sector, own; fit quality | Bootstrap intervals, residual diagnostics |

## 5. Method

Factor model, estimated only on data before the day being explained
- r_stock = alpha + b_mkt * r_mkt + b_sec * r_sector_pure + e
- Market factor: IWV. Sector factor: the sector ETF, regressed on the market
  first so the two factors do not overlap (r_sector_pure is its residual)
- Estimation window: trailing 252 trading days ending the previous day

Unusual-move score
- Residual divided by EWMA volatility, plus an empirical percentile
- Always show how many flags chance alone would produce

Intraday reactions
- 5-minute returns; residual volatility estimated per time-of-day bucket
- Show the abnormal move before the headline next to the move after it
- Headlines within minutes of each other are grouped and not separated
- After-hours headlines use the close-to-open move

Insider study
- Abnormal returns are measured from the filing date, never the trade date
- Compare real insider purchases with the same number of random dates
- Pool across the universe in a batch job and cache; show confidence intervals

Peer grid
- Rank all stocks by residual correlation on an earlier window only
- Take the top 99, order by clustering, validate on the later window
- Show selection-window and validation-window correlations side by side
- Cointegration (Engle-Granger) on the top 10 as a secondary badge,
  Benjamini-Hochberg correction across those 10 tests

## 6. Architecture

- domain: pure statistics, no I/O, no framework imports
- services: orchestration using ports (interfaces)
- adapters: Alpaca, EDGAR, news, storage; fakes for tests
- api: FastAPI, versioned under /api/v1, typed schemas, one error format,
  Server-Sent Events for the streaming grid
- Nightly job: validates and loads daily bars into a parquet file, loads
  filings and headlines into Postgres
- At startup the API loads the returns matrix into memory; correlation ranking
  is computed per request
- Frontend: React and TypeScript, typed API client, canvas for the grid

## 7. Engineering standards

- ruff (lint and format), mypy strict, pytest with coverage, run in CI on
  every push
- Statistics tested against synthetic data with planted effects (known betas,
  a known shock at a known minute, no look-ahead)
- Config through pydantic-settings, no secret defaults, fail fast at startup
- Structured logs with request IDs, /health endpoint
- Docker image identical locally and in production
- Short decision notes in docs/decisions for each major choice

## 8. Limits stated on the site

- Unexplained is not the same as news: flows, style effects, and noise land
  there too
- The two-factor model fits large caps better than small caps (R-squared shown)
- Free price data is limited; incomplete stocks are dropped
- Survivorship bias: the universe is today's list
- Most insider sales are routine; Form 4 is filed up to two business days late
- Headline timing shows coincidence, not causation
- Correlations drift; peers are historical, not predictive

## 9. Milestones

Each milestone ends typed, tested, and deployed.

| # | Milestone |
|---|---|
| M0 | Skeleton, CI, Docker, first deploy (backend and frontend) |
| M1 | Statistics core with synthetic-data tests |
| M2 | Data adapters and nightly job (prices, EDGAR, news) |
| M3 | Day decomposition and year attribution, API and UI |
| M4 | Timeline, 8-K and Form 4 markers, insider panel |
| M5 | Reaction chips (intraday) |
| M6 | Peer grid, live streaming, replay |
| M7 | Hardening and polish |

Cut line: launch after M4. M5 and M6 ship as deployed increments afterwards.
