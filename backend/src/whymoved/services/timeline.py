from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from whymoved.adapters.press_store import read_ticker
from whymoved.adapters.snapshot import Snapshot
from whymoved.adapters.world_store import coverage_start, read_seen_times
from whymoved.adapters.world_store import read_ticker as read_world
from whymoved.domain.corporate_events import form_kind, is_earnings, item_label
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
from whymoved.domain.press import empty_press
from whymoved.domain.reaction import ReactionSize, reaction_size
from whymoved.domain.timing import session_for_filing, session_for_utc, sessions_for_utc
from whymoved.domain.world_news import empty_world
from whymoved.domain.world_news import stories as world_stories

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


# Below this many trading days of coverage there is no "usual day" to compare with.
MIN_COVERAGE_DAYS = 20


@dataclass(frozen=True, eq=False)
class WorldDay:
    """World press coverage of a stock on one trading day."""

    covered: bool  # the day is inside the stored coverage
    since: pd.Timestamp | None  # first day of coverage
    n_articles: int
    typical: float | None  # median articles on the stock's other trading days
    stories: pd.DataFrame  # see world_news.stories


@dataclass(frozen=True, eq=False)
class DayItems:
    headlines: pd.DataFrame
    filings: pd.DataFrame
    insiders: pd.DataFrame
    macro: pd.DataFrame


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
        self._press_path = snapshot.press_path
        self._macro = snapshot.macro
        self._world_path = snapshot.world_path
        self._world_since = (
            coverage_start(snapshot.world_path) if snapshot.world_path is not None else None
        )
        self.pooled: dict[str, object] | None = snapshot.insider_study

        # Row positions per ticker. A headline can name several tickers.
        self._news_rows = _rows_by_symbol(self._news["symbols"])
        self._event_rows = {
            str(t): np.asarray(rows, dtype=np.int64)
            for t, rows in self._events.groupby("ticker", sort=False, observed=True).indices.items()
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
        """Company filings. Columns: accession, form, category, kind_label, minor,
        filing_date, accepted_at, items, labels, earnings, url, day, and the attached press
        release as press_title, press_summary, press_url (empty strings if none)."""
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
        forms = [str(f) for f in frame["form"]]
        kinds = [form_kind(f) for f in forms]
        frame["form"] = forms
        frame["category"] = [k.category for k in kinds]
        frame["kind_label"] = [k.label for k in kinds]
        frame["minor"] = [k.minor for k in kinds]
        frame["labels"] = [
            [item_label(i) for i in str(items).split(",") if i.strip()] for items in frame["items"]
        ]
        frame["earnings"] = [
            f == "8-K" and is_earnings(str(items))
            for f, items in zip(forms, frame["items"], strict=True)
        ]
        frame["url"] = [
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{str(acc).replace('-', '')}/{doc}"
            for cik, acc, doc in zip(
                frame["cik"], frame["accession"], frame["primary_document"], strict=True
            )
        ]
        frame = frame.dropna(subset=["day"])
        # One row per form and day for offerings and stakes: a shelf takedown can bring several
        # prospectus supplements, and a holder that files through an agent (whose ID, not the
        # company's, starts the accession number) can send a batch of schedules on one day.
        batched = frame["category"].isin(["offering", "ownership"])
        frame = frame[~(frame.duplicated(subset=["form", "day"]) & batched)]
        frame = self._with_press(frame, ticker)
        return frame.sort_values("filing_date", ascending=False)

    def _with_press(self, frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Attach each filing's press release (title, summary, exhibit address), if any."""
        press = (
            read_ticker(self._press_path, ticker) if self._press_path is not None else empty_press()
        )
        by_accession = press.set_index("accession")[["title", "summary", "url"]]
        by_accession.columns = ["press_title", "press_summary", "press_url"]
        joined = frame.join(by_accession, on="accession")
        for name in ("press_title", "press_summary", "press_url"):
            joined[name] = joined[name].fillna("").astype(str)
        return joined

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

    def macro(self, ticker: str) -> pd.DataFrame:
        """Economic releases (MACRO_COLUMNS) on this stock's trading days. All come out before
        the close, so each moves its own date's session (or the next one, on a holiday)."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("macro", t), lambda: self._macro_days(t))

    def _macro_days(self, ticker: str) -> pd.DataFrame:
        frame = self._macro.copy()
        frame["day"] = _snap(frame["release_date"], self._explainer.prices(ticker).index)
        return frame.dropna(subset=["day"])

    def world_counts(self, ticker: str) -> pd.Series:
        """World press articles per trading day since coverage began (zeros included)."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("world", t), lambda: self._world_counts(t))

    def _world_counts(self, ticker: str) -> pd.Series:
        if self._world_path is None or self._world_since is None:
            return pd.Series(dtype="int64")
        trading = self._explainer.prices(ticker).index
        days = _snap(sessions_for_utc(read_seen_times(self._world_path, ticker)), trading)
        counts = days.dropna().value_counts()
        covered = trading[trading >= self._world_since.normalize()]
        return counts.reindex(covered, fill_value=0).astype("int64")

    def world_day(self, ticker: str, day: pd.Timestamp) -> WorldDay:
        t = self._explainer.member(ticker).ticker
        counts = self.world_counts(t)
        if self._world_path is None or day not in counts.index:
            return WorldDay(
                covered=False,
                since=self._world_since,
                n_articles=0,
                typical=None,
                stories=world_stories(empty_world()),
            )
        # A session collects what came out since the previous close, which after a weekend or
        # holiday starts several days earlier.
        start = day - pd.Timedelta(days=7)
        end = day + pd.Timedelta(days=1)
        articles = read_world(self._world_path, t, start.to_pydatetime(), end.to_pydatetime())
        placed = _snap(sessions_for_utc(articles["seen_at"]), self._explainer.prices(t).index)
        others = counts.drop(day)
        return WorldDay(
            covered=True,
            since=self._world_since,
            n_articles=int(counts.loc[day]),
            typical=float(others.median()) if len(others) >= MIN_COVERAGE_DAYS else None,
            stories=world_stories(articles[placed == day]),
        )

    def day_items(self, ticker: str, day: pd.Timestamp) -> DayItems:
        h = self.headlines(ticker)
        f = self.filings(ticker)
        i = self.insiders(ticker)
        m = self.macro(ticker)
        return DayItems(
            headlines=h[h["day"] == day],
            filings=f[f["day"] == day],
            insiders=i[i["day"] == day] if not i.empty else i,
            macro=m[m["day"] == day],
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
    values = pa.array(symbols, type=pa.string())
    if isinstance(values, pa.ChunkedArray):  # a real parquet file loads in several chunks
        values = values.combine_chunks()
    lists = pc.split_pattern(values, ",")
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
