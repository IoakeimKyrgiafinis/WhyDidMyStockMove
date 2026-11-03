from __future__ import annotations

import gc
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
        """Download (if a URL is set) and load the snapshot.

        An unchanged snapshot is not reloaded. A changed one replaces the old data, which is
        released FIRST: two copies of the snapshot do not fit in a 512 MB host, so for the few
        seconds of a reload the API answers "loading" (the site retries on its own). A failed
        download keeps the old data.
        """
        try:
            changed = download_snapshot(snapshot_url, data_dir) if snapshot_url else True
        except SnapshotError as exc:
            logger.error("downloading the snapshot failed: %s", exc)
            with self._lock:
                if self._explainer is None:
                    self._error = str(exc)
                    self._loading = False
            return
        with self._lock:
            loaded = self._explainer is not None
            if loaded and not changed:
                logger.info("snapshot unchanged; nothing to reload")
                return
            self._explainer = None
            self._loading = True
        gc.collect()
        try:
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
