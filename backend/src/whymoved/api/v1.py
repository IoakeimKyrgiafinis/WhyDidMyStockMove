from __future__ import annotations

import math
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from whymoved.api.schemas import (
    AttributionResponse,
    DailyResponse,
    DayResponse,
    ErrorResponse,
    Estimate,
    HealthOut,
    Interval,
    MetaResponse,
    ProfileResponse,
    SearchResponse,
    StockRef,
    VarianceOut,
)
from whymoved.api.state import DataState
from whymoved.domain.universe import UniverseMember
from whymoved.services.explain import MoveExplainer

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


def _num(value: object) -> float | None:
    """A finite float, or None for NaN and missing values."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


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


@router.get("/stocks/{ticker}/days/{day}", response_model=DayResponse)
def day(ticker: str, day: date, explainer: Explainer) -> DayResponse:
    member = explainer.member(ticker)
    e, row = explainer.day(member.ticker, day)
    return DayResponse(
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
