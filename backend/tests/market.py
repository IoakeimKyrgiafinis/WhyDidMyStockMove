"""A synthetic market with known structure, written to disk like the nightly job's snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from whymoved.adapters.filings_store import ParquetEventStore
from whymoved.adapters.insider_store import ParquetTradeStore
from whymoved.adapters.news_store import NEWS_FILE
from whymoved.adapters.parquet_store import ParquetPriceStore
from whymoved.adapters.snapshot import Snapshot
from whymoved.domain.bars import DailyBars
from whymoved.domain.corporate_events import EVENT_COLUMNS, normalize_events
from whymoved.domain.insider import TRADE_COLUMNS, normalize_trades
from whymoved.domain.universe import MARKET_ETF, SECTOR_ETF_TICKERS, UniverseMember

BETA_MARKET = 1.2
BETA_SECTOR = 0.6
SHOCK_DAY = 500
SHOCK = -0.08

# AAA: open-market insider purchases, each followed by five days of +0.5% abnormal return,
# starting the first session after the filing. Spaced so windows (-5, +20) never overlap
# and none contains the shock.
BUY_DAYS = (290, 320, 350, 380, 410, 440, 470, 530, 560, 590, 620, 650)
BUY_DRIFT = 0.005
# BBB: earnings 8-Ks filed after the close, followed by a 5% move (alternating sign).
EARNINGS_DAYS = (310, 373, 436, 499, 562, 625)
EARNINGS_MOVE = 0.05
# BBB: an 8-K filed before the close lands on its own day.
INTRADAY_8K_DAY = 400
# AAA headlines: one during the session, one after the close (lands on the next day).
NEWS_DAY = 350

MEMBERS = [
    UniverseMember("AAA", "ALPHA CORP", "Information Technology", "XLK", 5.0),
    UniverseMember("BBB", "BETA BANK", "Financials", "XLF", 3.0),
    UniverseMember("ABC", "ABC HOLDINGS", "Industrials", "XLI", 2.0),
    UniverseMember("NEW", "NEW LISTING", "Energy", "XLE", 1.0),
]
CIK = {"AAA": 1001, "BBB": 1002, "ABC": 1003, "NEW": 1004}


@dataclass(frozen=True, eq=False)
class Market:
    close: pd.DataFrame
    snapshot: Snapshot


def make_market(n_days: int = 700, seed: int = 11, *, with_events: bool = False) -> Market:
    """Every stock loads BETA_MARKET on the market and BETA_SECTOR on its pure sector factor.

    AAA gets a SHOCK on day SHOCK_DAY. NEW only listed 100 days ago, too short to explain.
    With `with_events`, insider purchases, 8-Ks and headlines are added with planted effects.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2023-01-02", periods=n_days)
    market = rng.normal(0.0004, 0.01, n_days)
    returns: dict[str, np.ndarray] = {MARKET_ETF: market}
    pure: dict[str, np.ndarray] = {}
    for etf in SECTOR_ETF_TICKERS:
        pure[etf] = rng.normal(0.0, 0.006, n_days)
        returns[etf] = 0.9 * market + pure[etf]
    for member in MEMBERS:
        noise = rng.normal(0.0, 0.008, n_days)
        returns[member.ticker] = (
            BETA_MARKET * market + BETA_SECTOR * pure[member.sector_etf] + noise
        )
    returns["AAA"][SHOCK_DAY] += SHOCK
    if with_events:
        for p in BUY_DAYS:
            returns["AAA"][p : p + 5] += BUY_DRIFT
        for k, p in enumerate(EARNINGS_DAYS):
            returns["BBB"][p] += EARNINGS_MOVE if k % 2 == 0 else -EARNINGS_MOVE

    close = pd.DataFrame({s: 100.0 * np.cumprod(1.0 + r) for s, r in returns.items()}, index=index)
    close.loc[close.index[: n_days - 100], "NEW"] = np.nan

    extra: dict[str, object] = {}
    if with_events:
        extra = {
            "news": _news(index),
            "events": _events(index),
            "trades": _trades(index),
        }
    snapshot = Snapshot(
        close=close,
        members=list(MEMBERS),
        as_of=index[-1].date(),
        history_start=index[0].date(),
        **extra,  # type: ignore[arg-type]
    )
    return Market(close=close, snapshot=snapshot)


def _day(index: pd.DatetimeIndex, position: int) -> date:
    return pd.Timestamp(index[position]).date()


