from __future__ import annotations

import pytest

from whymoved.domain.universe import (
    SECTOR_ETF_TICKERS,
    build_universe,
    normalize_symbol,
    parse_holdings,
    sector_etf,
    sector_map,
)

SAMPLE = """iShares Russell 3000 ETF
Fund Holdings as of,"Oct 30, 2026"
Shares Outstanding,"50,000,000.00"
 
Ticker,Name,Sector,Asset Class,Market Value,Weight (%)
"NVDA","NVIDIA CORP","Information Technology","Equity","1,000,000.00","6.90"
"AAPL","APPLE INC","Information Technology","Equity","900,000.00","6.20"
"GOOGL","ALPHABET INC CLASS A","Communication","Equity","300,000.00","1.98"
"GOOG","ALPHABET INC CLASS C","Communication","Equity","250,000.00","1.62"
"BRKB","BERKSHIRE HATHAWAY INC CLASS B","Financials","Equity","200,000.00","1.49"
"XOM","EXXON MOBIL CORP","Energy","Equity","100,000.00","0.80"
"NVDA","NVIDIA CORP","Information Technology","Equity","1,000.00","0.01"
"ZZZZ","MYSTERY CO","Other","Equity","5,000.00","0.05"
"USD","USD CASH","Cash and/or Derivatives","Cash","10,000.00","0.10"
"-","FUTURES","Cash and/or Derivatives","Futures","1,000.00","0.00"
"The content contained herein is owned or licensed by the fund sponsor."
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        (" msft ", "MSFT"),
        ("BRK-B", "BRK.B"),
        ("BRK/B", "BRK.B"),
        ("BRK B", "BRK.B"),
        ("BRKB", "BRK.B"),
        ("BFB", "BF.B"),
        ("-", None),
        ("", None),
        ("TOOLONGSYM", None),
        ("A1", None),
    ],
)
def test_normalize_symbol(raw: str, expected: str | None) -> None:
    assert normalize_symbol(raw) == expected


def test_parse_holdings_keeps_equities_sorted_by_weight() -> None:
    holdings = parse_holdings(SAMPLE)

    assert [h.ticker for h in holdings] == ["NVDA", "AAPL", "GOOGL", "GOOG", "BRK.B", "XOM", "ZZZZ"]
    assert holdings[0].weight == pytest.approx(6.9)  # the duplicate row with weight 0.01 lost
    assert holdings[0].sector == "Information Technology"
    assert holdings[4].name.startswith("BERKSHIRE")


def test_parse_holdings_needs_a_header_row() -> None:
    with pytest.raises(ValueError, match="header"):
        parse_holdings("just,some,rows\n1,2,3\n")


def test_parse_holdings_without_optional_columns() -> None:
    text = "Ticker,Sector\nAAPL,Information Technology\nXOM,Energy\n"

    holdings = parse_holdings(text)

    assert {h.ticker for h in holdings} == {"AAPL", "XOM"}
    assert all(h.weight == 0.0 for h in holdings)


def test_sector_etf_lookup_is_case_insensitive() -> None:
    assert sector_etf("Information Technology") == "XLK"
    assert sector_etf("  energy ") == "XLE"
    assert sector_etf("Communication") == "XLC"
    assert sector_etf("Cash and/or Derivatives") is None
    assert len(SECTOR_ETF_TICKERS) == 11


def test_build_universe_drops_unmapped_sectors() -> None:
    members, unmapped = build_universe(parse_holdings(SAMPLE))

    assert unmapped == ["ZZZZ"]
    assert [m.ticker for m in members] == ["NVDA", "AAPL", "GOOGL", "GOOG", "BRK.B", "XOM"]
    mapping = sector_map(members)
    assert mapping["NVDA"] == "XLK"
    assert mapping["GOOGL"] == "XLC"
    assert mapping["BRK.B"] == "XLF"
    assert mapping["XOM"] == "XLE"
