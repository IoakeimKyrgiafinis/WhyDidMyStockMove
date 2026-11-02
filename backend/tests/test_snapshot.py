from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd
import pytest

from market import make_market, write_snapshot
from whymoved.adapters.http_policy import RetryPolicy
from whymoved.adapters.snapshot import (
    REQUIRED_FILES,
    SnapshotError,
    download_snapshot,
    load_snapshot,
)

BASE = "https://example.test/releases/download/data-latest/"


def test_load_reads_prices_and_members(tmp_path: Path) -> None:
    market = make_market()
    write_snapshot(tmp_path, market)

    snapshot = load_snapshot(tmp_path)

    assert [m.ticker for m in snapshot.members] == ["AAA", "BBB", "ABC", "NEW"]
    assert snapshot.as_of == market.snapshot.as_of
    pd.testing.assert_frame_equal(
        snapshot.close, market.close, check_freq=False, check_index_type=False
    )


def test_load_without_files_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="no price files"):
        load_snapshot(tmp_path)


def test_load_with_a_broken_universe_file_fails_clearly(tmp_path: Path) -> None:
    write_snapshot(tmp_path, make_market())
    (tmp_path / "universe.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(SnapshotError, match="universe.json"):
        load_snapshot(tmp_path)


def _serve(source: Path, failures: dict[str, list[int]] | None = None) -> httpx.MockTransport:
    pending = {k: list(v) for k, v in (failures or {}).items()}

    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).removeprefix(BASE)
        if pending.get(name):
            return httpx.Response(pending[name].pop(0))
        path = source / name
        if not path.exists():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    return httpx.MockTransport(handler)


def test_download_then_load(tmp_path: Path) -> None:
    source, target = tmp_path / "release", tmp_path / "data"
    source.mkdir()
    write_snapshot(source, make_market())

    download_snapshot(BASE, target, transport=_serve(source), sleep=lambda _: None)

    assert sorted(p.name for p in target.iterdir()) == sorted(REQUIRED_FILES)
    assert len(load_snapshot(target).members) == 4


def test_download_retries_server_errors(tmp_path: Path) -> None:
    source, target = tmp_path / "release", tmp_path / "data"
    source.mkdir()
    write_snapshot(source, make_market())
    sleeps: list[float] = []

    download_snapshot(
        BASE,
        target,
        transport=_serve(source, {"universe.json": [503, 502]}),
        retry=RetryPolicy(max_attempts=3, base_delay=1.0),
        sleep=sleeps.append,
    )

    assert sleeps == [1.0, 2.0]


def test_a_failed_download_keeps_the_old_files(tmp_path: Path) -> None:
    source, target = tmp_path / "release", tmp_path / "data"
    source.mkdir()
    write_snapshot(source, make_market())
    write_snapshot(target, make_market(seed=99))
    before = (target / "prices_close.parquet").read_bytes()
    (source / "universe.json").unlink()

    with pytest.raises(SnapshotError, match="not found"):
        download_snapshot(BASE, target, transport=_serve(source), sleep=lambda _: None)

    assert (target / "prices_close.parquet").read_bytes() == before
    assert not list(target.glob(".*.download"))


def test_client_errors_are_not_retried(tmp_path: Path) -> None:
    sleeps: list[float] = []

    with pytest.raises(SnapshotError, match="HTTP 403"):
        download_snapshot(
            BASE,
            tmp_path,
            transport=httpx.MockTransport(lambda _: httpx.Response(403)),
            sleep=sleeps.append,
        )

    assert sleeps == []
