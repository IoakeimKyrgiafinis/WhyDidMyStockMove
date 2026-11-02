from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from whymoved.adapters.snapshot import Snapshot
from whymoved.domain.corporate_events import is_earnings, item_label
from whymoved.domain.events import (
    MAX_SNAP_DAYS,
    AveragePath,
    CarResult,
    EventWindows,
    PermutationResult,
    average_path,
    build_event_windows,
    car_test,
    permutation_test,
)
from whymoved.domain.insider_summary import summarize_filings
from whymoved.domain.reaction import ReactionSize, reaction_size
from whymoved.domain.timing import session_for_filing, session_for_utc

if TYPE_CHECKING:
    from whymoved.services.explain import MoveExplainer

PRE = 5
POST = 20
STUDY_TESTS = ((0, 1), (0, 5), (0, 20))
N_PERM = 2000
# A single stock has few purchases, so its permutation test uses the one-week CAR, where
# power is reasonable; the 20-day horizon is left to the pooled study.
PERMUTATION_WINDOW = (0, 5)


@dataclass(frozen=True, eq=False)
class StockInsiderStudy:
    """Abnormal returns after this stock's insider purchases, from the day they became public."""

    windows: EventWindows
    path: AveragePath | None
    tests: tuple[CarResult, ...]
    permutation: PermutationResult | None


@dataclass(frozen=True, eq=False)
class DayItems:
    headlines: pd.DataFrame
    filings: pd.DataFrame
    insiders: pd.DataFrame


def _snap(sessions: pd.Series[pd.Timestamp], trading_days: pd.Index) -> pd.Series:
    """Map each session date to the first trading day on or after it (NaT if none within
    MAX_SNAP_DAYS calendar days)."""
    if len(sessions) == 0 or len(trading_days) == 0:
        return pd.Series(pd.NaT, index=sessions.index, dtype="datetime64[ns]")
    days = pd.DatetimeIndex(trading_days).to_numpy().astype("datetime64[D]")
    targets = pd.to_datetime(sessions).to_numpy().astype("datetime64[D]")
    pos = np.searchsorted(days, targets, side="left")
    out = np.full(len(targets), np.datetime64("NaT", "D"), dtype="datetime64[D]")
    ok = pos < len(days)
    hit = np.zeros(len(targets), dtype=bool)
    hit[ok] = (days[pos[ok]] - targets[ok]).astype(np.int64) <= MAX_SNAP_DAYS
    out[hit] = days[pos[hit]]
    return pd.Series(pd.to_datetime(out), index=sessions.index)


