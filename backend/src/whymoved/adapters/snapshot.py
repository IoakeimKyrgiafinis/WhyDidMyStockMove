from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
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
from whymoved.adapters.press_store import PRESS_FILE
from whymoved.domain.corporate_events import empty_events, normalize_events
from whymoved.domain.insider import empty_trades
from whymoved.domain.universe import UniverseMember
from whymoved.memory import release_memory

logger = logging.getLogger(__name__)

UNIVERSE_FILE = "universe.json"
INSIDER_STUDY_FILE = "insider_study.json"
REQUIRED_FILES = (CLOSE_FILE, UNIVERSE_FILE)  # the API never reads volumes
# Produced by collectors that may fail on a given night; the site works without them.
OPTIONAL_FILES = (NEWS_FILE, EVENTS_FILE, SIGNALS_FILE, INSIDER_STUDY_FILE, PRESS_FILE)
NEWS_COLUMNS = ["headline", "source", "url", "created_at", "symbols"]
NEWS_DAYS = 400
BATCH_ROWS = 50_000
# The insider columns the site reads (filing summaries, timeline, studies).
API_TRADE_COLUMNS = [
    "accession",
    "issuer_cik",
    "ticker",
    "filing_date",
    "trade_date",
    "kind",
    "shares",
    "price",
    "owner_name",
    "owner_role",
    "plan",
]
# Repeated text columns of the trades table, stored as categories to keep memory small.
# Not "accession": nearly every filing has its own, so as a category it would still need a
# Python string per filing; it stays an Arrow string instead.
_TRADE_CATEGORIES = (
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


_EVENT_CATEGORIES = ("ticker", "form", "items")


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
    # Press releases stay on disk: the timeline reads one stock's rows at a time.
    press_path: Path | None = None


def load_snapshot(directory: Path) -> Snapshot:
    """Read the nightly job's outputs from `directory`."""
    close_path = directory / CLOSE_FILE
    if not close_path.exists():
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

    # Memory on a 512 MB host: each read briefly needs much more than the table it keeps, and
    # the freed buffers are handed back before the next read (whymoved.memory). Reading the
    # files with the biggest spikes first means each spike lands while little else is held:
    # measured on Linux, a loading peak of about 360 MB instead of 490.
    trades = _optional(_trades_path(directory), _read_trades, empty_trades())
    release_memory()
    news = _optional(directory / NEWS_FILE, _read_news, pd.DataFrame(columns=NEWS_COLUMNS))
    release_memory()
    try:
        # Only closes: the API never uses volumes. 32-bit: seven significant digits is plenty
        # for daily closes, and every calculation converts to 64-bit itself.
        close = _read_close(close_path).sort_index()
    except Exception as exc:  # pyarrow failing on a truncated or corrupt file
        raise SnapshotError(f"cannot read prices: {exc}") from exc
    close.index = pd.DatetimeIndex(close.index).normalize()
    release_memory()
    events = _optional(directory / EVENTS_FILE, _read_events, empty_events())
    release_memory()
    return Snapshot(
        close=close,
        members=members,
        as_of=as_of,
        history_start=history_start,
        news=news,
        events=events,
        trades=trades,
        insider_study=_optional(directory / INSIDER_STUDY_FILE, _read_json, None),
        press_path=directory / PRESS_FILE if (directory / PRESS_FILE).exists() else None,
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
    created = pq.read_table(path, columns=["created_at"]).column("created_at")
    if len(created) == 0:
        return pd.DataFrame(columns=NEWS_COLUMNS)
    newest = pc.max(created).as_py()
    cutoff = pa.scalar(newest - timedelta(days=NEWS_DAYS), type=created.type)
    table = _read_in_batches(
        path, NEWS_COLUMNS, lambda b: pc.greater_equal(b.column("created_at"), cutoff)
    )
    frame: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype)
    frame["created_at"] = pd.to_datetime(frame["created_at"].astype("datetime64[ns]"))
    return frame.sort_values("created_at", kind="mergesort").reset_index(drop=True)


def _read_in_batches(
    path: Path,
    columns: Sequence[str],
    keep: Callable[[pa.RecordBatch], pa.Array],
    read_dictionary: Sequence[str] = (),
) -> pa.Table:
    """Read `columns` a batch of rows at a time, keeping the rows `keep` selects.

    Reading the whole file and then filtering builds every row first: for the news and trades
    files that is a 130 MB spike on top of what is kept. Batch by batch, the peak is what is
    kept plus one batch.
    """
    file = pq.ParquetFile(path, read_dictionary=list(read_dictionary) or None)
    parts = [
        pa.Table.from_batches([batch]).filter(keep(batch))
        for batch in file.iter_batches(batch_size=BATCH_ROWS, columns=list(columns))
    ]
    if not parts:
        return file.schema_arrow.empty_table().select(list(columns))
    return pa.concat_tables(parts, promote_options="permissive")


def _read_close(path: Path) -> pd.DataFrame:
    """Daily closes as 32-bit floats, converted inside Arrow so a 64-bit copy of the whole
    table never exists in pandas. Seven significant digits is plenty for daily closes, and
    every calculation converts to 64-bit itself."""
    table = pq.read_table(path)
    schema = pa.schema(
        [
            field.with_type(pa.float32()) if pa.types.is_floating(field.type) else field
            for field in table.schema
        ],
        metadata=table.schema.metadata,
    )
    frame: pd.DataFrame = table.cast(schema).to_pandas()
    return frame


def _read_events(path: Path) -> pd.DataFrame:
    """Company filings with compact text columns: repeated values as categories, the unique
    accession numbers and document names as Arrow strings (a quarter of the memory)."""
    events = normalize_events(pd.read_parquet(path))
    for name in _EVENT_CATEGORIES:
        events[name] = events[name].astype("category")
    for name in ("accession", "primary_document"):
        events[name] = events[name].astype(pd.ArrowDtype(pa.string()))
    return events


def _trades_path(directory: Path) -> Path:
    """The small signals extract when present; the full table otherwise (local runs of the
    jobs before the extract existed)."""
    signals = directory / SIGNALS_FILE
    return signals if signals.exists() else directory / TRADES_FILE


def _read_trades(path: Path) -> pd.DataFrame:
    """Open-market purchases and sales only, and only the columns the site uses.

    Awards, option exercises and tax withholding are most of the table (about 60% of seven
    years of Form 4 rows) but carry almost no information, and the studies do not use them.
    The repeated text columns arrive dictionary-encoded and become pandas categories without
    a Python string per value. All of this keeps the API inside a 512 MB host.
    """
    names = pq.ParquetFile(path).schema_arrow.names
    missing = [name for name in API_TRADE_COLUMNS if name not in names]
    if missing:
        raise ValueError(f"insider trades are missing columns: {missing}")
    dictionaries = [n for n in _TRADE_CATEGORIES if n in API_TRADE_COLUMNS]
    if path.name == SIGNALS_FILE:
        # Already only purchases and sales: one read with no filter is the cheapest (measured
        # on Linux: a 130 MB smaller peak than filtering batch by batch).
        table = pq.read_table(path, columns=API_TRADE_COLUMNS, read_dictionary=dictionaries)
    else:
        signal = pa.array(list(SIGNAL_KINDS))
        table = _read_in_batches(
            path,
            API_TRADE_COLUMNS,
            lambda b: pc.is_in(pc.cast(b.column("kind"), pa.string()), value_set=signal),
            read_dictionary=dictionaries,
        )
    # Each batch brings its own dictionaries: merge them in Arrow (no strings are expanded).
    table = table.unify_dictionaries()
    columns = {}
    for name in API_TRADE_COLUMNS:
        column = table.column(name).combine_chunks()
        if name in _TRADE_CATEGORIES and not pa.types.is_dictionary(column.type):
            column = column.dictionary_encode()
        columns[name] = column
    trades: pd.DataFrame = pa.table(columns).to_pandas(
        types_mapper=lambda t: pd.ArrowDtype(t) if t in (pa.string(), pa.large_string()) else None
    )
    for name in ("shares", "price"):
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
) -> bool:
    """Download the snapshot files from `base_url` (a GitHub release download URL).

    Every file is downloaded to a temporary name first, and only when all have arrived are
    they renamed into place, so a failed download never leaves a mixed snapshot behind.
    Optional files that are not published are skipped, keeping any older copy. Returns
    whether any file differs from the copy already on disk.
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
                current = directory / name
                if current.exists() and current.read_bytes() == content:
                    continue  # unchanged since the last download
                temp = directory / f".{name}.download"
                temps[name] = temp
                temp.write_bytes(content)
        for name, temp in temps.items():
            os.replace(temp, directory / name)
        return bool(temps)
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
