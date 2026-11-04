from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Self

import pandas as pd
import pytest

from whymoved.adapters.filings_store import ParquetEventStore
from whymoved.adapters.press_store import ParquetPressStore
from whymoved.domain.corporate_events import EVENT_COLUMNS, normalize_events
from whymoved.domain.press import PressRelease
from whymoved.jobs import collect_press


def configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WHYMOVED_ALPACA_KEY_ID", "key")
    monkeypatch.setenv("WHYMOVED_ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("WHYMOVED_SEC_USER_AGENT", "Jane Doe jane@example.com")
    monkeypatch.chdir(tmp_path)


class FakeEdgar:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def press_release(self, cik: int, accession: str) -> tuple[str, PressRelease | None] | None:
        return f"https://sec.test/{accession}.htm", PressRelease(title=accession, summary="s")


def test_releases_are_fetched_newest_first_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure(monkeypatch, tmp_path)
    rows = [
        {
            "ticker": "ACME",
            "cik": 7,
            "accession": f"a{k}",
            "form": "8-K",
            "filing_date": date(2026, 3, 1 + k),
            "accepted_at": None,
            "items": "9.01",
            "primary_document": "d.htm",
        }
        for k in range(3)
    ]
    ParquetEventStore(tmp_path / "data").save(
        normalize_events(pd.DataFrame(rows, columns=EVENT_COLUMNS))
    )
    monkeypatch.setattr(collect_press, "from_settings", lambda _settings: FakeEdgar())

    assert collect_press.main(["--max-filings", "2"]) == 0
    first = ParquetPressStore(tmp_path / "data").load()
    assert collect_press.main([]) == 0
    second = ParquetPressStore(tmp_path / "data").load()

    assert sorted(first["accession"]) == ["a1", "a2"]
    assert sorted(second["accession"]) == ["a0", "a1", "a2"]


def test_the_job_needs_configuration_and_stored_filings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("WHYMOVED_SEC_USER_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert collect_press.main([]) == 2

    configure(monkeypatch, tmp_path)
    assert collect_press.main([]) == 2  # no filings stored yet
