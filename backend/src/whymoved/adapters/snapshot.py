from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from whymoved.adapters.filings_store import EVENTS_FILE
from whymoved.adapters.http_policy import RetryPolicy
from whymoved.adapters.insider_store import SIGNAL_KINDS, SIGNALS_FILE, TRADES_FILE
from whymoved.adapters.news_store import NEWS_FILE
from whymoved.adapters.parquet_store import CLOSE_FILE
from whymoved.domain.corporate_events import empty_events, normalize_events
from whymoved.domain.insider import TRADE_COLUMNS, empty_trades
from whymoved.domain.universe import UniverseMember

logger = logging.getLogger(__name__)

UNIVERSE_FILE = "universe.json"
INSIDER_STUDY_FILE = "insider_study.json"
REQUIRED_FILES = (CLOSE_FILE, UNIVERSE_FILE)  # the API never reads volumes
# Produced by collectors that may fail on a given night; the site works without them.
OPTIONAL_FILES = (NEWS_FILE, EVENTS_FILE, SIGNALS_FILE, INSIDER_STUDY_FILE)
NEWS_COLUMNS = ["headline", "source", "url", "created_at", "symbols"]
NEWS_DAYS = 400
# Repeated text columns of the trades table, stored as categories to keep memory small.
_TRADE_CATEGORIES = (
    "accession",
    "ticker",
    "form",
    "code",
    "kind",
    "acquired",
    "direct",
    "owner_name",
    "owner_role",
    "plan",
    "source",
)


class SnapshotError(Exception):
    """The snapshot is missing, unreadable or could not be downloaded."""


class _NotPublished(SnapshotError):
    """The release has no such file (HTTP 404)."""


@dataclass(frozen=True, eq=False)
class Snapshot:
    """What the API serves from: daily closes and the clean universe."""

    close: pd.DataFrame
    members: list[UniverseMember]
    as_of: date
    history_start: date
    news: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=NEWS_COLUMNS))
    events: pd.DataFrame = field(default_factory=empty_events)
    trades: pd.DataFrame = field(default_factory=empty_trades)
    insider_study: dict[str, object] | None = None


def load_snapshot(directory: Path) -> Snapshot:
    """Read the nightly job's outputs from `directory`."""
    # Only closes: the API never uses volumes, and reading them costs memory on a small host.
    close_path = directory / CLOSE_FILE
    if not close_path.exists():
        raise SnapshotError(f"no price files in {directory}")
    try:
        raw_close = pd.read_parquet(close_path)
    except Exception as exc:  # pyarrow failing on a truncated or corrupt file
        raise SnapshotError(f"cannot read prices: {exc}") from exc

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

    # 32-bit prices: seven significant digits is plenty for daily closes, every calculation
    # converts to 64-bit itself, and it halves the largest table on a 512 MB host.
    close = raw_close.sort_index().astype("float32")
    close.index = pd.DatetimeIndex(close.index).normalize()
    return Snapshot(
        close=close,
        members=members,
        as_of=as_of,
        history_start=history_start,
        news=_optional(directory / NEWS_FILE, _read_news, pd.DataFrame(columns=NEWS_COLUMNS)),
        events=_optional(directory / EVENTS_FILE, _read_events, empty_events()),
        trades=_optional(_trades_path(directory), _read_trades, empty_trades()),
        insider_study=_optional(directory / INSIDER_STUDY_FILE, _read_json, None),
    )


def _optional[T](path: Path, read: Callable[[Path], T], default: T) -> T:
    """Read an optional file; a missing or broken one is logged and replaced by `default`."""
    if not path.exists():
        return default
    try:
        return read(path)
    except Exception as exc:  # a broken optional file must not take the site down
        logger.error("ignoring %s: %s", path.name, exc)
        return default


