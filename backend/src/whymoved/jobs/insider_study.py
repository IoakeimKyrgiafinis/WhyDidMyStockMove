"""Pooled event study of insider trades across the whole universe (nightly batch job).

Per stock there are too few insider purchases to test anything, so the study pools every
stock's events, clusters them by date, and writes the result for the API to serve.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from whymoved.adapters.snapshot import INSIDER_STUDY_FILE, SnapshotError, load_snapshot
from whymoved.domain.events import EventWindows, build_event_windows
from whymoved.domain.pooled import PooledStudy, pool_event_windows
from whymoved.services.explain import MoveExplainer, NotEnoughData
from whymoved.services.timeline import POST, PRE, STUDY_TESTS

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
SIGNALS = ("buy", "sale", "planned_sale")


def collect_windows(explainer: MoveExplainer, signal: str) -> tuple[list[EventWindows], int]:
    """Event windows for every stock with filings of `signal`; also the number skipped."""
    out: list[EventWindows] = []
    skipped = 0
    for member in explainer.members:
        insiders = explainer.timeline.insiders(member.ticker)
        if insiders.empty:
            continue
        days = [pd.Timestamp(d) for d in insiders.loc[insiders["signal"] == signal, "day"].dropna()]
        if not days:
            continue
        try:
            daily = explainer.daily(member.ticker)
        except NotEnoughData:
            skipped += 1
            continue
        windows = build_event_windows(daily["unexplained"], daily["sigma"], days, PRE, POST)
        if windows.n_used:
            out.append(windows)
    return out, skipped


def study_to_json(study: PooledStudy) -> dict[str, object]:
    def floats(values: np.ndarray) -> list[float]:
        return [float(v) for v in values]

    return {
        "relative_days": [int(d) for d in study.relative_days],
        "mean_car": floats(study.mean_car),
        "ci_low": floats(study.ci_low),
        "ci_high": floats(study.ci_high),
        "tests": [
            {
                "start": t.start,
                "end": t.end,
                "mean_car": t.mean_car,
                "se": t.se,
                "t_stat": t.t_stat,
                "p_value": t.p_value,
                "ci_low": t.ci_low,
                "ci_high": t.ci_high,
                "positive_share": t.positive_share,
            }
            for t in study.tests
        ],
        "n_events": study.n_events,
        "n_stocks": study.n_stocks,
        "n_clusters": study.n_clusters,
        "level": study.level,
    }


def run_study(explainer: MoveExplainer) -> dict[str, object]:
    studies: dict[str, object] = {}
    for signal in SIGNALS:
        windows, skipped = collect_windows(explainer, signal)
        try:
            study = pool_event_windows(windows, STUDY_TESTS)
        except ValueError as exc:
            logger.warning("%s: %s", signal, exc)
            continue
        logger.info(
            "%s: %d events in %d stocks on %d dates (%d stocks lacked history)",
            signal,
            study.n_events,
            study.n_stocks,
            study.n_clusters,
            skipped,
        )
        studies[signal] = study_to_json(study)
    return {
        "as_of": explainer.as_of.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pre": PRE,
        "post": POST,
        "studies": studies,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pooled insider-trade event study.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    data_dir = Path(args.data_dir)
    try:
        explainer = MoveExplainer(load_snapshot(data_dir), cache_size=64)
    except (SnapshotError, ValueError) as exc:
        logger.error("cannot load the snapshot: %s", exc)
        return 1
    if explainer.timeline.insiders_available == 0:
        logger.error("no insider trades in the snapshot; run collect_insider first")
        return 1

    result = run_study(explainer)
    temp = data_dir / f".{INSIDER_STUDY_FILE}.tmp"
    try:
        temp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        os.replace(temp, data_dir / INSIDER_STUDY_FILE)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("wrote %s", data_dir / INSIDER_STUDY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
