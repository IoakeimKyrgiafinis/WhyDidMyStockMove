from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from whymoved.adapters.snapshot import Snapshot
from whymoved.domain.diagnostics import ModelHealth, assess_model_health
from whymoved.domain.factors import (
    DEFAULT_WINDOW,
    DayExplanation,
    FactorFit,
    explain_day,
    fit_factor_model,
    rolling_decomposition,
)
from whymoved.domain.scoring import DEFAULT_ALPHA, expected_false_alarms, score_unexplained
from whymoved.domain.universe import MARKET_ETF, UniverseMember
from whymoved.domain.variance import VarianceSplit, variance_split

DEFAULT_CACHE_SIZE = 512  # about eight entries per stock
SEARCH_LIMIT = 20


class UnknownTicker(KeyError):
    """The ticker is not in the clean universe."""


class NotEnoughData(ValueError):
    """The stock does not have enough history for the requested calculation."""


@dataclass(frozen=True, eq=False)
class Attribution:
    """A period's return split into market, sector and the stock's own part.

    The parts are sums of daily contributions, so they add up to `sum_of_days` exactly.
    `compounded` is the real price change over the period; the gap between the two is the
    compounding effect, which belongs to no single factor.
    """

    start: date
    end: date
    n_days: int
    compounded: float
    sum_of_days: float
    market: float
    sector: float
    drift: float
    unexplained: float
    n_flagged: int
    expected_flags: float


@dataclass(frozen=True, eq=False)
class Profile:
    """How the factor model describes a stock over the latest estimation window."""

    member: UniverseMember
    last_close: float
    last_date: date
    first_date: date
    n_returns: int
    fit: FactorFit
    health: ModelHealth
    variance: VarianceSplit


class _LruCache:
    def __init__(self, size: int) -> None:
        self._size = size
        self._data: OrderedDict[Hashable, object] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute[T](self, key: Hashable, compute: Callable[[], T]) -> T:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]  # type: ignore[return-value]
        value = compute()
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)
        return value