def _news(index: pd.DatetimeIndex) -> pd.DataFrame:
    during = datetime.combine(_day(index, NEWS_DAY), time(14, 0))  # 10:00 New York
    after = datetime.combine(_day(index, NEWS_DAY + 1), time(21, 30))  # 17:30 New York
    return pd.DataFrame(
        {
            "headline": ["Alpha Corp wins contract", "Alpha and Beta announce partnership"],
            "source": ["benzinga", "benzinga"],
            "url": ["https://example.test/1", "https://example.test/2"],
            "created_at": pd.to_datetime([during, after]),
            "symbols": ["AAA", "AAA,BBB"],
        }
    )


def _events(index: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for k, p in enumerate(EARNINGS_DAYS):
        filed = _day(index, p - 1)
        rows.append(
            {
                "ticker": "BBB",
                "cik": CIK["BBB"],
                "accession": f"0001002-24-{k:06d}",
                "form": "8-K",
                "filing_date": filed,
                "accepted_at": datetime.combine(filed, time(16, 5)),
                "items": "2.02,9.01",
                "primary_document": "earnings.htm",
            }
        )
    filed = _day(index, INTRADAY_8K_DAY)
    rows.append(
        {
            "ticker": "BBB",
            "cik": CIK["BBB"],
            "accession": "0001002-24-999999",
            "form": "8-K",
            "filing_date": filed,
            "accepted_at": datetime.combine(filed, time(10, 0)),
            "items": "5.02",
            "primary_document": "officer.htm",
        }
    )
    return normalize_events(pd.DataFrame(rows, columns=EVENT_COLUMNS))


def _trade(
    accession: str, ticker: str, filed: date, code: str, kind: str, plan: str = "no"
) -> dict[str, object]:
    return {
        "accession": accession,
        "issuer_cik": CIK[ticker],
        "ticker": ticker,
        "form": "4",
        "filing_date": filed,
        "trade_date": filed - timedelta(days=1),
        "code": code,
        "kind": kind,
        "shares": 1000.0,
        "price": 50.0,
        "acquired": "A" if code in "PA" else "D",
        "direct": "D",
        "shares_after": 10_000.0,
        "owner_name": "Doe Jane",
        "owner_role": "CEO",
        "plan": plan,
        "source": "bulk",
    }


def _trades(index: pd.DatetimeIndex) -> pd.DataFrame:
    rows = [
        _trade(f"0000000001-24-{k:06d}", "AAA", _day(index, p - 1), "P", "open_market_buy")
        for k, p in enumerate(BUY_DAYS)
    ]
    rows.append(
        _trade("0000000002-24-000001", "BBB", _day(index, 450), "S", "open_market_sell", "yes")
    )
    rows.append(_trade("0000000002-24-000002", "BBB", _day(index, 455), "A", "routine"))
    return normalize_trades(pd.DataFrame(rows, columns=TRADE_COLUMNS))


def write_snapshot(directory: Path, market: Market) -> None:
    """Write the files the nightly job publishes."""
    volume = pd.DataFrame(1_000_000.0, index=market.close.index, columns=market.close.columns)
    ParquetPriceStore(directory).save(DailyBars(close=market.close, volume=volume))
    summary = {
        "as_of": market.snapshot.as_of.isoformat(),
        "history_start": market.snapshot.history_start.isoformat(),
        "members": [
            {
                "ticker": m.ticker,
                "name": m.name,
                "sector": m.sector,
                "sector_etf": m.sector_etf,
                "weight": m.weight,
            }
            for m in MEMBERS
        ],
    }
    (directory / "universe.json").write_text(json.dumps(summary), encoding="utf-8")
    snap = market.snapshot
    if len(snap.news):
        news = snap.news.assign(
            id=range(len(snap.news)),
            updated_at=pd.Series(pd.NaT, index=snap.news.index, dtype="datetime64[ns]"),
        )
        news.to_parquet(directory / NEWS_FILE, engine="pyarrow")
    if len(snap.events):
        ParquetEventStore(directory).save(snap.events)
    if len(snap.trades):
        ParquetTradeStore(directory).save(snap.trades)


def shock_date(market: Market) -> date:
    return pd.Timestamp(market.close.index[SHOCK_DAY]).date()


def day_at(market: Market, position: int) -> date:
    return pd.Timestamp(market.close.index[position]).date()
