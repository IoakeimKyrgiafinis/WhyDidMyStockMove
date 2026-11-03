from __future__ import annotations

import json
import os
from collections.abc import Collection
from pathlib import Path

import pandas as pd

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.insider import empty_trades, normalize_trades

TRADES_FILE = "insider_trades.parquet"
# Open-market purchases and sales only, for the API: a small file it can read on a host with
# little memory. The full table stays for the jobs.
SIGNALS_FILE = "insider_signals.parquet"
SIGNAL_KINDS = ("open_market_buy", "open_market_sell")
STATE_FILE = "insider_quarters.json"


class ParquetTradeStore:
    """The insider trades table as one parquet file, written to a temporary name first and
    renamed, so a failed write does not damage the existing data."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self) -> pd.DataFrame:
        path = self._directory / TRADES_FILE
        if not path.exists():
            return empty_trades()
        try:
            return normalize_trades(pd.read_parquet(path))
        except (ValueError, OSError) as exc:
            raise StoreError(f"the insider trades file is unreadable or malformed: {exc}") from exc

    def save(self, trades: pd.DataFrame) -> None:
        """Write the full table and the signals-only extract; each replaces its file atomically."""
        self._directory.mkdir(parents=True, exist_ok=True)
        table = normalize_trades(trades)
        signals = table[table["kind"].isin(SIGNAL_KINDS)].reset_index(drop=True)
        for name, frame in ((TRADES_FILE, table), (SIGNALS_FILE, signals)):
            temp = self._directory / f".{name}.tmp"
            try:
                frame.to_parquet(temp, engine="pyarrow")
                os.replace(temp, self._directory / name)
            finally:
                temp.unlink(missing_ok=True)


def load_imported(directory: Path) -> set[str]:
    """Quarter keys (like '2026q2') that have been imported before."""
    path = directory / STATE_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    keys = data.get("imported") if isinstance(data, dict) else None
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        raise StoreError(f"{path} does not have the expected format")
    return set(keys)


def load_covered_ciks(directory: Path) -> set[int] | None:
    """Companies whose full history is stored, or None when the state predates tracking."""
    path = directory / STATE_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc
    ciks = data.get("ciks") if isinstance(data, dict) else None
    if ciks is None:
        return None
    if not isinstance(ciks, list) or not all(isinstance(c, int) for c in ciks):
        raise StoreError(f"{path} does not have the expected format")
    return set(ciks)


def save_imported(
    directory: Path, keys: Collection[str], ciks: Collection[int] | None = None
) -> None:
    """Record imported quarters and, when given, the companies their history covers."""
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{STATE_FILE}.tmp"
    state: dict[str, object] = {"imported": sorted(keys)}
    if ciks is not None:
        state["ciks"] = sorted(ciks)
    try:
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temp, directory / STATE_FILE)
    finally:
        temp.unlink(missing_ok=True)
