from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from whymoved.adapters.snapshot import SnapshotError, download_snapshot, load_snapshot
from whymoved.services.explain import MoveExplainer

logger = logging.getLogger(__name__)

Status = Literal["ready", "loading", "unavailable"]


class DataUnavailable(Exception):
    """No snapshot is loaded (yet)."""


class DataState:
    """Holds the current explainer and swaps it atomically when a new snapshot loads."""

    def __init__(self, explainer: MoveExplainer | None = None) -> None:
        self._lock = threading.Lock()
        self._explainer = explainer
        self._error: str | None = None
        self._loading = explainer is None
        self.loaded_at: datetime | None = datetime.now(UTC) if explainer else None

    @property
    def explainer(self) -> MoveExplainer:
        with self._lock:
            if self._explainer is None:
                if self._loading:
                    raise DataUnavailable("the data is still loading, try again in a minute")
                raise DataUnavailable(self._error or "no data is loaded")
            return self._explainer

    def status(self) -> tuple[Status, str | None]:
        with self._lock:
            if self._explainer is not None:
                return "ready", None
            if self._loading:
                return "loading", "the data is still loading"
            return "unavailable", self._error

    def refresh(self, data_dir: Path, snapshot_url: str) -> None:
        """Download (if a URL is set) and load the snapshot. Keeps the old data on failure."""
        try:
            if snapshot_url:
                download_snapshot(snapshot_url, data_dir)
            explainer = MoveExplainer(load_snapshot(data_dir))
        except (SnapshotError, ValueError) as exc:
            logger.error("loading the snapshot failed: %s", exc)
            with self._lock:
                self._error = str(exc)
                self._loading = False
            return
        with self._lock:
            self._explainer = explainer
            self._error = None
            self._loading = False
            self.loaded_at = datetime.now(UTC)
        logger.info("snapshot loaded: %d stocks as of %s", len(explainer.members), explainer.as_of)
