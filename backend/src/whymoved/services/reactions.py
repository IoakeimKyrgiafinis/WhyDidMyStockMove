"""Intraday reactions to a stock's headlines on one session."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from whymoved.adapters.alpaca import AlpacaError
from whymoved.domain.intraday import (
    SESSION_CLOSE,
    SESSION_OPEN,
    IntradayModel,
    Reaction,
    SessionData,
    fit_intraday_model,
    group_headlines,
    prepare_sessions,
    score_reaction,
)
from whymoved.domain.timing import MARKET_TZ
from whymoved.services.explain import MoveExplainer
from whymoved.services.ports import IntradayProvider

logger = logging.getLogger(__name__)

# SPY, not IWV: on the free IEX feed IWV trades too thinly for clean 5-minute returns.
INTRADAY_MARKET = "SPY"
LOOKBACK_CALENDAR_DAYS = 70  # about 45 sessions, enough for 40 estimation days
CACHE_SIZE = 64


class ReactionsUnavailable(Exception):
    """Intraday data is not configured on this server."""


@dataclass(frozen=True, eq=False)
class HeadlineGroup:
    headlines: pd.DataFrame  # rows of the timeline's headline table
    published: datetime  # New York time of the first headline
    reaction: Reaction | None
    error: str | None


class ReactionService:
    def __init__(self, provider: IntradayProvider | None) -> None:
        self._provider = provider
        self._cache: OrderedDict[tuple[str, date], tuple[SessionData, IntradayModel]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._provider is not None

    def day_reactions(
        self, explainer: MoveExplainer, ticker: str, day: date
    ) -> list[HeadlineGroup]:
        """One entry per group of headlines that landed on `day`, newest first."""
        if self._provider is None:
            raise ReactionsUnavailable("intraday data is not configured on this server")
        member = explainer.member(ticker)
        headlines = explainer.timeline.headlines(member.ticker)
        todays = headlines[headlines["day"] == pd.Timestamp(day)]
        if todays.empty:
            return []

        local = [_to_new_york(ts) for ts in todays["created_at"]]
        # Headlines outside market hours all react at the same open, so they form one group.
        inside = [i for i, t in enumerate(local) if SESSION_OPEN <= t.time() < SESSION_CLOSE]
        outside = [i for i in range(len(local)) if i not in set(inside)]
        intraday_groups = group_headlines([local[i] for i in inside])
        groups = [[inside[j] for j in g] for g in intraday_groups]
        if outside:
            groups.append(outside)

        try:
            data, model = self._model(member.ticker, member.sector_etf, day)
        except (AlpacaError, ValueError) as exc:
            logger.warning("intraday model for %s on %s: %s", member.ticker, day, exc)
            return [
                HeadlineGroup(todays.iloc[g], min(local[i] for i in g), None, _reason(exc))
                for g in groups
            ]

        out: list[HeadlineGroup] = []
        for g in groups:
            published = min(local[i] for i in g)
            try:
                reaction: Reaction | None = score_reaction(data, model, published)
                error = None
            except ValueError as exc:
                reaction, error = None, str(exc)
            out.append(HeadlineGroup(todays.iloc[g], published, reaction, error))
        return sorted(out, key=lambda grp: grp.published, reverse=True)

    def _model(self, ticker: str, sector_etf: str, day: date) -> tuple[SessionData, IntradayModel]:
        key = (ticker, day)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        assert self._provider is not None
        start = datetime.combine(day - timedelta(days=LOOKBACK_CALENDAR_DAYS), datetime.min.time())
        end = datetime.combine(day + timedelta(days=1), datetime.min.time()) + timedelta(hours=6)
        raw = self._provider.bars([ticker, INTRADAY_MARKET, sector_etf], start, end)
        closes = raw.rename(
            columns={ticker: "stock", INTRADAY_MARKET: "market", sector_etf: "sector"}
        )
        data = prepare_sessions(closes)
        if day not in data.sessions:
            raise ValueError(
                "no usable intraday data for this session (the stock trades thinly on IEX)"
            )
        model = fit_intraday_model(data, before=day)
        with self._lock:
            self._cache[key] = (data, model)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return data, model


def _to_new_york(ts: object) -> datetime:
    stamp = pd.Timestamp(ts)  # type: ignore[arg-type]
    local = stamp.tz_localize("UTC").tz_convert(MARKET_TZ).tz_localize(None)
    return local.to_pydatetime()


def _reason(exc: Exception) -> str:
    if isinstance(exc, AlpacaError):
        return "intraday prices could not be fetched right now"
    return str(exc)
