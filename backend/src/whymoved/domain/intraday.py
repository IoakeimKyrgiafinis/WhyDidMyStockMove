"""How did a stock react in the minutes after a headline, beyond what market and sector did?

The model is the daily one at 5-minute frequency: stock = alpha + b_m * market + b_s * pure
sector + e, fitted only on sessions before the headline's day. Two intraday facts matter:

- Volatility is not constant through the day: the open and the close are much noisier than
  midday. Residual volatility is therefore estimated per 30-minute bucket of the session, and a
  reaction is scored against the volatility of the bars it covers.
- News often follows price ("stock falls 3% on ..."). The abnormal move before the headline is
  reported next to the move after it, so a headline is not credited with a move that came first.

Outside market hours there is no intraday reaction to measure. Those headlines are scored on
the overnight move from the previous close to the first five-minute bar of the next session.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from whymoved.domain.factors import orthogonalize_sector
from whymoved.domain.timing import MARKET_TZ

FloatArray = npt.NDArray[np.float64]

BAR_MINUTES = 5
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
BUCKET_MINUTES = 30
WINDOW_BARS = 6  # 30 minutes before and after the headline
PATH_PRE_BARS = 6
PATH_POST_BARS = 12
MAX_ESTIMATION_DAYS = 40
MIN_ESTIMATION_DAYS = 10
MIN_DAY_COVERAGE = 0.8  # share of a session's bars the stock must have traded in
GROUP_MINUTES = 15
DEFAULT_ALPHA = 0.05
Z95 = 1.96

Kind = Literal["intraday", "overnight"]


@dataclass(frozen=True, eq=False)
class SessionData:
    """Within-session 5-minute returns and overnight returns, in New York time.

    `intraday` has columns stock, market, sector, a naive New York bar-start index, and a
    `slot` column: the bar's position in the session (0 = 9:30). The first bar of each session
    is dropped there, because its return would span the overnight gap. `overnight` has one row
    per session: the return from the previous session's last close to this session's first
    bar close.
    """

    intraday: pd.DataFrame
    overnight: pd.DataFrame
    sessions: list[date]


def prepare_sessions(closes: pd.DataFrame, min_coverage: float = MIN_DAY_COVERAGE) -> SessionData:
    """Turn raw bar closes (columns stock, market, sector; naive UTC index) into returns.

    Only regular-session bars are kept. Sessions where the stock traded in fewer than
    `min_coverage` of the bars are dropped. Remaining gaps (a bar with no trade) are filled
    with the previous close, which makes that bar's return zero and the next one catch up.
    """
    missing = {"stock", "market", "sector"} - set(closes.columns)
    if missing:
        raise ValueError(f"closes are missing columns: {sorted(missing)}")
    if closes.empty:
        return SessionData(_empty_intraday(), _empty_overnight(), [])

    utc = pd.DatetimeIndex(closes.index).tz_localize("UTC")
    local = pd.DatetimeIndex(utc.tz_convert(MARKET_TZ).tz_localize(None))
    frame = closes[["stock", "market", "sector"]].copy()
    frame.index = local
    clock = np.array([t.time() for t in local])
    frame = frame[(clock >= SESSION_OPEN) & (clock < SESSION_CLOSE)].sort_index()
    days = np.array([t.date() for t in pd.DatetimeIndex(frame.index)])

    parts: list[pd.DataFrame] = []
    overnight_rows: list[dict[str, object]] = []
    sessions: list[date] = []
    previous_close: pd.Series | None = None
    for day in sorted(set(days)):
        bars = frame[days == day]
        start = pd.Timestamp(datetime.combine(day, SESSION_OPEN))
        offsets = (pd.DatetimeIndex(bars.index) - start).total_seconds() // (BAR_MINUTES * 60)
        coverage = float(bars["stock"].notna().sum()) / (int(offsets[-1]) + 1)
        filled = bars.ffill()
        if coverage < min_coverage or not bool(filled.notna().all(axis=1).iloc[0]):
            previous_close = None
            continue
        sessions.append(day)
        returns = filled.pct_change().iloc[1:]
        returns["slot"] = np.asarray(offsets[1:], dtype=np.int64)
        returns["day"] = day
        parts.append(returns)
        if previous_close is not None:
            gap = filled.iloc[0] / previous_close - 1.0
            overnight_rows.append(
                {
                    "session": start.normalize(),
                    "stock": float(gap["stock"]),
                    "market": float(gap["market"]),
                    "sector": float(gap["sector"]),
                }
            )
        previous_close = filled.iloc[-1]

    intraday = pd.concat(parts) if parts else _empty_intraday()
    overnight = (
        pd.DataFrame(overnight_rows).set_index("session") if overnight_rows else _empty_overnight()
    )
    return SessionData(intraday=intraday, overnight=overnight, sessions=sessions)


def _empty_intraday() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stock": [],
            "market": [],
            "sector": [],
            "slot": pd.Series([], dtype=int),
            "day": pd.Series([], dtype=object),
        },
        index=pd.DatetimeIndex([]),
    )


def _empty_overnight() -> pd.DataFrame:
    return pd.DataFrame({"stock": [], "market": [], "sector": []}, index=pd.DatetimeIndex([]))


@dataclass(frozen=True, eq=False)
class IntradayModel:
    alpha: float
    beta_market: float
    beta_sector: float
    gamma0: float
    gamma1: float
    bucket_sigma: dict[int, float]  # residual std per 30-minute bucket (0 = 9:30-10:00)
    overnight_sigma: float | None
    n_days: int
    r_squared: float

    def expected(self, market: FloatArray, sector: FloatArray) -> FloatArray:
        pure = sector - self.gamma0 - self.gamma1 * market
        out: FloatArray = self.alpha + self.beta_market * market + self.beta_sector * pure
        return out

    def sigma(self, slots: Sequence[int] | npt.NDArray[np.int64]) -> FloatArray:
        fallback = float(np.median(list(self.bucket_sigma.values())))
        return np.array(
            [self.bucket_sigma.get(bucket_of(int(s)), fallback) for s in slots], dtype=np.float64
        )


def bucket_of(slot: int) -> int:
    return (slot * BAR_MINUTES) // BUCKET_MINUTES


def fit_intraday_model(data: SessionData, before: date) -> IntradayModel:
    """Fit on the last MAX_ESTIMATION_DAYS sessions strictly before `before`."""
    days = [d for d in data.sessions if d < before][-MAX_ESTIMATION_DAYS:]
    if len(days) < MIN_ESTIMATION_DAYS:
        raise ValueError(
            f"need {MIN_ESTIMATION_DAYS} earlier sessions with intraday data, have {len(days)}"
        )
    est = data.intraday[data.intraday["day"].isin(days)]
    y = np.asarray(est["stock"], dtype=np.float64)
    m = np.asarray(est["market"], dtype=np.float64)
    s = np.asarray(est["sector"], dtype=np.float64)
    gamma0, gamma1, pure = orthogonalize_sector(s, m)
    x = np.column_stack([np.ones(len(y)), m, pure])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    total = float(np.sum((y - y.mean()) ** 2))

    buckets = np.array([bucket_of(int(v)) for v in est["slot"]])
    bucket_sigma = {
        int(b): float(np.sqrt(np.mean(resid[buckets == b] ** 2)))
        for b in np.unique(buckets)
        if np.sum(buckets == b) >= 20
    }

    model = IntradayModel(
        alpha=float(coef[0]),
        beta_market=float(coef[1]),
        beta_sector=float(coef[2]),
        gamma0=gamma0,
        gamma1=gamma1,
        bucket_sigma=bucket_sigma,
        overnight_sigma=None,
        n_days=len(days),
        r_squared=1.0 - float(np.sum(resid**2)) / total if total > 0 else 0.0,
    )
    on = data.overnight[data.overnight.index.isin(pd.to_datetime(days))]
    if len(on) >= MIN_ESTIMATION_DAYS:
        on_resid = np.asarray(on["stock"], dtype=np.float64) - _overnight_expected(model, on)
        model = replace(model, overnight_sigma=float(np.std(on_resid, ddof=1)))
    return model


def _overnight_expected(model: IntradayModel, frame: pd.DataFrame) -> FloatArray:
    """Expected overnight move: the betas without the per-bar alpha, which is not an
    overnight quantity."""
    m = np.asarray(frame["market"], dtype=np.float64)
    s = np.asarray(frame["sector"], dtype=np.float64)
    pure = s - model.gamma0 - model.gamma1 * m
    out: FloatArray = model.beta_market * m + model.beta_sector * pure
    return out


@dataclass(frozen=True)
class Window:
    """Abnormal (unexplained) return over a window and its score."""

    abnormal: float
    z: float
    minutes: int


@dataclass(frozen=True, eq=False)
class Reaction:
    kind: Kind
    published: datetime  # New York time of the first headline in the group
    session: date
    before: Window | None
    after: Window
    p_empirical: float | None  # share of comparable windows on earlier days at least as large
    n_reference: int
    flagged: bool
    moved_before: bool  # the stock had already moved unusually before the headline
    # Event path, minutes relative to the bar containing the headline
    path_minutes: list[int]
    path_actual: list[float]
    path_expected: list[float]
    path_band: list[float]  # half-width of the 95% band around the expected path


def group_headlines(times: Sequence[datetime], minutes: int = GROUP_MINUTES) -> list[list[int]]:
    """Indices of headlines grouped when each lands within `minutes` of the group's first.

    Headlines that close together cannot be told apart by price, so they share one reaction.
    """
    order = sorted(range(len(times)), key=lambda i: times[i])
    groups: list[list[int]] = []
    start: datetime | None = None
    for i in order:
        if start is None or times[i] - start > timedelta(minutes=minutes):
            groups.append([i])
            start = times[i]
        else:
            groups[-1].append(i)
    return groups


def _window(resid: FloatArray, sigma: FloatArray) -> Window:
    var = float(np.sum(sigma**2))
    car = float(np.sum(resid))
    return Window(
        abnormal=car,
        z=car / np.sqrt(var) if var > 0 else float("nan"),
        minutes=len(resid) * BAR_MINUTES,
    )


def score_reaction(
    data: SessionData, model: IntradayModel, published: datetime, alpha: float = DEFAULT_ALPHA
) -> Reaction:
    """Score a headline published at `published` (naive New York time)."""
    if SESSION_OPEN <= published.time() < SESSION_CLOSE:
        return _intraday_reaction(data, model, published, alpha)
    return _overnight_reaction(data, model, published, alpha)


def _day_frame(data: SessionData, day: date) -> pd.DataFrame:
    return data.intraday[data.intraday["day"] == day]


def _residuals(model: IntradayModel, frame: pd.DataFrame) -> tuple[FloatArray, FloatArray]:
    y = np.asarray(frame["stock"], dtype=np.float64)
    expected = model.expected(
        np.asarray(frame["market"], dtype=np.float64), np.asarray(frame["sector"], dtype=np.float64)
    )
    return y - expected, expected


def _intraday_reaction(
    data: SessionData, model: IntradayModel, published: datetime, alpha: float
) -> Reaction:
    day = published.date()
    frame = _day_frame(data, day)
    if frame.empty:
        raise ValueError(f"no intraday data for {day}")
    start = datetime.combine(day, SESSION_OPEN)
    event_slot = int((published - start).total_seconds() // (BAR_MINUTES * 60))
    slots = np.asarray(frame["slot"], dtype=np.int64)
    resid, expected = _residuals(model, frame)
    sigma = model.sigma(slots)
    actual = np.asarray(frame["stock"], dtype=np.float64)

    after_mask = (slots >= event_slot) & (slots < event_slot + WINDOW_BARS)
    before_mask = (slots >= event_slot - WINDOW_BARS) & (slots < event_slot)
    if not after_mask.any():
        raise ValueError("the headline came too late in the session to measure a reaction")
    after = _window(resid[after_mask], sigma[after_mask])
    before = _window(resid[before_mask], sigma[before_mask]) if before_mask.any() else None

    # The same time-of-day window on each earlier session, as an empirical reference.
    after_slots = slots[after_mask]
    references: list[float] = []
    for d in [s for s in data.sessions if s < day][-MAX_ESTIMATION_DAYS:]:
        f = _day_frame(data, d)
        r, _ = _residuals(model, f)
        mask = np.isin(np.asarray(f["slot"]), after_slots)
        if mask.sum() == len(after_slots):
            references.append(float(np.sum(r[mask])))
    p_emp = _empirical_p(after.abnormal, references)

    path_mask = (slots >= event_slot - PATH_PRE_BARS) & (slots < event_slot + PATH_POST_BARS)
    path_slots = slots[path_mask]
    cum_actual = np.cumsum(actual[path_mask])
    cum_expected = np.cumsum(expected[path_mask])
    band = Z95 * np.sqrt(np.cumsum(sigma[path_mask] ** 2))
    return Reaction(
        kind="intraday",
        published=published,
        session=day,
        before=before,
        after=after,
        p_empirical=p_emp,
        n_reference=len(references),
        flagged=p_emp is not None and p_emp <= alpha,
        moved_before=before is not None and abs(before.z) >= Z95,
        path_minutes=[int((s - event_slot) * BAR_MINUTES) for s in path_slots],
        path_actual=[float(v) for v in cum_actual],
        path_expected=[float(v) for v in cum_expected],
        path_band=[float(v) for v in band],
    )


def _overnight_reaction(
    data: SessionData, model: IntradayModel, published: datetime, alpha: float
) -> Reaction:
    # Before the open the reaction is at this morning's open; after the close, the next one's.
    session = next(
        (
            d
            for d in data.sessions
            if (
                d > published.date() if published.time() >= SESSION_CLOSE else d >= published.date()
            )
        ),
        None,
    )
    if session is None or pd.Timestamp(session) not in data.overnight.index:
        raise ValueError("no next-session data to measure the overnight reaction")
    if model.overnight_sigma is None or model.overnight_sigma <= 0:
        raise ValueError("not enough earlier sessions to score overnight moves")
    row = data.overnight.loc[[pd.Timestamp(session)]]
    resid = float(np.asarray(row["stock"])[0] - _overnight_expected(model, row)[0])
    after = Window(abnormal=resid, z=resid / model.overnight_sigma, minutes=0)

    earlier = data.overnight[data.overnight.index < pd.Timestamp(session)]
    earlier = earlier.iloc[-MAX_ESTIMATION_DAYS:]
    stock = np.asarray(earlier["stock"], dtype=np.float64)
    references = list(stock - _overnight_expected(model, earlier))
    p_emp = _empirical_p(resid, references)

    before: Window | None = None
    if published.time() >= SESSION_CLOSE:
        frame = _day_frame(data, published.date())
        if not frame.empty:
            r, _ = _residuals(model, frame)
            tail = slice(-WINDOW_BARS, None)
            before = _window(r[tail], model.sigma(np.asarray(frame["slot"])[tail]))

    return Reaction(
        kind="overnight",
        published=published,
        session=session,
        before=before,
        after=after,
        p_empirical=p_emp,
        n_reference=len(references),
        flagged=p_emp is not None and p_emp <= alpha,
        moved_before=before is not None and abs(before.z) >= Z95,
        path_minutes=[],
        path_actual=[],
        path_expected=[],
        path_band=[],
    )


def _empirical_p(observed: float, references: Sequence[float]) -> float | None:
    if len(references) < MIN_ESTIMATION_DAYS:
        return None
    ref = np.abs(np.asarray(references, dtype=np.float64))
    return (1.0 + float(np.sum(ref >= abs(observed)))) / (len(ref) + 1.0)
