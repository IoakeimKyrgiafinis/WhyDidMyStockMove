# Why did my stock move?

A website that explains a US stock's price move: how much came from the overall market, how much
from its sector, and how much is left for the company itself, with statistics that say how far
to trust each number. Headlines, SEC filings and insider trades sit on the same timeline.

It is descriptive, not predictive, and it is not investment advice.

## What it shows

| Panel | What you see | Statistics behind it |
|---|---|---|
| Where the move came from | A period's return split into market, sector and own parts | Two-factor model (market + orthogonalised sector), betas from the previous year only |
| Day by day | Every day's split; click a day to explain it | Out-of-sample decomposition, Newey-West error bands |
| Was it unusual? | How surprising the unexplained part was | EWMA-scaled score, empirical tail probability next to the normal one, expected false alarms |
| Model health | How much of the stock is just the market | Block-bootstrap variance shares, R², beta stability, Ljung-Box, Jarque-Bera, ARCH |
| What else happened | Economic releases (Fed decisions, inflation, jobs, GDP), headlines, world press coverage, company press releases, 8-Ks and other SEC filings (stakes, offerings, tender offers) and insider trades on the day they could first move the price | New York session timing with no look-ahead |
| Headline reactions | The move beyond expected in the 30 minutes after a headline, and before it | 5-minute factor model, time-of-day volatility, empirical percentile |
| Earnings days | How much bigger results days are than ordinary days | Permutation test on absolute scores |
| Insider trades | Purchases, sales and pre-planned sales from SEC Form 4 | Event study from the filing date, permutation test, pooled study clustered by date |
| Stocks that move with it | The 99 closest stocks beyond market and sector | Residual correlation, held-out validation, spillover beta, Engle-Granger with Benjamini-Hochberg |
| Replay | Rolling correlations across the whole history | Raw vs residual correlation over time |

Each choice is written up in [docs/decisions](docs/decisions), and the full scope is in
[docs/SPEC.md](docs/SPEC.md).

## How it fits together

```
GitHub Actions (nightly)                          Render (API)                 Vercel (site)
  prices      Alpaca daily bars    ─┐
  headlines   Alpaca news          ─┤  snapshot    download at startup,
  insiders    SEC Form 4 data sets ─┼─► GitHub  ─► refresh every 6 h,   ◄──── React + TypeScript
  filings     SEC EDGAR 8-K/Form 4 ─┤  release     compute per request         (Vite)
  economy     FRED releases        ─┤
  world press GDELT 15-min files   ─┤
  study       pooled insider study ─┘              (FastAPI)
                                                       │ on demand
                                                       └─► Alpaca 5-minute bars (headline reactions)
```

- `backend/src/whymoved/domain`: the statistics, pure functions with no I/O.
- `backend/src/whymoved/services`: orchestration (decomposition, timeline, reactions, peers).
- `backend/src/whymoved/adapters`: Alpaca, SEC EDGAR, FRED, GDELT, parquet storage, snapshot download.
- `backend/src/whymoved/api`: FastAPI under `/api/v1`, one error format, Server-Sent Events for
  the peer grid and replay, request IDs, JSON logs, cache headers.
- `backend/src/whymoved/jobs`: the nightly collectors and the pooled insider study.
- `frontend/src`: the site. Charts are hand-written SVG; the 100 × 100 peer grid is a canvas.

Statistics are tested against synthetic data with planted effects: known betas, a known shock on
a known day, a known reaction at a known minute, known peers and a known cointegrated pair. The
tests check that each is recovered, and that changing the future never changes the past.

## Data sources

| Source | Used for | Notes |
|---|---|---|
| Alpaca market data (free plan, IEX feed) | Daily and 5-minute prices, headlines | Headlines are shown as title, source and link only |
| SEC EDGAR | Form 4 insider trades, 8-K press releases, 13D/13G, offerings, tender offers, periodic reports | Public data; requests identify you and stay under 10 per second |
| FRED (St. Louis Fed) | Economic calendar: Fed rate decisions, CPI, PPI, PCE, jobs report, jobless claims, retail sales, GDP, job openings | Free key; figures as first published |
| GDELT Project | World press coverage: articles naming each company, how many against a usual day | Raw 15-minute files, no key; titles and links only |
| iShares IWV holdings | The universe (Russell 3000) and sectors | Dated snapshot in `backend/universe/` |
| Select Sector SPDR ETFs | Sector factors | XLK, XLF, XLV, XLY, XLP, XLI, XLE, XLB, XLU, XLRE, XLC |

## Run it locally

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -c requirements.lock -e ".[dev]"
copy .env.example .env          # then fill in your keys (macOS/Linux: cp)
uvicorn whymoved.main:app --reload
```

The API reads the snapshot from `backend/data/`. Either run the nightly jobs once
(`python -m whymoved.jobs.nightly`, then `collect_news`, `collect_insider`, `collect_filings`, `collect_press`, `collect_macro`, `collect_world_news`,
`insider_study`), or set `WHYMOVED_SNAPSHOT_URL` to your release (see below) to download it.

Frontend (Node 22+):

```bash
cd frontend
npm install
npm run dev
```

Quality gate, the same as CI:

```bash
cd backend
ruff format --check . && ruff check . && mypy && pytest
```

```bash
cd frontend
npm run lint && npm run build
```

## Deploy checklist

1. **GitHub repository secrets** (Settings → Secrets and variables → Actions → Repository
   secrets): `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `SEC_USER_AGENT` (your name and email), `FRED_API_KEY` (free, for the economic calendar).
2. **Run the data workflow** once from the Actions tab. It publishes the snapshot to a release
   called `data-latest`, and then runs every weeknight. GitHub pauses scheduled workflows after
   about 60 days without repository activity, so commit or run it manually now and then.
3. **Render (API)**: new Web Service from the repo, runtime Docker, root directory `backend`,
   health check path `/health`. Environment variables:

   | Variable | Value |
   |---|---|
   | `WHYMOVED_SNAPSHOT_URL` | `https://github.com/<you>/<repo>/releases/download/data-latest/` |
   | `WHYMOVED_CORS_ORIGINS` | your Vercel URL, with `https://` and no trailing slash |
   | `WHYMOVED_ALPACA_KEY_ID`, `WHYMOVED_ALPACA_SECRET_KEY` | optional: enables headline reactions |

4. **Vercel (site)**: import the repo, root directory `frontend`, environment variable
   `VITE_API_URL` set to the Render URL (with `https://`, no trailing slash). It is baked in at
   build time, so changing it needs a redeploy.

The free Render service sleeps when idle. The first visit after a quiet spell can take up to a
minute; the site says so and retries on its own.

## Limits, stated on the site too

- Unexplained is not the same as news: fund flows, style effects and noise land there too.
- The two-factor model fits large caps better than small caps; R² is shown for every stock.
- The free price feed is limited, so stocks with incomplete history are left out.
- The universe is today's index, so it carries survivorship bias.
- Form 4 filings are legal disclosures made up to two business days after the trade; most sales
  are routine.
- A headline that coincides with a move shows timing, not cause.
- Correlations and betas drift; peers are historical, not predictive.
