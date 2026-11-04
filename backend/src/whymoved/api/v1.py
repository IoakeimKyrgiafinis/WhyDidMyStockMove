from __future__ import annotations

from datetime import date
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Query, Request

from whymoved.api import convert
from whymoved.api.ratelimit import TokenBucket, client_key
from whymoved.api.schemas import (
    AttributionResponse,
    DailyResponse,
    DayDetailResponse,
    DayItemsOut,
    ErrorResponse,
    Estimate,
    HealthOut,
    InsidersResponse,
    Interval,
    Marker,
    MarkersResponse,
    MetaResponse,
    ProfileResponse,
    ReactionsResponse,
    SearchResponse,
    StockRef,
    VarianceOut,
)
from whymoved.api.state import DataState
from whymoved.domain.intraday import DEFAULT_ALPHA
from whymoved.domain.universe import UniverseMember
from whymoved.services.explain import MoveExplainer
from whymoved.services.reactions import ReactionService

ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Unknown ticker"},
    422: {"model": ErrorResponse, "description": "Invalid request or not enough data"},
    503: {"model": ErrorResponse, "description": "Data not loaded"},
}

router = APIRouter(prefix="/api/v1", tags=["v1"], responses=ERRORS)


def get_state(request: Request) -> DataState:
    state: DataState = request.app.state.data
    return state


def get_explainer(state: Annotated[DataState, Depends(get_state)]) -> MoveExplainer:
    return state.explainer


Explainer = Annotated[MoveExplainer, Depends(get_explainer)]


_num = convert.num


def _ref(member: UniverseMember) -> StockRef:
    return StockRef(
        ticker=member.ticker,
        name=member.name,
        sector=member.sector,
        sector_etf=member.sector_etf,
    )


@router.get("/meta", response_model=MetaResponse)
def meta(state: Annotated[DataState, Depends(get_state)]) -> MetaResponse:
    status, message = state.status()
    if status != "ready":
        return MetaResponse(
            status=status, message=message, as_of=None, n_stocks=0, window=0, alpha=0.0
        )
    explainer = state.explainer
    return MetaResponse(
        status="ready",
        message=None,
        as_of=explainer.as_of,
        n_stocks=len(explainer.members),
        window=explainer.window,
        alpha=explainer.alpha,
    )


