from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.news import NewsItem

NEWS_FILE = "news.parquet"
COLUMNS = ["id", "headline", "source", "url", "created_at", "updated_at", "symbols"]


class ParquetNewsStore:
    """Headlines as one parquet file. Each save writes a temporary file and renames it, so a
    failed write does not damage the existing data. Tickers are stored comma-separated."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self) -> list[NewsItem]:
        path = self._directory / NEWS_FILE
        if not path.exists():
            return []
        try:
            frame = pd.read_parquet(path)
        except (ValueError, OSError) as exc:
            raise StoreError(f"the news store is unreadable: {exc}") from exc
        missing = [name for name in COLUMNS if name not in frame.columns]
        if missing:
            raise StoreError(f"the news store is missing columns: {missing}")

        items: list[NewsItem] = []
        for identifier, headline, source, url, created, updated, symbols in zip(
            frame["id"],
            frame["headline"],
            frame["source"],
            frame["url"],
            frame["created_at"],
            frame["updated_at"],
            frame["symbols"],
            strict=True,
        ):
            items.append(
                NewsItem(
                    id=int(identifier),
                    headline=str(headline),
                    source=str(source),
                    url=str(url),
                    created_at=pd.Timestamp(created).to_pydatetime(),
                    updated_at=None if pd.isna(updated) else pd.Timestamp(updated).to_pydatetime(),
                    symbols=tuple(part for part in str(symbols).split(",") if part),
                )
            )
        return items

    def save(self, items: Sequence[NewsItem]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            {
                "id": pd.Series([item.id for item in items], dtype="int64"),
                "headline": pd.Series([item.headline for item in items], dtype="object"),
                "source": pd.Series([item.source for item in items], dtype="object"),
                "url": pd.Series([item.url for item in items], dtype="object"),
                "created_at": pd.to_datetime(
                    pd.Series([item.created_at for item in items], dtype="object")
                ),
                "updated_at": pd.to_datetime(
                    pd.Series([item.updated_at for item in items], dtype="object")
                ),
                "symbols": pd.Series([",".join(item.symbols) for item in items], dtype="object"),
            }
        )
        temp = self._directory / f".{NEWS_FILE}.tmp"
        try:
            frame.to_parquet(temp, engine="pyarrow")
            os.replace(temp, self._directory / NEWS_FILE)
        finally:
            temp.unlink(missing_ok=True)
