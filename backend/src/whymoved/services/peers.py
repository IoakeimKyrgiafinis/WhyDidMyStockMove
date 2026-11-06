"""Which stocks move with this one, beyond the market and their sectors?

Peers are ranked on residual correlation over a selection window and then checked on a
later validation window they had no say in. Everything is computed from the in-memory daily
closes; nothing is precomputed or stored.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from whymoved.domain.cointegration import CointegrationResult, engle_granger
from whymoved.domain.multiple_testing import benjamini_hochberg
from whymoved.domain.peers import (
    Residualizer,
    cluster_order,
    correlation_matrix,
    fit_residualizer,
    rank_peers,
    residualize,
    validate_peers,
)
from whymoved.domain.universe import MARKET_ETF
from whymoved.services.explain import MoveExplainer, NotEnoughData

FloatArray = npt.NDArray[np.float64]

SELECTION_DAYS = 504  # about two years to choose peers
VALIDATION_DAYS = 252  # the last year, to check them
GRID_PEERS = 99
TESTED_PEERS = 10
REPLAY_PEERS = 20
REPLAY_WINDOW = 63  # about three months
REPLAY_STEP = 5
FDR_Q = 0.05

Layer = Literal["residual", "raw", "spillover"]


@dataclass(frozen=True, eq=False)
class UniverseWindow:
    """Daily returns of every stock with complete data over the selection + validation span,
    and residuals from a model fitted on the selection window only."""

    selection: pd.DataFrame  # residuals, selection window
    validation: pd.DataFrame  # residuals, validation window (out of sample)
    raw_validation: pd.DataFrame  # raw returns, validation window
    prices_validation: pd.DataFrame  # closes over the validation window, for cointegration
    model: Residualizer
    start: date
    split: date
    end: date


@dataclass(frozen=True, eq=False)
class PeerResult:
    target: str
    ranking: pd.DataFrame  # top GRID_PEERS by selection-window residual correlation
    validation: pd.DataFrame  # selection vs validation correlation for every grid peer
    spillover: pd.DataFrame  # beta and se of each peer's residual on the target's residual
    order: list[str]  # grid order (target included), clustered on the selection window
    grids: dict[Layer, FloatArray]  # matrices in `order`, validation window
    window: UniverseWindow


def build_window(explainer: MoveExplainer) -> UniverseWindow:
    close = explainer.close
    span = SELECTION_DAYS + VALIDATION_DAYS
    if len(close) <= span:
        raise NotEnoughData(f"peer analysis needs more than {span} days of prices")
    recent = close.iloc[-(span + 1) :]
    tickers = [m.ticker for m in explainer.members]
    sector_of = {m.ticker: m.sector_etf for m in explainer.members}
    etfs = sorted(set(sector_of.values()) & set(recent.columns))
    complete = [t for t in tickers if bool(recent[t].notna().all()) and sector_of[t] in etfs]
    if len(complete) < 2:
        raise NotEnoughData("too few stocks have complete prices for the peer analysis")

    returns = recent.pct_change().iloc[1:]
    stocks = returns[complete]
    market = returns[MARKET_ETF]
    sectors = returns[etfs]
    sel = slice(0, SELECTION_DAYS)
    val = slice(SELECTION_DAYS, span)
    model = fit_residualizer(stocks.iloc[sel], market.iloc[sel], sectors.iloc[sel], sector_of)
    return UniverseWindow(
        selection=residualize(model, stocks.iloc[sel], market.iloc[sel], sectors.iloc[sel]),
        validation=residualize(model, stocks.iloc[val], market.iloc[val], sectors.iloc[val]),
        raw_validation=stocks.iloc[val],
        prices_validation=recent[complete].iloc[SELECTION_DAYS + 1 :],
        model=model,
        start=returns.index[0].date(),
        split=returns.index[SELECTION_DAYS].date(),
        end=returns.index[-1].date(),
    )


class PeerService:
    def __init__(self, explainer: MoveExplainer) -> None:
        self._explainer = explainer
        self._window: UniverseWindow | None = None
        self._window_lock = threading.Lock()

    def window(self) -> UniverseWindow:
        """The universe-wide residuals, built once per snapshot and kept. Not in the shared
        cache: evicting it while peer results still pointed at it would hold two copies."""
        with self._window_lock:
            if self._window is None:
                self._window = build_window(self._explainer)
            return self._window

    def peers(self, ticker: str) -> PeerResult:
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("peers", t), lambda: self._peers(t))

    def _peers(self, target: str) -> PeerResult:
        w = self.window()
        if target not in w.selection.columns:
            raise NotEnoughData(f"{target} lacks complete prices over the last three years")
        ranking = rank_peers(w.selection, target, GRID_PEERS)
        names = list(ranking.index)
        validation = validate_peers(w.selection, w.validation, target, names)

        members = [target, *names]
        order = cluster_order(correlation_matrix(w.selection[members]))
        residual = np.asarray(correlation_matrix(w.validation[order]), dtype=np.float64)
        raw = np.asarray(correlation_matrix(w.raw_validation[order]), dtype=np.float64)
        e = np.asarray(w.validation[order], dtype=np.float64)
        e = e - e.mean(axis=0)
        cov = (e.T @ e) / (len(e) - 1)
        spill = cov / np.diag(cov)[None, :]  # [i, j]: beta of stock i's residual on stock j's

        return PeerResult(
            target=target,
            ranking=ranking,
            validation=validation,
            spillover=spillover_table(w.validation, target, names),
            order=order,
            grids={"residual": residual, "raw": raw, "spillover": spill},
            window=w,
        )

    def cointegration(self, ticker: str) -> Iterator[tuple[str, CointegrationResult | None]]:
        """Engle-Granger on validation-window prices for the top peers, one at a time."""
        result = self.peers(ticker)
        prices = result.window.prices_validation
        for peer in list(result.ranking.index)[:TESTED_PEERS]:
            try:
                yield peer, engle_granger(prices[result.target], prices[peer])
            except ValueError:
                yield peer, None

    def cointegration_table(self, ticker: str) -> pd.DataFrame:
        """All top-peer tests with Benjamini-Hochberg q-values (columns: statistic, p_value,
        q_value, cointegrated, hedge_ratio). Peers whose test could not run are left out."""
        t = self._explainer.member(ticker).ticker
        return self._explainer.cached(("coint", t), lambda: self._coint_table(t))

    def _coint_table(self, ticker: str) -> pd.DataFrame:
        done = [(peer, r) for peer, r in self.cointegration(ticker) if r is not None]
        if not done:
            return pd.DataFrame(
                columns=["statistic", "p_value", "q_value", "cointegrated", "hedge_ratio"]
            )
        p = np.array([r.p_value for _, r in done], dtype=np.float64)
        q, rejected = benjamini_hochberg(p, FDR_Q)
        return pd.DataFrame(
            {
                "statistic": [r.statistic for _, r in done],
                "p_value": p,
                "q_value": q,
                "cointegrated": rejected,
                "hedge_ratio": [r.hedge_ratio for _, r in done],
            },
            index=pd.Index([peer for peer, _ in done], name="ticker"),
        )

    def comove(self, ticker: str, day: date) -> pd.DataFrame:
        """For one day: each top peer's unexplained move, what the spillover beta predicted
        from the target's unexplained move, and the gap scored by the peer's volatility.

        Uses each stock's own out-of-sample daily decomposition, as on the stock pages.
        Columns: actual, expected, z, beta.
        """
        result = self.peers(ticker)
        stamp = pd.Timestamp(day)
        target_daily = self._explainer.daily(result.target)
        if stamp not in target_daily.index:
            raise NotEnoughData(f"{day.isoformat()} is not an explainable day for {ticker}")
        own = float(target_daily.loc[stamp, "unexplained"])  # type: ignore[arg-type]
        rows: list[dict[str, object]] = []
        for peer in list(result.ranking.index)[:TESTED_PEERS]:
            try:
                d = self._explainer.daily(peer)
            except NotEnoughData:
                continue
            if stamp not in d.index:
                continue
            beta = float(result.spillover.loc[peer, "beta"])
            actual = float(d.loc[stamp, "unexplained"])  # type: ignore[arg-type]
            sigma = float(d.loc[stamp, "sigma"])  # type: ignore[arg-type]
            expected = beta * own
            rows.append(
                {
                    "ticker": peer,
                    "actual": actual,
                    "expected": expected,
                    "z": (actual - expected) / sigma if sigma > 0 else math.nan,
                    "beta": beta,
                }
            )
        return pd.DataFrame(rows, columns=["ticker", "actual", "expected", "z", "beta"]).set_index(
            "ticker"
        )

    def replay(self, ticker: str) -> Iterator[dict[str, object]]:
        """Rolling correlations of the target and its top peers over the whole history.

        Uses each stock's out-of-sample unexplained returns for the residual layer and raw
        returns for the raw layer. Frames are computed lazily as they are consumed.
        """
        result = self.peers(ticker)
        names = [result.target, *list(result.ranking.index)[: REPLAY_PEERS - 1]]
        unexplained: dict[str, pd.Series] = {}
        for name in names:
            try:
                unexplained[name] = self._explainer.daily(name)["unexplained"]
            except NotEnoughData:
                continue
        names = [n for n in names if n in unexplained]
        resid = pd.DataFrame(unexplained).dropna()
        raw = self._explainer.close[names].pct_change().reindex(resid.index)
        yield {"type": "names", "names": names}
        n = len(names)
        upper = np.triu_indices(n, k=1)
        for end in range(REPLAY_WINDOW, len(resid) + 1, REPLAY_STEP):
            sl = slice(end - REPLAY_WINDOW, end)
            r = _corr(np.asarray(resid.iloc[sl], dtype=np.float64))
            w = np.asarray(raw.iloc[sl], dtype=np.float64)
            q = _corr(w) if np.isfinite(w).all() else np.full((n, n), np.nan)
            yield {
                "type": "frame",
                "date": resid.index[end - 1].date().isoformat(),
                "residual": _round(r),
                "raw": _round(q),
                "mean_residual": _mean(r[upper]),
                "mean_raw": _mean(q[upper]),
            }


def spillover_table(residuals: pd.DataFrame, target: str, peers: list[str]) -> pd.DataFrame:
    """OLS slope of each peer's residual on the target's residual, with its standard error."""
    x = np.asarray(residuals[target], dtype=np.float64)
    x = x - x.mean()
    sxx = float(np.sum(x**2))
    n = len(x)
    rows: list[tuple[float, float]] = []
    for peer in peers:
        y = np.asarray(residuals[peer], dtype=np.float64)
        y = y - y.mean()
        beta = float(np.sum(x * y) / sxx)
        resid = y - beta * x
        se = float(np.sqrt(np.sum(resid**2) / (n - 2) / sxx))
        rows.append((beta, se))
    return pd.DataFrame(rows, columns=["beta", "se"], index=pd.Index(peers, name="ticker"))


def _corr(values: FloatArray) -> FloatArray:
    out: FloatArray = np.atleast_2d(np.asarray(np.corrcoef(values, rowvar=False), dtype=np.float64))
    return out


def _round(m: FloatArray) -> list[list[float | None]]:
    return [[None if not np.isfinite(v) else round(float(v), 3) for v in row] for row in m]


def _mean(values: FloatArray) -> float | None:
    finite = values[np.isfinite(values)]
    return round(float(finite.mean()), 4) if len(finite) else None
