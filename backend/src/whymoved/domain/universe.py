from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass

MARKET_ETF = "IWV"

# Sector names as they are likely to appear in holdings files, mapped to the Select
# Sector SPDR ETFs. Unmapped sectors (cash, other) are dropped from the universe.
SECTOR_ETFS: dict[str, str] = {
    "information technology": "XLK",
    "technology": "XLK",
    "financials": "XLF",
    "financial services": "XLF",
    "health care": "XLV",
    "healthcare": "XLV",
    "consumer discretionary": "XLY",
    "consumer cyclical": "XLY",
    "consumer staples": "XLP",
    "consumer defensive": "XLP",
    "industrials": "XLI",
    "energy": "XLE",
    "materials": "XLB",
    "basic materials": "XLB",
    "utilities": "XLU",
    "real estate": "XLRE",
    "communication": "XLC",
    "communication services": "XLC",
}
SECTOR_ETF_TICKERS: tuple[str, ...] = tuple(sorted(set(SECTOR_ETFS.values())))

# Holdings files sometimes drop the dot in class-share tickers. Extend as needed.
TICKER_ALIASES: dict[str, str] = {"BRKB": "BRK.B", "BFB": "BF.B"}

_VALID_SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    sector: str
    weight: float


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str
    sector: str
    sector_etf: str
    weight: float


def normalize_symbol(raw: str) -> str | None:
    """Canonical ticker (Alpaca style, class shares with a dot), or None if unusable."""
    text = re.sub(r"[-/\s]+", ".", raw.strip().upper())
    text = TICKER_ALIASES.get(text, text)
    return text if _VALID_SYMBOL.fullmatch(text) else None


def sector_etf(sector: str) -> str | None:
    """The sector ETF for a sector name, or None if the sector is not tracked."""
    return SECTOR_ETFS.get(sector.strip().lower())


def _cells(row: list[str]) -> list[str]:
    return [cell.strip().lower() for cell in row]


def _column(header: list[str], *names: str) -> int | None:
    for i, cell in enumerate(header):
        if any(cell == name or cell.startswith(name) for name in names):
            return i
    return None


def _parse_number(cell: str) -> float:
    try:
        return float(cell.replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_holdings(text: str) -> list[Holding]:
    """Parse an iShares-style holdings CSV.

    The table is found by searching for a row with Ticker and Sector columns, so any
    metadata lines above it and disclaimer lines below it are ignored. Only equity rows
    with a usable ticker are kept. If a ticker repeats, the larger weight wins. The result
    is sorted by weight, largest first.
    """
    rows = list(csv.reader(io.StringIO(text)))
    start = next(
        (
            i
            for i, row in enumerate(rows)
            if _column(_cells(row), "ticker") is not None
            and _column(_cells(row), "sector") is not None
        ),
        None,
    )
    if start is None:
        raise ValueError("no header row with Ticker and Sector columns found")

    header = _cells(rows[start])
    i_ticker = _column(header, "ticker")
    i_sector = _column(header, "sector")
    if i_ticker is None or i_sector is None:
        raise ValueError("no header row with Ticker and Sector columns found")
    i_name = _column(header, "name")
    i_class = _column(header, "asset class")
    i_weight = _column(header, "weight")

    needed = max(i for i in (i_ticker, i_sector, i_name, i_class, i_weight) if i is not None)
    best: dict[str, Holding] = {}
    for row in rows[start + 1 :]:
        if len(row) <= needed:
            continue
        symbol = normalize_symbol(row[i_ticker])
        if symbol is None:
            continue
        if i_class is not None and row[i_class].strip().lower() != "equity":
            continue
        holding = Holding(
            ticker=symbol,
            name=row[i_name].strip() if i_name is not None else "",
            sector=row[i_sector].strip(),
            weight=_parse_number(row[i_weight]) if i_weight is not None else 0.0,
        )
        current = best.get(symbol)
        if current is None or holding.weight > current.weight:
            best[symbol] = holding

    return sorted(best.values(), key=lambda h: (-h.weight, h.ticker))


def build_universe(holdings: Sequence[Holding]) -> tuple[list[UniverseMember], list[str]]:
    """Attach a sector ETF to each holding. Returns (members, tickers dropped for no sector)."""
    members: list[UniverseMember] = []
    unmapped: list[str] = []
    for holding in holdings:
        etf = sector_etf(holding.sector)
        if etf is None:
            unmapped.append(holding.ticker)
            continue
        members.append(
            UniverseMember(
                ticker=holding.ticker,
                name=holding.name,
                sector=holding.sector,
                sector_etf=etf,
                weight=holding.weight,
            )
        )
    return members, unmapped


def sector_map(members: Sequence[UniverseMember]) -> dict[str, str]:
    """Ticker to sector ETF, in the form the residualizer expects."""
    return {member.ticker: member.sector_etf for member in members}
