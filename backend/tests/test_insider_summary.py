from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from whymoved.domain.insider import TRADE_COLUMNS, normalize_trades
from whymoved.domain.insider_summary import FILING_COLUMNS, signal_of, summarize_filings


def row(accession: str, code: str, kind: str, shares: float, price: float | None, **kw: object):  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "accession": accession,
        "issuer_cik": 7,
        "ticker": "AAA",
        "form": "4",
        "filing_date": date(2026, 3, 5),
        "trade_date": date(2026, 3, 3),
        "code": code,
        "kind": kind,
        "shares": shares,
        "price": price,
        "acquired": "A",
        "direct": "D",
        "shares_after": 1.0,
        "owner_name": "Doe Jane",
        "owner_role": "CFO",
        "plan": "no",
        "source": "bulk",
    }
    base.update(kw)
    return base


def frame(*rows: dict[str, object]) -> pd.DataFrame:
    return normalize_trades(pd.DataFrame(list(rows), columns=TRADE_COLUMNS))


def test_signal_priorities() -> None:
    assert signal_of({"open_market_buy", "routine"}, "no") == "buy"
    assert signal_of({"open_market_sell"}, "no") == "sale"
    assert signal_of({"open_market_sell"}, "yes") == "planned_sale"
    assert signal_of({"open_market_sell"}, "unknown") == "sale"
    assert signal_of({"routine"}, "no") == "routine"
    assert signal_of({"other"}, "no") == "other"


def test_a_buy_counts_only_the_purchases() -> None:
    trades = frame(
        row("a1", "P", "open_market_buy", 100.0, 10.0),
        row("a1", "P", "open_market_buy", 300.0, 20.0, trade_date=date(2026, 3, 4)),
        row("a1", "A", "routine", 5000.0, None),
    )

    out = summarize_filings(trades)

    assert list(out.columns) == FILING_COLUMNS
    r = out.iloc[0]
    assert r["signal"] == "buy"
    assert r["shares"] == pytest.approx(400.0)
    assert r["value"] == pytest.approx(100 * 10 + 300 * 20)
    assert r["price"] == pytest.approx(7000 / 400)
    assert r["n_transactions"] == 3
    assert r["lag_days"] == 1  # last purchase on the 4th, filed on the 5th


def test_a_planned_sale_is_kept_apart() -> None:
    out = summarize_filings(frame(row("s1", "S", "open_market_sell", 50.0, 9.0, plan="yes")))

    assert out.iloc[0]["signal"] == "planned_sale"


def test_unpriced_filings_have_no_value() -> None:
    out = summarize_filings(frame(row("r1", "A", "routine", 10.0, None)))

    assert pd.isna(out.iloc[0]["value"])
    assert pd.isna(out.iloc[0]["price"])


def test_newest_filings_come_first() -> None:
    out = summarize_filings(
        frame(
            row("old", "P", "open_market_buy", 1.0, 1.0, filing_date=date(2025, 1, 2)),
            row("new", "P", "open_market_buy", 1.0, 1.0, filing_date=date(2026, 1, 2)),
        )
    )

    assert list(out["accession"]) == ["new", "old"]


def test_no_trades_gives_an_empty_table() -> None:
    out = summarize_filings(frame())

    assert out.empty
    assert list(out.columns) == FILING_COLUMNS
