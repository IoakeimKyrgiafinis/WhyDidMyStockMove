from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """The one error format every endpoint uses."""

    error: ErrorDetail


class MetaResponse(BaseModel):
    status: Literal["ready", "loading", "unavailable"]
    message: str | None
    as_of: date | None
    n_stocks: int
    window: int
    alpha: float


class StockRef(BaseModel):
    ticker: str
    name: str
    sector: str
    sector_etf: str


class SearchResponse(BaseModel):
    results: list[StockRef]


class Estimate(BaseModel):
    estimate: float
    se: float


class Interval(BaseModel):
    estimate: float
    low: float
    high: float


class HealthOut(BaseModel):
    status: Literal["good", "caution", "poor"]
    reasons: list[str]
    r_squared: float
    beta_z_market: float
    beta_z_sector: float
    ljung_box_p: float
    jarque_bera_p: float
    skewness: float
    excess_kurtosis: float
    arch_lm_p: float


class VarianceOut(BaseModel):
    market: Interval
    sector: Interval
    own: Interval
    level: float
    n_boot: int
    block: int


class ProfileResponse(BaseModel):
    stock: StockRef
    last_close: float
    last_date: date
    first_date: date
    n_returns: int
    window: int
    beta_market: Estimate
    beta_sector: Estimate
    r_squared: float
    health: HealthOut
    variance: VarianceOut


class DailyResponse(BaseModel):
    """One array per column, aligned with `dates`. Scores are null until enough history."""

    ticker: str
    window: int
    alpha: float
    dates: list[date]
    total: list[float]
    market: list[float]
    sector: list[float]
    drift: list[float]
    unexplained: list[float]
    z: list[float | None]
    p_value: list[float | None]
    flagged: list[bool]


class DayResponse(BaseModel):
    ticker: str
    date: date
    total: float
    market: float
    sector: float
    drift: float
    unexplained: float
    se_market: float
    se_sector: float
    se_explained: float
    beta_market: float
    beta_sector: float
    sigma: float | None
    z: float | None
    p_value: float | None
    p_normal: float | None
    return_period: float | None
    flagged: bool
    alpha: float
    window: int


class AttributionResponse(BaseModel):
    ticker: str
    start: date
    end: date
    n_days: int
    compounded: float
    sum_of_days: float
    market: float
    sector: float
    own: float
    drift: float
    unexplained: float
    compounding: float
    n_flagged: int
    expected_flags: float
    alpha: float