class Timeline:
    """Headlines, 8-K filings and insider trades for a stock, placed on its trading days."""

    def __init__(self, snapshot: Snapshot, explainer: MoveExplainer) -> None:
        self._explainer = explainer
        self._news = snapshot.news
        self._events = snapshot.events
        self._trades = snapshot.trades
        self.pooled: dict[str, object] | None = snapshot.insider_study

        # Row positions per ticker. A headline can name several tickers.
        self._news_rows = _rows_by_symbol(self._news["symbols"])
        self._event_rows = {
            str(t): np.asarray(rows, dtype=np.int64)
            for t, rows in self._events.groupby("ticker", sort=False).indices.items()
        }
        self._trade_rows = {
            str(t): np.asarray(rows, dtype=np.int64)
            for t, rows in self._trades.groupby("ticker", sort=False, observed=True).indices.items()
        }

    @property
    def insiders_available(self) -> int:
        """Number of insider transaction rows loaded."""
        return len(self._trades)

    # ---- per stock tables, with the trading day each item lands on ----

    def headlines(self, ticker: str) -> pd.DataFrame:
        """Columns: headline, source, url, created_at, day."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("headlines", t), lambda: self._headlines(t))

    def _headlines(self, ticker: str) -> pd.DataFrame:
        rows = self._news_rows.get(ticker, np.array([], dtype=np.int64))
        frame = self._news.iloc[rows][["headline", "source", "url", "created_at"]].copy()
        sessions = frame["created_at"].map(lambda ts: pd.Timestamp(session_for_utc(ts)))
        frame["day"] = _snap(sessions, self._explainer.prices(ticker).index)
        return frame.dropna(subset=["day"]).sort_values("created_at", ascending=False)

    def filings(self, ticker: str) -> pd.DataFrame:
        """8-K filings. Columns: accession, filing_date, accepted_at, items, labels,
        earnings, url, day."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("filings", t), lambda: self._filings(t))

    def _filings(self, ticker: str) -> pd.DataFrame:
        rows = self._event_rows.get(ticker, np.array([], dtype=np.int64))
        frame = self._events.iloc[rows].copy()
        sessions = pd.Series(
            [
                pd.Timestamp(session_for_filing(pd.Timestamp(f).date(), _naive(a)))
                for f, a in zip(frame["filing_date"], frame["accepted_at"], strict=True)
            ],
            index=frame.index,
            dtype="datetime64[ns]",
        )
        frame["day"] = _snap(sessions, self._explainer.prices(ticker).index)
        frame["labels"] = [
            [item_label(i) for i in str(items).split(",") if i.strip()] for items in frame["items"]
        ]
        frame["earnings"] = [is_earnings(str(items)) for items in frame["items"]]
        frame["url"] = [
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{str(acc).replace('-', '')}/{doc}"
            for cik, acc, doc in zip(
                frame["cik"], frame["accession"], frame["primary_document"], strict=True
            )
        ]
        return frame.dropna(subset=["day"]).sort_values("filing_date", ascending=False)

    def insiders(self, ticker: str) -> pd.DataFrame:
        """One row per Form 4 filing (see FILING_COLUMNS), plus the day it became public."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("insiders", t), lambda: self._insiders(t))

    def _insiders(self, ticker: str) -> pd.DataFrame:
        rows = self._trade_rows.get(ticker, np.array([], dtype=np.int64))
        trades = self._trades.iloc[rows]
        filings = summarize_filings(trades)
        if filings.empty:
            filings["day"] = pd.Series(dtype="datetime64[ns]")
            return filings
        # The bulk data sets carry no acceptance time, so a Form 4 counts from the next day.
        sessions = filings["filing_date"].map(
            lambda d: pd.Timestamp(session_for_filing(pd.Timestamp(d).date(), None))
        )
        filings["day"] = _snap(sessions, self._explainer.prices(ticker).index)
        return filings

    def day_items(self, ticker: str, day: pd.Timestamp) -> DayItems:
        h = self.headlines(ticker)
        f = self.filings(ticker)
        i = self.insiders(ticker)
        return DayItems(
            headlines=h[h["day"] == day],
            filings=f[f["day"] == day],
            insiders=i[i["day"] == day] if not i.empty else i,
        )

    # ---- statistics ----

    def insider_study(self, ticker: str) -> StockInsiderStudy:
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("insider_study", t), lambda: self._insider_study(t))

    def _insider_study(self, ticker: str) -> StockInsiderStudy:
        daily = self._explainer.daily(ticker)
        insiders = self.insiders(ticker)
        buys = (
            [pd.Timestamp(d) for d in insiders.loc[insiders["signal"] == "buy", "day"].dropna()]
            if not insiders.empty
            else []
        )
        windows = build_event_windows(daily["unexplained"], daily["sigma"], buys, PRE, POST)
        if windows.n_used < 2:
            return StockInsiderStudy(windows=windows, path=None, tests=(), permutation=None)
        return StockInsiderStudy(
            windows=windows,
            path=average_path(windows),
            tests=tuple(car_test(windows, s, e) for s, e in STUDY_TESTS),
            permutation=permutation_test(
                daily["unexplained"], daily["sigma"], windows, *PERMUTATION_WINDOW, n_perm=N_PERM
            ),
        )

    def earnings_reaction(self, ticker: str) -> ReactionSize | None:
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("earnings", t), lambda: self._earnings(t))

    def _earnings(self, ticker: str) -> ReactionSize | None:
        filings = self.filings(ticker)
        days = [pd.Timestamp(d) for d in filings.loc[filings["earnings"], "day"]]
        return reaction_size(self._explainer.daily(ticker)["z"], days)


def _rows_by_symbol(symbols: pd.Series) -> dict[str, np.ndarray]:
    """Row positions per ticker for comma-separated ticker lists, computed in Arrow so no
    Python string is created per headline."""
    if len(symbols) == 0:
        return {}
    lists = pc.split_pattern(pa.array(symbols, type=pa.string()), ",")
    tickers = pc.list_flatten(lists).dictionary_encode()
    parents = np.asarray(pc.list_parent_indices(lists), dtype=np.int64)
    codes = np.asarray(tickers.indices, dtype=np.int64)
    names = tickers.dictionary.to_pylist()
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(names) + 1))
    return {
        str(name): parents[order[bounds[i] : bounds[i + 1]]] for i, name in enumerate(names) if name
    }


def _naive(value: object) -> pd.Timestamp | None:
    """An acceptance time as a naive timestamp, or None when it is missing."""
    if not isinstance(value, datetime):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.tz_convert(None) if ts.tzinfo is not None else ts
