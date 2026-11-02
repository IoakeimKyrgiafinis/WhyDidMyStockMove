from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from intraday_market import make_bars, slot
from market import NEWS_DAY, day_at, make_market
from whymoved.adapters.alpaca import AlpacaUnavailable
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer
from whymoved.services.reactions import ReactionService, ReactionsUnavailable


@dataclass
class FakeIntraday:
    """Serves synthetic bars under the requested symbol names and counts the calls."""

    closes: pd.DataFrame
    names: dict[str, str]  # requested symbol -> column of `closes`
    calls: int = 0
    fail: bool = False
    requests: list[tuple[datetime, datetime]] = field(default_factory=list)

    def bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> pd.DataFrame:
        self.calls += 1
        self.requests.append((start, end))
        if self.fail:
            raise AlpacaUnavailable("HTTP 503 after 5 attempts")
        window = self.closes[(self.closes.index >= start) & (self.closes.index <= end)]
        return pd.DataFrame({s: window[self.names[s]] for s in symbols if s in self.names})


@pytest.fixture(scope="module")
def market():  # type: ignore[no-untyped-def]
    return make_market(with_events=True)


@pytest.fixture(scope="module")
def explainer(market) -> MoveExplainer:  # type: ignore[no-untyped-def]
    return MoveExplainer(market.snapshot, n_boot=200)


def provider_for(market, *, fail: bool = False) -> FakeIntraday:  # type: ignore[no-untyped-def]
    """Bars ending on the session after the evening headline, with a +3% reaction planted in
    the 30 minutes after the 10:00 New York headline."""
    news_day = day_at(market, NEWS_DAY)
    last = day_at(market, NEWS_DAY + 2)
    k = slot(10, 0)
    closes, _ = make_bars(last=last, shocks={(news_day, k + j): 0.005 for j in range(6)})
    names = {"AAA": "stock", "BBB": "stock", "SPY": "market", "XLK": "sector", "XLF": "sector"}
    return FakeIntraday(closes, names, fail=fail)


def test_the_planted_reaction_is_found(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    service = ReactionService(provider_for(market))

    groups = service.day_reactions(explainer, "AAA", day_at(market, NEWS_DAY))

    assert len(groups) == 1
    g = groups[0]
    assert g.published == datetime.combine(day_at(market, NEWS_DAY), datetime.min.time()).replace(
        hour=10
    )
    assert g.reaction is not None and g.reaction.kind == "intraday"
    assert g.reaction.after.abnormal == pytest.approx(0.03, abs=0.008)
    assert g.reaction.flagged
    assert list(g.headlines["headline"]) == ["Alpha Corp wins contract"]


def test_an_evening_headline_is_scored_on_the_next_open(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    service = ReactionService(provider_for(market))

    groups = service.day_reactions(explainer, "AAA", day_at(market, NEWS_DAY + 2))

    assert len(groups) == 1
    assert groups[0].reaction is not None
    assert groups[0].reaction.kind == "overnight"
    assert groups[0].reaction.session == day_at(market, NEWS_DAY + 2)


def test_results_are_cached_per_stock_and_day(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    provider = provider_for(market)
    service = ReactionService(provider)
    day = day_at(market, NEWS_DAY)

    service.day_reactions(explainer, "AAA", day)
    service.day_reactions(explainer, "AAA", day)

    assert provider.calls == 1
    start, end = provider.requests[0]
    assert (pd.Timestamp(day) - pd.Timestamp(start)).days == 70
    assert end > datetime.combine(day, datetime.min.time())


def test_a_day_without_headlines_fetches_nothing(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    provider = provider_for(market)

    assert ReactionService(provider).day_reactions(explainer, "ABC", day_at(market, NEWS_DAY)) == []
    assert provider.calls == 0


def test_a_failing_provider_gives_a_message_per_group(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    service = ReactionService(provider_for(market, fail=True))

    groups = service.day_reactions(explainer, "AAA", day_at(market, NEWS_DAY))

    assert groups[0].reaction is None
    assert groups[0].error == "intraday prices could not be fetched right now"


def test_no_provider_means_unavailable(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ReactionsUnavailable):
        ReactionService(None).day_reactions(explainer, "AAA", day_at(market, NEWS_DAY))


def test_endpoint(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(create_app(explainer, reactions=ReactionService(provider_for(market))))
    when = day_at(market, NEWS_DAY).isoformat()

    body = client.get(f"/api/v1/stocks/AAA/days/{when}/reactions").json()

    assert body["available"] is True
    assert body["expected_false_flags"] == pytest.approx(0.05)
    group = body["groups"][0]
    assert group["headlines"][0]["headline"] == "Alpha Corp wins contract"
    reaction = group["reaction"]
    assert reaction["flagged"] is True and reaction["kind"] == "intraday"
    path = reaction["path"]
    assert len(path["minutes"]) == len(path["actual"]) == len(path["expected"]) == len(path["band"])


def test_endpoint_without_keys(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(create_app(explainer, reactions=ReactionService(None)))
    when = day_at(market, NEWS_DAY).isoformat()

    body = client.get(f"/api/v1/stocks/AAA/days/{when}/reactions").json()

    assert body["available"] is False
    assert body["groups"] == []