class MoveExplainer:
    """Answers 'why did this stock move?' from a loaded snapshot.

    Every figure is computed on request from the in-memory closes and cached per ticker.
    Daily explanations use only the `window` trading days before each day.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        *,
        window: int = DEFAULT_WINDOW,
        alpha: float = DEFAULT_ALPHA,
        n_boot: int = 1000,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if MARKET_ETF not in snapshot.close.columns:
            raise ValueError(f"the snapshot has no prices for the market factor {MARKET_ETF}")
        self._close = snapshot.close
        self._members = [m for m in snapshot.members if m.ticker in snapshot.close.columns]
        self._by_ticker = {m.ticker: m for m in self._members}
        self.as_of = snapshot.as_of
        self.window = window
        self.alpha = alpha
        self._n_boot = n_boot
        self._cache = _LruCache(cache_size)
        # Imported here: the timeline needs the explainer for prices and the daily table.
        from whymoved.services.timeline import Timeline

        self.timeline = Timeline(snapshot, self)

    def cached[T](self, key: Hashable, compute: Callable[[], T]) -> T:
        """Compute once per key; least recently used entries are dropped first."""
        return self._cache.get_or_compute(key, compute)

    @property
    def members(self) -> list[UniverseMember]:
        return list(self._members)

    def member(self, ticker: str) -> UniverseMember:
        found = self._by_ticker.get(ticker.strip().upper())
        if found is None:
            raise UnknownTicker(ticker)
        return found

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[UniverseMember]:
        """Exact ticker first, then ticker prefixes, then name matches; liquid stocks first."""
        q = query.strip().upper()
        if not q:
            return self._members[:limit]
        exact = [m for m in self._members if m.ticker == q]
        prefix = [m for m in self._members if m.ticker.startswith(q) and m.ticker != q]
        name = [m for m in self._members if q in m.name.upper() and not m.ticker.startswith(q)]
        return [*exact, *prefix, *name][:limit]

    # ---- data ----

    def prices(self, ticker: str) -> pd.DataFrame:
        """Closes of the stock, the market and its sector ETF on the days all three traded."""
        member = self.member(ticker)
        return self._cache.get_or_compute(("prices", member.ticker), lambda: self._prices(member))

    def _prices(self, member: UniverseMember) -> pd.DataFrame:
        if member.sector_etf not in self._close.columns:
            raise NotEnoughData(f"no prices for the sector factor {member.sector_etf}")
        frame = pd.DataFrame(
            {
                "stock": self._close[member.ticker],
                "market": self._close[MARKET_ETF],
                "sector": self._close[member.sector_etf],
            }
        ).dropna()
        return frame[(frame > 0.0).all(axis=1)]

    def returns(self, ticker: str) -> pd.DataFrame:
        prices = self.prices(ticker)
        return prices.pct_change().iloc[1:]

    def daily(self, ticker: str) -> pd.DataFrame:
        """Out-of-sample decomposition and unusual-move score for every explainable day.

        Columns: total, market, sector, alpha, unexplained, sigma, z, p_value, p_normal,
        return_period, flagged.
        """
        member = self.member(ticker)
        return self._cache.get_or_compute(("daily", member.ticker), lambda: self._daily(member))

    def _daily(self, member: UniverseMember) -> pd.DataFrame:
        r = self.returns(member.ticker)
        if len(r) <= self.window:
            raise NotEnoughData(
                f"{member.ticker} has {len(r)} days of returns; "
                f"explaining a day needs more than {self.window}"
            )
        parts = rolling_decomposition(r["stock"], r["market"], r["sector"], self.window)
        scored = score_unexplained(parts["unexplained"], alpha=self.alpha)
        return parts.join(scored.drop(columns=["unexplained"]))

    def day(self, ticker: str, when: date) -> tuple[DayExplanation, pd.Series[float]]:
        """One day with standard errors, and its row of the daily table."""
        daily = self.daily(ticker)
        stamp = pd.Timestamp(when)
        if stamp not in daily.index:
            raise NotEnoughData(f"{when.isoformat()} is not an explainable trading day")
        r = self.returns(ticker)
        explanation = explain_day(r["stock"], r["market"], r["sector"], stamp, self.window)
        return explanation, daily.iloc[int(daily.index.get_indexer(pd.Index([stamp]))[0])]

    def attribution(self, ticker: str, start: date | None, end: date | None) -> Attribution:
        daily = self.daily(ticker)
        lo = pd.Timestamp(start) if start else daily.index[0]
        hi = pd.Timestamp(end) if end else daily.index[-1]
        if lo > hi:
            raise ValueError("start must not be after end")
        part = daily.loc[(daily.index >= lo) & (daily.index <= hi)]
        if part.empty:
            raise NotEnoughData("no explainable trading days in that range")
        total = part["total"].to_numpy(dtype=np.float64)
        return Attribution(
            start=part.index[0].date(),
            end=part.index[-1].date(),
            n_days=len(part),
            compounded=float(np.prod(1.0 + total) - 1.0),
            sum_of_days=float(total.sum()),
            market=float(part["market"].sum()),
            sector=float(part["sector"].sum()),
            drift=float(part["alpha"].sum()),
            unexplained=float(part["unexplained"].sum()),
            n_flagged=int(part["flagged"].sum()),
            expected_flags=expected_false_alarms(part, self.alpha),
        )

    def profile(self, ticker: str) -> Profile:
        """Fit, model health and variance split over the latest `window` days."""
        member = self.member(ticker)
        return self._cache.get_or_compute(("profile", member.ticker), lambda: self._profile(member))

    def _profile(self, member: UniverseMember) -> Profile:
        prices = self.prices(member.ticker)
        r = self.returns(member.ticker)
        if len(r) < self.window:
            raise NotEnoughData(
                f"{member.ticker} has {len(r)} days of returns; the model needs {self.window}"
            )
        recent = r.iloc[-self.window :]
        y = np.asarray(recent["stock"], dtype=np.float64)
        m = np.asarray(recent["market"], dtype=np.float64)
        s = np.asarray(recent["sector"], dtype=np.float64)
        return Profile(
            member=member,
            last_close=float(prices["stock"].iloc[-1]),
            last_date=prices.index[-1].date(),
            first_date=prices.index[0].date(),
            n_returns=len(r),
            fit=fit_factor_model(y, m, s),
            health=assess_model_health(y, m, s),
            variance=variance_split(y, m, s, n_boot=self._n_boot),
        )
