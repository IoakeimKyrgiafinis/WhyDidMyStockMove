from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fakes import FACTORS, SECTOR_OF, FakePriceProvider, InMemoryStore, holdings_csv, make_world
from whymoved.domain.selection import DataQualityError
from whymoved.domain.universe import MARKET_ETF
from whymoved.jobs.nightly import main, run_nightly, save_outputs

HISTORY_START = date(2024, 1, 1)
HOLDINGS = holdings_csv(SECTOR_OF)
REQUIRED = ("WHYMOVED_ALPACA_KEY_ID", "WHYMOVED_ALPACA_SECRET_KEY", "WHYMOVED_SEC_USER_AGENT")


def day(frame: pd.DataFrame, i: int) -> date:
    return pd.Timestamp(frame.index[i]).date()


def setup(n_days: int = 300) -> tuple[FakePriceProvider, InMemoryStore]:
    close, volume = make_world(n_days)
    return FakePriceProvider(close, volume), InMemoryStore()


def test_the_first_night_builds_the_clean_universe() -> None:
    provider, store = setup()

    result = run_nightly(
        provider, store, HOLDINGS, history_start=HISTORY_START, end=day(provider.close, 299)
    )

    tickers = [m.ticker for m in result.members]
    assert tickers[0] == "AAA"
    assert set(tickers) == {"AAA", "BBB", "CCC", "DDD"}
    assert result.selection.duplicates == {"DUP": "AAA"}
    assert result.unmapped == []
    assert result.members[0].sector_etf == "XLK"
    assert result.n_holdings == 5
    assert result.as_of == day(provider.close, 299)
    assert set(provider.calls[0][0]) == set(FACTORS) | set(SECTOR_OF)


def test_holdings_in_an_untracked_sector_are_reported_and_not_fetched() -> None:
    provider, store = setup()
    holdings = holdings_csv({**SECTOR_OF, "ODD": "Conglomerates"})

    result = run_nightly(
        provider, store, holdings, history_start=HISTORY_START, end=day(provider.close, 299)
    )

    assert result.unmapped == ["ODD"]
    assert "ODD" not in provider.calls[0][0]


def test_limit_keeps_only_the_heaviest_holdings() -> None:
    provider, store = setup()

    result = run_nightly(
        provider,
        store,
        HOLDINGS,
        history_start=HISTORY_START,
        end=day(provider.close, 299),
        limit=2,
    )

    requested = set(provider.calls[0][0])
    assert requested == set(FACTORS) | {"AAA", "DUP"}
    assert {m.ticker for m in result.members} == {"AAA"}


def test_the_second_night_is_incremental() -> None:
    provider, store = setup()
    run_nightly(
        provider, store, HOLDINGS, history_start=HISTORY_START, end=day(provider.close, 298)
    )
    provider.calls.clear()

    result = run_nightly(
        provider, store, HOLDINGS, history_start=HISTORY_START, end=day(provider.close, 299)
    )

    assert len(provider.calls) == 1
    assert provider.calls[0][1] > HISTORY_START
    assert result.update.new_symbols == ()
    assert result.update.last_date == day(provider.close, 299)


def test_a_failing_market_factor_stops_the_job() -> None:
    provider, store = setup()
    provider.close[MARKET_ETF] = np.nan

    with pytest.raises(DataQualityError, match=MARKET_ETF):
        run_nightly(
            provider, store, HOLDINGS, history_start=HISTORY_START, end=day(provider.close, 299)
        )


def test_invalid_job_inputs_are_rejected() -> None:
    provider, store = setup()
    end = day(provider.close, 299)

    with pytest.raises(ValueError, match="limit"):
        run_nightly(provider, store, HOLDINGS, history_start=HISTORY_START, end=end, limit=0)
    with pytest.raises(ValueError, match="header"):
        run_nightly(provider, store, "not,a,holdings,file\n", history_start=HISTORY_START, end=end)


def test_outputs_are_written(tmp_path: Path) -> None:
    provider, store = setup()
    result = run_nightly(
        provider, store, HOLDINGS, history_start=HISTORY_START, end=day(provider.close, 299)
    )

    save_outputs(tmp_path / "out", result)

    summary = json.loads((tmp_path / "out" / "universe.json").read_text(encoding="utf-8"))
    quality = pd.read_parquet(tmp_path / "out" / "quality.parquet")
    assert summary["counts"]["clean"] == 4
    assert summary["duplicates"] == {"DUP": "AAA"}
    assert {m["ticker"] for m in summary["members"]} == {"AAA", "BBB", "CCC", "DDD"}
    assert summary["as_of"] == day(provider.close, 299).isoformat()
    assert "passed" in quality.columns
    assert MARKET_ETF in quality.index


def test_main_fails_fast_without_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # so no .env file is found

    assert main([]) == 2


def test_main_reports_a_missing_holdings_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WHYMOVED_ALPACA_KEY_ID", "key")
    monkeypatch.setenv("WHYMOVED_ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("WHYMOVED_SEC_USER_AGENT", "Jane Doe jane@example.com")
    monkeypatch.chdir(tmp_path)

    assert main(["--holdings", str(tmp_path / "nope.csv")]) == 2