def _read_news(path: Path) -> pd.DataFrame:
    """The last NEWS_DAYS of headlines, in Arrow's compact string format.

    Two years of news for the whole universe is hundreds of thousands of rows: too many to
    hold as Python strings, and more than a 512 MB host can spare. The snapshot keeps all of
    it; the API serves the recent part.
    """
    frame = pd.read_parquet(path, columns=NEWS_COLUMNS, dtype_backend="pyarrow")
    frame["created_at"] = pd.to_datetime(frame["created_at"].astype("datetime64[ns]"))
    if len(frame):
        cutoff = frame["created_at"].max() - pd.Timedelta(days=NEWS_DAYS)
        frame = frame[frame["created_at"] >= cutoff]
    return frame.sort_values("created_at", kind="mergesort").reset_index(drop=True)


def _read_events(path: Path) -> pd.DataFrame:
    return normalize_events(pd.read_parquet(path))


def _trades_path(directory: Path) -> Path:
    """The small signals extract when present; the full table otherwise (local runs of the
    jobs before the extract existed)."""
    signals = directory / SIGNALS_FILE
    return signals if signals.exists() else directory / TRADES_FILE


def _read_trades(path: Path) -> pd.DataFrame:
    """Open-market purchases and sales only.

    Awards, option exercises and tax withholding are most of the table (about 60% of seven
    years of Form 4 rows) but carry almost no information, and the studies do not use them.
    Leaving them out keeps the API inside a small host's memory.
    """
    table = pq.read_table(path, filters=[("kind", "in", list(SIGNAL_KINDS))])
    missing = [name for name in TRADE_COLUMNS if name not in table.column_names]
    if missing:
        raise ValueError(f"insider trades are missing columns: {missing}")
    # Dictionary-encode the repeated text columns inside Arrow, so pandas receives categories
    # directly. Going through Python strings first briefly needed several hundred MB.
    columns = {}
    for name in TRADE_COLUMNS:
        column = table.column(name)
        if name in _TRADE_CATEGORIES:
            column = pc.cast(column, pa.string()).combine_chunks().dictionary_encode()
        columns[name] = column
    trades: pd.DataFrame = pa.table(columns).to_pandas()
    for name in ("shares", "price", "shares_after"):
        trades[name] = pd.to_numeric(trades[name], errors="coerce").astype("float64")
    trades["issuer_cik"] = pd.to_numeric(trades["issuer_cik"]).astype("int64")
    for name in ("filing_date", "trade_date"):
        trades[name] = pd.to_datetime(trades[name]).dt.as_unit("ns")
    return trades


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def download_snapshot(
    base_url: str,
    directory: Path,
    *,
    names: Sequence[str] = REQUIRED_FILES,
    optional: Sequence[str] = OPTIONAL_FILES,
    transport: httpx.BaseTransport | None = None,
    retry: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Download the snapshot files from `base_url` (a GitHub release download URL).

    Every file is downloaded to a temporary name first, and only when all have arrived are
    they renamed into place, so a failed download never leaves a mixed snapshot behind.
    Optional files that are not published are skipped, keeping any older copy.
    """
    policy = retry or RetryPolicy(max_attempts=4, base_delay=2.0)
    base = base_url if base_url.endswith("/") else base_url + "/"
    directory.mkdir(parents=True, exist_ok=True)
    temps: dict[str, Path] = {}
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True, transport=transport) as client:
            for name in [*names, *optional]:
                try:
                    content = _fetch(client, base + name, policy, sleep)
                except _NotPublished:
                    if name in optional:
                        logger.info("%s is not published; skipping it", name)
                        continue
                    raise
                temp = directory / f".{name}.download"
                temps[name] = temp
                temp.write_bytes(content)
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
                raise _NotPublished(f"{url} was not found; has the data workflow run yet?")
            problem = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                raise SnapshotError(f"downloading {url} failed: {problem}")
        if attempt < policy.max_attempts:
            logger.warning("downloading %s failed (%s), retrying", url, problem)
            sleep(policy.backoff(attempt))
    raise SnapshotError(f"downloading {url} failed after {policy.max_attempts} attempts")
