"""The economic releases that move the whole market, and how each is shown.

Every value is the figure as first published (FRED's "initial release only" view): what traders
saw that morning, not the number after later revisions. Release times are the agencies' fixed
schedule in New York time; all of them come before the 16:00 close, so a release moves the
session of its own date.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

MACRO_COLUMNS = ["release_date", "key", "period", "value", "previous"]

Units = Literal["lin", "chg", "pch"]
Frequency = Literal["weekly", "monthly", "quarterly", "meeting"]


@dataclass(frozen=True)
class MacroSeries:
    key: str
    series_id: str
    name: str
    about: str
    units: Units  # FRED transformation: level, change, or percent change
    unit: str  # shown after the number
    decimals: int
    time_et: str
    frequency: Frequency
    scale: float = 1.0  # FRED value times scale is the shown number


FOMC = "fomc"
FOMC_RELEASE_ID = 101  # FRED release "FOMC Press Release"
FED_TARGET_UPPER = "DFEDTARU"
FED_RANGE_WIDTH = 0.25  # the target range has been a quarter point wide since 2008

MACRO_SERIES: tuple[MacroSeries, ...] = (
    MacroSeries(
        FOMC,
        FED_TARGET_UPPER,
        "Fed interest-rate decision",
        "The Federal Reserve's target range for its policy rate, announced at 2:00 PM.",
        "lin",
        "%",
        2,
        "14:00",
        "meeting",
    ),
    MacroSeries(
        "cpi",
        "CPIAUCSL",
        "Consumer prices (CPI)",
        "Change in consumer prices from the month before.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "core_cpi",
        "CPILFESL",
        "Core consumer prices",
        "CPI without food and energy, the part the Fed watches most.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "payrolls",
        "PAYEMS",
        "Jobs report: payrolls",
        "Jobs added or lost in the month, outside farming.",
        "chg",
        "k jobs",
        0,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "unemployment",
        "UNRATE",
        "Jobs report: unemployment rate",
        "Share of the labour force out of work and looking.",
        "lin",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "claims",
        "ICSA",
        "Weekly jobless claims",
        "New claims for unemployment benefits in the week.",
        "lin",
        "k",
        0,
        "08:30",
        "weekly",
        scale=0.001,
    ),
    MacroSeries(
        "ppi",
        "PPIFIS",
        "Producer prices (PPI)",
        "Change in the prices businesses receive, from the month before.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "retail",
        "RSAFS",
        "Retail sales",
        "Change in retail and food-service sales from the month before.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "gdp",
        "A191RL1Q225SBEA",
        "GDP growth",
        "Growth of the economy in the quarter, at an annual rate.",
        "lin",
        "%",
        1,
        "08:30",
        "quarterly",
    ),
    MacroSeries(
        "pce",
        "PCEPI",
        "PCE prices",
        "Change in prices of what households buy, the Fed's preferred inflation gauge.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "core_pce",
        "PCEPILFE",
        "Core PCE prices",
        "PCE prices without food and energy.",
        "pch",
        "%",
        1,
        "08:30",
        "monthly",
    ),
    MacroSeries(
        "jolts",
        "JTSJOL",
        "Job openings (JOLTS)",
        "Open positions employers are trying to fill.",
        "lin",
        "M",
        2,
        "10:00",
        "monthly",
        scale=0.001,
    ),
)
SERIES_BY_KEY = {s.key: s for s in MACRO_SERIES}


def period_label(series: MacroSeries, period: pd.Timestamp) -> str:
    """What the figure covers: "Sep 2026", "Q3 2026", "week to Oct 24", or the meeting."""
    if series.frequency == "quarterly":
        return f"Q{(period.month - 1) // 3 + 1} {period.year}"
    if series.frequency == "weekly":
        return f"week to {period.strftime('%b')} {period.day}"
    if series.frequency == "meeting":
        return "meeting"
    return period.strftime("%b %Y")


def fred_url(series: MacroSeries) -> str:
    return f"https://fred.stlouisfed.org/series/{series.series_id}"


def initial_rows(
    series: MacroSeries, observations: list[tuple[pd.Timestamp, pd.Timestamp, float]]
) -> list[dict[str, object]]:
    """One row per release from (first published, period, value) triples, each with the first
    print of the period before it. Later revisions are not used."""
    rows: list[dict[str, object]] = []
    previous: float | None = None
    for released, period, value in sorted(observations, key=lambda o: o[1]):
        rows.append(
            {
                "release_date": released,
                "key": series.key,
                "period": period,
                "value": value * series.scale,
                "previous": previous,
            }
        )
        previous = value * series.scale
    return rows


def fomc_rows(
    meetings: list[pd.Timestamp], target_upper: pd.Series, *, until: pd.Timestamp
) -> list[dict[str, object]]:
    """One row per rate decision: the upper end of the target range after and before it.

    A decision announced on day D takes effect on D + 1, so the rate before is the one on D and
    the rate after the one on D + 1. Meetings later than `until` (scheduled ones) are left out.
    """
    upper = target_upper.dropna().sort_index()
    rows: list[dict[str, object]] = []
    for day in sorted(meetings):
        if day > until or upper.empty:
            continue
        before = upper.loc[:day]
        after = upper.loc[: day + pd.Timedelta(days=1)]
        if before.empty or after.empty or after.index[-1] <= day:
            continue  # the rate after the meeting is not published yet
        rows.append(
            {
                "release_date": day,
                "key": FOMC,
                "period": day,
                "value": float(after.iloc[-1]),
                "previous": float(before.iloc[-1]),
            }
        )
    return rows


def normalize_macro(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in MACRO_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"macro releases are missing columns: {missing}")
    data = frame[MACRO_COLUMNS].reset_index(drop=True)
    out = pd.DataFrame(
        {
            "release_date": pd.to_datetime(data["release_date"]).dt.as_unit("ns"),
            "key": data["key"].astype(str),
            "period": pd.to_datetime(data["period"]).dt.as_unit("ns"),
            "value": pd.to_numeric(data["value"]).astype("float64"),
            "previous": pd.to_numeric(data["previous"]).astype("float64"),
        }
    )
    known = out["key"].isin(list(SERIES_BY_KEY))
    return (
        out[known]
        .drop_duplicates(subset=["key", "period"], keep="last")
        .sort_values(["release_date", "key"])
        .reset_index(drop=True)
    )


def empty_macro() -> pd.DataFrame:
    return normalize_macro(pd.DataFrame({c: [] for c in MACRO_COLUMNS}))
