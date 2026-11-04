from __future__ import annotations

from datetime import date, datetime
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


# ---- M4: timeline, insiders, event studies ----


class HeadlineOut(BaseModel):
    headline: str
    source: str
    url: str
    created_at: datetime


class PressReleaseOut(BaseModel):
    title: str
    summary: str
    url: str


class FilingOut(BaseModel):
    accession: str
    form: str
    category: Literal["current", "ownership", "offering", "tender", "merger", "report"]
    kind_label: str
    minor: bool
    filing_date: date
    accepted_at: datetime | None  # UTC, without a zone
    items: list[str]
    labels: list[str]
    earnings: bool
    url: str
    day: date
    press: PressReleaseOut | None


class InsiderFilingOut(BaseModel):
    accession: str
    filing_date: date
    trade_date: date | None
    day: date | None
    signal: Literal["buy", "sale", "planned_sale", "routine", "other"]
    signal_label: str
    owner_name: str
    owner_role: str
    plan: str
    shares: float | None
    value: float | None
    price: float | None
    n_transactions: int
    lag_days: int | None
    url: str


class MacroReleaseOut(BaseModel):
    key: str
    name: str
    about: str
    time_et: str
    period: str
    value: float
    previous: float | None
    unit: str
    decimals: int
    signed: bool  # a change, shown with + or -
    url: str


class WorldStoryOut(BaseModel):
    title: str
    url: str
    domain: str
    copies: int  # outlets that carried it
    seen_at: datetime  # UTC, without a zone
    business: bool


class WorldNewsOut(BaseModel):
    covered: bool
    since: date | None
    n_articles: int
    typical: float | None
    stories: list[WorldStoryOut]


class HealthEventOut(BaseModel):
    kind: Literal["drug_approval", "device_approval", "recall", "trial_results", "trial_stopped"]
    label: str
    date: date  # the date the source gives; the event counts from the next session
    title: str
    detail: str
    url: str


class DayItemsOut(BaseModel):
    headlines: list[HeadlineOut]
    filings: list[FilingOut]
    insiders: list[InsiderFilingOut]
    macro: list[MacroReleaseOut]
    world: WorldNewsOut
    health: list[HealthEventOut]


class DayDetailResponse(DayResponse):
    items: DayItemsOut


class Marker(BaseModel):
    date: date
    headlines: int
    filings: int
    earnings: bool
    buys: int
    sales: int


class MarkersResponse(BaseModel):
    ticker: str
    markers: list[Marker]


class CarTestOut(BaseModel):
    start: int
    end: int
    mean_car: float
    se: float | None
    t_stat: float | None
    p_value: float | None
    bmp_p: float | None
    positive_share: float
    ci_low: float | None
    ci_high: float | None


class PermutationOut(BaseModel):
    observed: float
    null_mean: float
    null_std: float
    p_value: float
    n_permutations: int


class StockStudyOut(BaseModel):
    n_requested: int
    n_used: int
    dropped_incomplete: int
    dropped_overlap: int
    relative_days: list[int]
    mean_car: list[float]
    ci_low: list[float | None]
    ci_high: list[float | None]
    tests: list[CarTestOut]
    permutation: PermutationOut | None


class ReactionOut(BaseModel):
    n_events: int
    mean_abs_z_events: float
    mean_abs_z_all: float
    ratio: float
    p_value: float
    n_permutations: int


class InsidersResponse(BaseModel):
    ticker: str
    filings: list[InsiderFilingOut]
    counts: dict[str, int]
    study: StockStudyOut | None
    earnings: ReactionOut | None
    pooled: dict[str, object] | None


# ---- M5: intraday reactions ----


class WindowOut(BaseModel):
    abnormal: float
    z: float | None
    minutes: int


class ReactionPath(BaseModel):
    minutes: list[int]
    actual: list[float]
    expected: list[float]
    band: list[float]


class HeadlineReactionOut(BaseModel):
    kind: Literal["intraday", "overnight"]
    session: date
    before: WindowOut | None
    after: WindowOut
    p_empirical: float | None
    n_reference: int
    flagged: bool
    moved_before: bool
    path: ReactionPath | None


class HeadlineGroupOut(BaseModel):
    headlines: list[HeadlineOut]
    published: datetime  # New York time
    reaction: HeadlineReactionOut | None
    error: str | None


class ReactionsResponse(BaseModel):
    ticker: str
    date: date
    available: bool
    message: str | None
    alpha: float
    expected_false_flags: float
    groups: list[HeadlineGroupOut]
