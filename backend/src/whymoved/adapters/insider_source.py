from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Protocol

import pandas as pd

from whymoved.adapters.insider_bulk import read_insider_zip


class DatasetDownloader(Protocol):
    def insider_dataset(self, year: int, quarter: int) -> bytes | None: ...


class EdgarInsiderSource:
    """Insider trades of one quarter: downloads the SEC data set and parses it."""

    def __init__(self, downloader: DatasetDownloader, ticker_by_cik: Mapping[int, str]) -> None:
        self._downloader = downloader
        self._ticker_by_cik = ticker_by_cik

    def trades(self, year: int, quarter: int) -> pd.DataFrame | None:
        raw = self._downloader.insider_dataset(year, quarter)
        if raw is None:
            return None
        return read_insider_zip(io.BytesIO(raw), self._ticker_by_cik)
