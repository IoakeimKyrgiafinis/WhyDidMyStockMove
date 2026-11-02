from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from whymoved.adapters.http_policy import RetryPolicy
from whymoved.adapters.parquet_store import CLOSE_FILE, VOLUME_FILE, ParquetPriceStore
from whymoved.domain.universe import UniverseMember

logger = logging.getLogger(__name__)

UNIVERSE_FILE = "universe.json"
REQUIRED_FILES = (CLOSE_FILE, VOLUME_FILE, UNIVERSE_FILE)


class SnapshotError(Exception):
    """The snapshot is missing, unreadable or could not be downloaded."""


@dataclass(frozen=True, eq=False)
class Snapshot:
    """What the API serves from: daily closes and the clean universe."""

    close: pd.DataFrame
    members: list[UniverseMember]
    as_of: date
    history_start: date


def load_snapshot(directory: Path) -> Snapshot:
    """Read the nightly job's outputs from `directory`."""
    try:
        bars = ParquetPriceStore(directory).load()
    except Exception as exc:  # StoreError, or pyarrow failing on a truncated file
        raise SnapshotError(f"cannot read prices: {exc}") from exc
    if bars is None:
        raise SnapshotError(f"no price files in {directory}")

    path = directory / UNIVERSE_FILE
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
        members = [
            UniverseMember(
                ticker=str(m["ticker"]),
                name=str(m["name"]),
                sector=str(m["sector"]),
                sector_etf=str(m["sector_etf"]),
                weight=float(m["weight"]),
            )
            for m in summary["members"]
        ]
        as_of = date.fromisoformat(str(summary["as_of"]))
        history_start = date.fromisoformat(str(summary["history_start"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SnapshotError(f"cannot read {path}: {exc}") from exc

    close = bars.close.sort_index()
    close.index = pd.DatetimeIndex(close.index).normalize()
    return Snapshot(close=close, members=members, as_of=as_of, history_start=history_start)


def download_snapshot(
    base_url: str,
    directory: Path,
    *,
    names: Sequence[str] = REQUIRED_FILES,
    transport: httpx.BaseTransport | None = None,
    retry: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Download the snapshot files from `base_url` (a GitHub release download URL).

    Every file is downloaded to a temporary name first, and only when all have arrived are
    they renamed into place, so a failed download never leaves a mixed snapshot behind.
    """
    policy = retry or RetryPolicy(max_attempts=4, base_delay=2.0)
    base = base_url if base_url.endswith("/") else base_url + "/"
    directory.mkdir(parents=True, exist_ok=True)
    temps: dict[str, Path] = {}
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True, transport=transport) as client:
            for name in names:
                temp = directory / f".{name}.download"
                temps[name] = temp
                temp.write_bytes(_fetch(client, base + name, policy, sleep))
        for name, temp in temps.items():
            os.replace(temp, directory / name)
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)


def _fetch(
    client: httpx.Client, url: str, policy: RetryPolicy, sleep: Callable[[float], None]
) -> bytes:
    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            problem = f"network error: {exc}"
        else:
            if response.status_code == 200:
                return response.content
            if response.status_code == 404:
                raise SnapshotError(f"{url} was not found; has the data workflow run yet?")
            problem = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                raise SnapshotError(f"downloading {url} failed: {problem}")
        if attempt < policy.max_attempts:
            logger.warning("downloading %s failed (%s), retrying", url, problem)
            sleep(policy.backoff(attempt))
    raise SnapshotError(f"downloading {url} failed after {policy.max_attempts} attempts")
