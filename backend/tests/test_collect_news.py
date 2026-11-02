from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from fakes import SECTOR_OF, FakePriceProvider, InMemoryStore, holdings_csv, make_world
from whymoved.jobs.collect_news import load_universe_symbols, main
from whymoved.jobs.nightly import run_nightly, save_outputs

REQUIRED = ("WHYMOVED_ALPACA_KEY_ID", "WHYMOVED_ALPACA_SECRET_KEY", "WHYMOVED_SEC_USER_AGENT")


def write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_universe_symbols_are_read_without_duplicates(tmp_path: Path) -> None:
    path = write(
        tmp_path / "universe.json",
        {"members": [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "AAA"}, {"name": "x"}]},
    )

    assert load_universe_symbols(path) == ["AAA", "BBB"]


def test_bad_universe_files_are_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot read"):
        load_universe_symbols(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="cannot read"):
        load_universe_symbols(broken)
    with pytest.raises(ValueError, match="members list"):
        load_universe_symbols(write(tmp_path / "a.json", {"other": 1}))
    with pytest.raises(ValueError, match="no members"):
        load_universe_symbols(write(tmp_path / "b.json", {"members": []}))


def test_the_nightly_job_output_can_be_read_by_the_news_job(tmp_path: Path) -> None:
    close, volume = make_world(300)
    result = run_nightly(
        FakePriceProvider(close, volume),
        InMemoryStore(),
        holdings_csv(SECTOR_OF),
        history_start=date(2024, 1, 1),
        end=pd.Timestamp(close.index[299]).date(),
    )

    save_outputs(tmp_path, result)

    assert set(load_universe_symbols(tmp_path / "universe.json")) == {"AAA", "BBB", "CCC", "DDD"}


def test_main_fails_fast_without_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    assert main([]) == 2


def test_main_reports_a_missing_universe_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WHYMOVED_ALPACA_KEY_ID", "key")
    monkeypatch.setenv("WHYMOVED_ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("WHYMOVED_SEC_USER_AGENT", "Jane Doe jane@example.com")
    monkeypatch.chdir(tmp_path)

    assert main([]) == 2
