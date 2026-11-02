"""One row per Form 4 filing, sorted into trades that carry a signal and trades that don't."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from whymoved.domain.filings import TransactionKind

Signal = Literal["buy", "sale", "planned_sale", "routine", "other"]

SIGNAL_LABELS: dict[str, str] = {
    "buy": "Open-market purchase",
    "sale": "Open-market sale",
    "planned_sale": "Pre-planned sale (10b5-1)",
    "routine": "Award, exercise or tax withholding",
    "other": "Other",
}

FILING_COLUMNS = [
    "accession",
    "issuer_cik",
    "ticker",
    "filing_date",
    "trade_date",
    "signal",
    "owner_name",
    "owner_role",
    "plan",
    "shares",
    "value",
    "price",
    "n_transactions",
    "lag_days",
]


def signal_of(kinds: set[str], plan: str) -> Signal:
    """The most informative thing a filing contains.

    Open-market purchases are the signal-bearing trades, so a filing with any purchase is a
    buy. Sales are next; a sale made under a 10b5-1 plan was scheduled in advance, so it is
    kept apart from discretionary sales. Everything else is routine or other.
    """
    if TransactionKind.OPEN_MARKET_BUY.value in kinds:
        return "buy"
    if TransactionKind.OPEN_MARKET_SELL.value in kinds:
        return "planned_sale" if plan == "yes" else "sale"
    if TransactionKind.ROUTINE.value in kinds:
        return "routine"
    return "other"


def summarize_filings(trades: pd.DataFrame) -> pd.DataFrame:
    """Collapse transaction rows (TRADE_COLUMNS) into one row per filing.

    Shares, value and the average price cover only the transactions of the filing's signal
    kind (the purchases of a buy, the sales of a sale), so an award in the same filing does
    not distort them. `lag_days` is calendar days from the last trade to the filing.
    """
    if trades.empty:
        return pd.DataFrame({name: [] for name in FILING_COLUMNS})

    rows: list[dict[str, object]] = []
    for accession, group in trades.groupby("accession", sort=False):
        kinds = {str(k) for k in group["kind"]}
        plan = str(group["plan"].iloc[0])
        signal = signal_of(kinds, plan)
        kind = {
            "buy": TransactionKind.OPEN_MARKET_BUY.value,
            "sale": TransactionKind.OPEN_MARKET_SELL.value,
            "planned_sale": TransactionKind.OPEN_MARKET_SELL.value,
        }.get(signal)
        part = group[group["kind"] == kind] if kind else group
        shares = part["shares"].to_numpy(dtype=np.float64)
        prices = part["price"].to_numpy(dtype=np.float64)
        priced = np.isfinite(shares) & np.isfinite(prices)
        value = float(np.sum(shares[priced] * prices[priced])) if priced.any() else np.nan
        priced_shares = float(np.sum(shares[priced]))
        trade_dates = pd.to_datetime(part["trade_date"]).dropna()
        last_trade = trade_dates.max() if not trade_dates.empty else pd.NaT
        filed = pd.Timestamp(group["filing_date"].iloc[0])
        rows.append(
            {
                "accession": str(accession),
                "issuer_cik": int(group["issuer_cik"].iloc[0]),
                "ticker": str(group["ticker"].iloc[0]),
                "filing_date": filed,
                "trade_date": last_trade,
                "signal": signal,
                "owner_name": str(group["owner_name"].iloc[0]),
                "owner_role": str(group["owner_role"].iloc[0]),
                "plan": plan,
                "shares": float(np.nansum(shares)) if len(shares) else np.nan,
                "value": value,
                "price": value / priced_shares if priced_shares > 0 else np.nan,
                "n_transactions": len(group),
                "lag_days": (filed - last_trade).days if not pd.isna(last_trade) else np.nan,
            }
        )
    out = pd.DataFrame(rows, columns=FILING_COLUMNS)
    return out.sort_values(["filing_date", "accession"], ascending=[False, True]).reset_index(
        drop=True
    )