@router.get("/stocks", response_model=SearchResponse)
def search(
    explainer: Explainer,
    q: Annotated[str, Query(max_length=40)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    return SearchResponse(results=[_ref(m) for m in explainer.search(q, limit)])


@router.get("/stocks/{ticker}", response_model=ProfileResponse)
def profile(ticker: str, explainer: Explainer) -> ProfileResponse:
    p = explainer.profile(ticker)
    v = p.variance
    h = p.health

    def interval(i: int) -> Interval:
        return Interval(
            estimate=float(v.shares[i]), low=float(v.ci_low[i]), high=float(v.ci_high[i])
        )

    return ProfileResponse(
        stock=_ref(p.member),
        last_close=p.last_close,
        last_date=p.last_date,
        first_date=p.first_date,
        n_returns=p.n_returns,
        window=explainer.window,
        beta_market=Estimate(estimate=p.fit.beta_market, se=p.fit.se_beta_market),
        beta_sector=Estimate(estimate=p.fit.beta_sector, se=p.fit.se_beta_sector),
        r_squared=p.fit.r_squared,
        health=HealthOut(
            status=h.status,
            reasons=list(h.reasons),
            r_squared=h.r_squared,
            beta_z_market=h.beta_z_market,
            beta_z_sector=h.beta_z_sector,
            ljung_box_p=h.ljung_box.p_value,
            jarque_bera_p=h.jarque_bera.p_value,
            skewness=h.jarque_bera.skewness,
            excess_kurtosis=h.jarque_bera.excess_kurtosis,
            arch_lm_p=h.arch_lm.p_value,
        ),
        variance=VarianceOut(
            market=interval(0),
            sector=interval(1),
            own=interval(2),
            level=v.level,
            n_boot=v.n_boot,
            block=v.block,
        ),
    )


@router.get("/stocks/{ticker}/daily", response_model=DailyResponse)
def daily(ticker: str, explainer: Explainer) -> DailyResponse:
    member = explainer.member(ticker)
    d = explainer.daily(member.ticker)
    return DailyResponse(
        ticker=member.ticker,
        window=explainer.window,
        alpha=explainer.alpha,
        dates=[ts.date() for ts in d.index],
        total=d["total"].tolist(),
        market=d["market"].tolist(),
        sector=d["sector"].tolist(),
        drift=d["alpha"].tolist(),
        unexplained=d["unexplained"].tolist(),
        z=[_num(x) for x in d["z"]],
        p_value=[_num(x) for x in d["p_value"]],
        flagged=[bool(x) for x in d["flagged"]],
    )


@router.get("/stocks/{ticker}/days/{day}", response_model=DayDetailResponse)
def day(ticker: str, day: date, explainer: Explainer) -> DayDetailResponse:
    """One day explained, with the headlines, 8-Ks and insider filings that landed on it."""
    member = explainer.member(ticker)
    e, row = explainer.day(member.ticker, day)
    items = explainer.timeline.day_items(member.ticker, pd.Timestamp(day))
    return DayDetailResponse(
        ticker=member.ticker,
        date=day,
        total=e.total,
        market=e.market,
        sector=e.sector,
        drift=e.alpha,
        unexplained=e.unexplained,
        se_market=e.se_market,
        se_sector=e.se_sector,
        se_explained=e.se_explained,
        beta_market=e.fit.beta_market,
        beta_sector=e.fit.beta_sector,
        sigma=_num(row["sigma"]),
        z=_num(row["z"]),
        p_value=_num(row["p_value"]),
        p_normal=_num(row["p_normal"]),
        return_period=_num(row["return_period"]),
        flagged=bool(row["flagged"]),
        alpha=explainer.alpha,
        window=explainer.window,
        items=DayItemsOut(
            headlines=convert.headlines(items.headlines),
            filings=convert.filings(items.filings),
            insiders=convert.insider_filings(items.insiders),
        ),
    )


@router.get("/stocks/{ticker}/markers", response_model=MarkersResponse)
def markers(ticker: str, explainer: Explainer) -> MarkersResponse:
    """What landed on each trading day: headline counts, 8-K filings and insider filings."""
    member = explainer.member(ticker)
    timeline = explainer.timeline
    h = timeline.headlines(member.ticker)
    f = timeline.filings(member.ticker)
    f = f[~f["minor"]]  # routine filings (passive 13Gs, quarterly reports) get no marker
    i = timeline.insiders(member.ticker)

    frames = [
        pd.DataFrame({"day": h["day"], "headlines": 1}),
        pd.DataFrame({"day": f["day"], "filings": 1, "earnings": f["earnings"].astype(int)}),
    ]
    if not i.empty:
        frames.append(
            pd.DataFrame(
                {
                    "day": i["day"],
                    "buys": (i["signal"] == "buy").astype(int),
                    "sales": i["signal"].isin(["sale", "planned_sale"]).astype(int),
                }
            )
        )
    columns = ["headlines", "filings", "earnings", "buys", "sales"]
    combined = pd.concat(frames, ignore_index=True).dropna(subset=["day"])
    combined = combined.reindex(columns=["day", *columns]).fillna({c: 0 for c in columns})
    totals = combined.groupby("day")[columns].sum().astype(int)
    # Days with only routine insider filings (awards, tax withholding) get no marker.
    totals = totals[totals[["headlines", "filings", "buys", "sales"]].sum(axis=1) > 0]
    rows: dict[pd.Timestamp, dict[str, int]] = totals.to_dict("index")  # type: ignore[assignment]

    return MarkersResponse(
        ticker=member.ticker,
        markers=[
            Marker(
                date=pd.Timestamp(d).date(),
                headlines=r["headlines"],
                filings=r["filings"],
                earnings=r["earnings"] > 0,
                buys=r["buys"],
                sales=r["sales"],
            )
            for d, r in sorted(rows.items())
        ],
    )


@router.get("/stocks/{ticker}/insiders", response_model=InsidersResponse)
def insiders(ticker: str, explainer: Explainer) -> InsidersResponse:
    """Form 4 filings sorted by signal, this stock's insider-purchase event study, the earnings
    reaction size, and the pooled study across all stocks."""
    member = explainer.member(ticker)
    timeline = explainer.timeline
    filings = timeline.insiders(member.ticker)
    counts = (
        {str(k): int(v) for k, v in filings["signal"].value_counts().items()}
        if not filings.empty
        else {}
    )
    study = timeline.insider_study(member.ticker)
    reaction = timeline.earnings_reaction(member.ticker)
    return InsidersResponse(
        ticker=member.ticker,
        filings=convert.insider_filings(filings),
        counts=counts,
        study=convert.stock_study(study) if study.windows.n_requested else None,
        earnings=convert.reaction(reaction) if reaction else None,
        pooled=timeline.pooled,
    )


@router.get("/stocks/{ticker}/attribution", response_model=AttributionResponse)
def attribution(
    ticker: str,
    explainer: Explainer,
    start: date | None = None,
    end: date | None = None,
) -> AttributionResponse:
    member = explainer.member(ticker)
    a = explainer.attribution(member.ticker, start, end)
    return AttributionResponse(
        ticker=member.ticker,
        start=a.start,
        end=a.end,
        n_days=a.n_days,
        compounded=a.compounded,
        sum_of_days=a.sum_of_days,
        market=a.market,
        sector=a.sector,
        own=a.drift + a.unexplained,
        drift=a.drift,
        unexplained=a.unexplained,
        compounding=a.compounded - a.sum_of_days,
        n_flagged=a.n_flagged,
        expected_flags=a.expected_flags,
        alpha=explainer.alpha,
    )


@router.get("/stocks/{ticker}/days/{day}/reactions", response_model=ReactionsResponse)
def reactions(ticker: str, day: date, request: Request, explainer: Explainer) -> ReactionsResponse:
    """How the stock moved beyond market and sector right after each headline of the day.

    Fetches 5-minute bars on demand, so it is slower than the other endpoints; results are
    cached per stock and day.
    """
    member = explainer.member(ticker)
    service: ReactionService = request.app.state.reactions
    if service.available:
        limiter: TokenBucket = request.app.state.reaction_limiter
        limiter.take(client_key(request))
    if not service.available:
        return ReactionsResponse(
            ticker=member.ticker,
            date=day,
            available=False,
            message="Intraday reactions are not configured on this server.",
            alpha=DEFAULT_ALPHA,
            expected_false_flags=0.0,
            groups=[],
        )
    groups = service.day_reactions(explainer, member.ticker, day)
    scored = sum(1 for g in groups if g.reaction is not None and g.reaction.p_empirical is not None)
    return ReactionsResponse(
        ticker=member.ticker,
        date=day,
        available=True,
        message=None,
        alpha=DEFAULT_ALPHA,
        expected_false_flags=DEFAULT_ALPHA * scored,
        groups=convert.headline_groups(groups),
    )
