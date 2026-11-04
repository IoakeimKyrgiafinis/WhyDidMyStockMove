"""GDELT's raw Global Knowledge Graph files: one zipped table of articles every 15 minutes.

GDELT's search API allows one request every five seconds and in practice refuses most requests,
so the job reads the raw files instead (no key, no rate limit). Each holds about a thousand
articles and is 4 to 10 MB zipped; a day is 96 files.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from whymoved.adapters.http_policy import RetryPolicy
from whymoved.adapters.json_http import SourceError, SourceUnavailable

BASE_URL = "https://data.gdeltproject.org/gdeltv2/"
STEP = timedelta(minutes=15)
TIMEOUT = 120.0
STAMP = "%Y%m%d%H%M%S"

# Columns of a GKG 2.1 row.
_DATE, _SOURCE, _URL, _ORGS, _TONE, _EXTRAS = 1, 3, 4, 13, 15, 26
_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


@dataclass(frozen=True)
class GkgArticle:
    seen_at: datetime  # when GDELT found it (UTC): never earlier than publication
    domain: str
    url: str
    title: str
    organisations: tuple[str, ...]
    tone: float


def stamps(start: datetime, end: datetime) -> list[datetime]:
    """The 15-minute file times after `start` up to and including `end`."""
    first = start.replace(minute=start.minute - start.minute % 15, second=0, microsecond=0)
    out: list[datetime] = []
    moment = first + STEP
    while moment <= end:
        out.append(moment)
        moment += STEP
    return out


def file_url(stamp: datetime) -> str:
    return f"{BASE_URL}{stamp.strftime(STAMP)}.gkg.csv.zip"


def parse_gkg(content: bytes) -> Iterator[GkgArticle]:
    """The articles of one zipped GKG file; malformed rows are skipped."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        names = archive.namelist()
        if not names:
            return
        text = archive.read(names[0]).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError) as exc:
        raise SourceError(f"GDELT: unreadable file: {exc}") from exc
    for line in text.split("\n"):
        cells = line.split("\t")
        if len(cells) <= _EXTRAS:
            continue
        title = _TITLE.search(cells[_EXTRAS])
        try:
            seen = datetime.strptime(cells[_DATE], STAMP)
        except ValueError:
            continue
        try:
            tone = float(cells[_TONE].split(",", 1)[0])
        except ValueError:
            tone = float("nan")
        yield GkgArticle(
            seen_at=seen,
            domain=cells[_SOURCE].strip().lower(),
            url=cells[_URL].strip(),
            title=title.group(1) if title else "",
            organisations=tuple(o for o in cells[_ORGS].split(";") if o),
            tone=tone,
        )


class GdeltFiles:
    def __init__(
        self,
        client: httpx.Client,
        *,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._retry = retry or RetryPolicy(max_attempts=3)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GdeltFiles:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def articles(self, stamp: datetime) -> list[GkgArticle] | None:
        """The articles in one 15-minute file, or None if GDELT never published it (it skips a
        file now and then)."""
        url = file_url(stamp)
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt == self._retry.max_attempts:
                    raise SourceUnavailable(f"GDELT: network error: {exc}") from None
                self._sleep(self._retry.backoff(attempt))
                continue
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return list(parse_gkg(response.content))
            if response.status_code < 500 and response.status_code != 429:
                raise SourceError(f"GDELT: HTTP {response.status_code} for {url}")
            if attempt == self._retry.max_attempts:
                raise SourceUnavailable(f"GDELT: HTTP {response.status_code} for {url}")
            self._sleep(self._retry.backoff(attempt))
        return None  # pragma: no cover - the loop always returns or raises


def gdelt_files() -> GdeltFiles:
    return GdeltFiles(
        httpx.Client(
            timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "whystockmoved"}
        )
    )
