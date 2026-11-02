from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Self

import httpx
import numpy as np
import pandas as pd
from pydantic import SecretStr

from whymoved.adapters.alpaca import DATA_URL, AlpacaError, Feed, build_client
from whymoved.adapters.alpaca_http import AlpacaHttp
from whymoved.adapters.http_policy import RetryPolicy

logger = logging.getLogger(__name__)

BARS_PATH = "/v2/stocks/bars"
PAGE_LIMIT = 10_000
DEFAULT_MAX_PAGES = 50
DEFAULT_TIMEFRAME = "5Min"


def _format(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


class AlpacaIntradayBars:
    """Intraday bar closes from Alpaca, as a wide frame.

    The result has one row per bar start time (naive UTC), one column per requested symbol, and
    NaN where a symbol had no trade in that bar. On the free IEX feed thin stocks have many gaps.
    Prices are split-adjusted. Malformed bars are skipped and counted in the log.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        feed: Feed = "iex",
        timeframe: str = DEFAULT_TIMEFRAME,
        max_pages: int = DEFAULT_MAX_PAGES,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._http = AlpacaHttp(client, retry=retry, sleep=sleep, clock=clock)
        self._feed = feed
        self._timeframe = timeframe
        self._max_pages = max_pages

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> pd.DataFrame:
        names = list(dict.fromkeys(symbols))
        if not names:
            raise ValueError("symbols must not be empty")
        if start.tzinfo is not None or end.tzinfo is not None:
            raise ValueError("start and end must be timezone-naive UTC datetimes")
        if start > end:
            raise ValueError("start must not be after end")

        params = {
            "symbols": ",".join(names),
            "timeframe": self._timeframe,
            "start": _format(start),
            "end": _format(end),
            "limit": str(PAGE_LIMIT),
            "adjustment": "split",
            "feed": self._feed,
            "sort": "asc",
        }
        closes: dict[str, dict[datetime, float]] = {name: {} for name in names}
        skipped = 0
        seen_tokens: set[str] = set()
        for _ in range(self._max_pages):
            body = self._http.get_json(BARS_PATH, params)
            bars = body.get("bars") or {}
            if not isinstance(bars, dict):
                raise AlpacaError("unexpected bars in the response")
            for symbol, rows in bars.items():
                if symbol not in closes or not isinstance(rows, list):
                    continue
                for row in rows:
                    when = _bar_time(row.get("t")) if isinstance(row, dict) else None
                    price = row.get("c") if isinstance(row, dict) else None
                    if (
                        when is None
                        or isinstance(price, bool)
                        or not isinstance(price, int | float)
                        or not np.isfinite(price)
                        or price <= 0
                    ):
                        skipped += 1
                        continue
                    closes[symbol][when] = float(price)
            token = body.get("next_page_token")
            if token is None or token == "":
                break
            if not isinstance(token, str) or token in seen_tokens:
                raise AlpacaError("pagination did not advance")
            seen_tokens.add(token)
            params["page_token"] = token
        else:
            raise AlpacaError(f"more than {self._max_pages} pages of bars")
        if skipped:
            logger.warning("skipped %d malformed bars", skipped)

        frame = pd.DataFrame(
            {name: pd.Series(values, dtype="float64") for name, values in closes.items()}
        )
        frame.index = pd.DatetimeIndex(frame.index, name="time")
        return frame.sort_index().reindex(columns=names)


def build_intraday(
    key_id: SecretStr,
    secret_key: SecretStr,
    *,
    feed: Feed = "iex",
    base_url: str = DATA_URL,
    transport: httpx.BaseTransport | None = None,
) -> AlpacaIntradayBars:
    client = build_client(key_id, secret_key, base_url=base_url, transport=transport)
    return AlpacaIntradayBars(client, feed=feed)
