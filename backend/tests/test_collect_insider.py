from __future__ import annotations

from pathlib import Path

import pytest

from whymoved.jobs.collect_insider import build_ticker_by_cik, main

REQUIRED = ("WHYMOVED_ALPACA_KEY_ID", "WHYMOVED_ALPACA_SECRET_KEY", "WHYMOVED_SEC_USER_AGENT")


def test_the_first_symbol_wins_on_a_shared_cik() -> None:
    mapping, missing = build_ticker_by_cik(
        ["GOOGL", "GOOG", "ZZZ"], {"GOOGL": 1652044, "GOOG": 1652044}
    )

    assert mapping == {1652044: "GOOGL"}
    assert missing == ["ZZZ"]


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
