from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Self

import httpx

from whymoved.adapters.alpaca import AlpacaError
from whymoved.adapters.alpaca_http import AlpacaHttp
from whymoved.adapters.http_policy import RetryPolicy
from whymoved.domain.news import NewsItem
from whymoved.domain.universe import normalize_symbol

logger = logging.getLogger(__name__)

NEWS_PATH = "/v1beta1/news"
MAX_PAGE_LIMIT = 50
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_PAGES = 500

_SYMBOL = re.compile(r"[A-Z0-9.]{1,10}")


def _chunks(items: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _format(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _symbols(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    seen: dict[str, None] = {}
    for entry in value:
        if isinstance(entry, str):
            symbol = normalize_symbol(entry)
            if symbol is not None:
                seen.setdefault(symbol)
    return tuple(seen)


def _parse_article(raw: object) -> NewsItem | None:
    """One article as a NewsItem, or None if it lacks an id, headline or creation time."""
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        return None
    headline = raw.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return None
    created = _timestamp(raw.get("created_at"))
    if created is None:
        return None
    url = raw.get("url")
    source = raw.get("source")
    return NewsItem(
        id=identifier,
        headline=headline.strip(),
        source=source.strip() if isinstance(source, str) else "",
        url=url.strip() if isinstance(url, str) else "",
        created_at=created,
        updated_at=_timestamp(raw.get("updated_at")),
        symbols=_symbols(raw.get("symbols")),
    )


class AlpacaNews:
    """Headlines from Alpaca's news endpoint (Benzinga), tagged to tickers.

    Symbols are fetched in batches, each paginated until Alpaca stops returning a page token.
    An article tagged to symbols in several batches is returned once, in its latest version.
    Malformed articles are skipped and counted in the log.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._http = AlpacaHttp(client, retry=retry, sleep=sleep, clock=clock)
        self._batch_size = batch_size
        self._max_pages = max_pages

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def news(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[NewsItem]:
        names = list(symbols)
        self._validate(names, start, end)

        found: dict[int, NewsItem] = {}
        skipped = 0
        batches = list(_chunks(names, self._batch_size))
        for number, batch in enumerate(batches, start=1):
            logger.info(
                "fetching news: batch %d of %d (%d symbols)", number, len(batches), len(batch)
            )
            skipped += self._fetch_batch(batch, start, end, found)
        if skipped:
            logger.warning("skipped %d malformed articles", skipped)
        return sorted(found.values(), key=lambda item: (item.created_at, item.id))

    @staticmethod
    def _validate(names: list[str], start: datetime, end: datetime) -> None:
        if not names:
            raise ValueError("symbols must not be empty")
        if len(set(names)) != len(names):
            raise ValueError("symbols must be unique")
        bad = [s for s in names if _SYMBOL.fullmatch(s) is None]
        if bad:
            raise ValueError(f"invalid symbols: {bad[:5]}")
        if start.tzinfo is not None or end.tzinfo is not None:
            raise ValueError("start and end must be timezone-naive UTC datetimes")
        if start > end:
            raise ValueError("start must not be after end")

    def _fetch_batch(
        self, batch: list[str], start: datetime, end: datetime, found: dict[int, NewsItem]
    ) -> int:
        params = {
            "symbols": ",".join(batch),
            "start": _format(start),
            "end": _format(end),
            "limit": str(MAX_PAGE_LIMIT),
            "sort": "asc",
            "include_content": "false",
        }
        seen_tokens: set[str] = set()
        skipped = 0

        for _ in range(self._max_pages):
            body = self._http.get_json(NEWS_PATH, params)

            articles = body.get("news")
            if articles is not None:
                if not isinstance(articles, list):
                    raise AlpacaError("unexpected news list in the response")
                for raw in articles:
                    item = _parse_article(raw)
                    if item is None:
                        skipped += 1
                        continue
                    current = found.get(item.id)
                    if current is None or item.last_touched >= current.last_touched:
                        found[item.id] = item

            token = body.get("next_page_token")
            if token is None or token == "":
                return skipped
            if not isinstance(token, str):
                raise AlpacaError("unexpected page token in the response")
            if token in seen_tokens:
                raise AlpacaError("pagination did not advance")
            seen_tokens.add(token)
            params["page_token"] = token

        raise AlpacaError(f"too many pages for one batch (more than {self._max_pages})")
