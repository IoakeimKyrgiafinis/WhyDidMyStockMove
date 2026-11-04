from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# One row per filing whose exhibits were checked. A filing with no exhibit 99 has an empty
# url; one whose exhibit could not be read has an empty title. Both are kept, so they are not
# fetched again.
PRESS_COLUMNS = ["accession", "ticker", "title", "summary", "url"]


@dataclass(frozen=True)
class PressRelease:
    """The headline and lead paragraph of a release a company attached to a filing."""

    title: str
    summary: str


def press_row(
    accession: str, ticker: str, url: str | None, release: PressRelease | None
) -> dict[str, str]:
    return {
        "accession": accession,
        "ticker": ticker,
        "title": release.title if release else "",
        "summary": release.summary if release else "",
        "url": url or "",
    }


def normalize_press(frame: pd.DataFrame) -> pd.DataFrame:
    """Enforce the schema: column order, text everywhere, missing text as empty strings."""
    missing = [name for name in PRESS_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"press releases are missing columns: {missing}")
    data = frame[PRESS_COLUMNS].reset_index(drop=True)
    return pd.DataFrame({name: data[name].fillna("").astype(str) for name in PRESS_COLUMNS})


def empty_press() -> pd.DataFrame:
    return normalize_press(pd.DataFrame({name: [] for name in PRESS_COLUMNS}))
