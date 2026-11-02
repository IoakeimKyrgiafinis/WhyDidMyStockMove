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

    # Vectorised with group-bys: the nightly study summarises every stock's filings, and a
    # Python loop per filing took most of its run time.
    t = trades.reset_index(drop=True)
    key = t["accession"].astype(str).rename("accession")
    kind = t["kind"].astype(str)
    plan = t["plan"].astype(str)
    by = kind.groupby(key, sort=False)

    first = pd.DataFrame(
        {
            "issuer_cik": t["issuer_cik"].groupby(key, sort=False).first(),
            "ticker": t["ticker"].astype(str).groupby(key, sort=False).first(),
            "filing_date": pd.to_datetime(t["filing_date"]).groupby(key, sort=False).first(),
            "owner_name": t["owner_name"].astype(str).groupby(key, sort=False).first(),
            "owner_role": t["owner_role"].astype(str).groupby(key, sort=False).first(),
            "plan": plan.groupby(key, sort=False).first(),
            "n_transactions": by.size(),
        }
    )
    has = {
        k.value: (kind == k.value).groupby(key, sort=False).any()
        for k in (
            TransactionKind.OPEN_MARKET_BUY,
            TransactionKind.OPEN_MARKET_SELL,
            TransactionKind.ROUTINE,
        )
    }
    buy = has[TransactionKind.OPEN_MARKET_BUY.value].to_numpy()
    sell = has[TransactionKind.OPEN_MARKET_SELL.value].to_numpy()
    routine = has[TransactionKind.ROUTINE.value].to_numpy()
    planned = (first["plan"] == "yes").to_numpy()
    first["signal"] = np.select(
        [buy, sell & planned, sell, routine], ["buy", "planned_sale", "sale", "routine"], "other"
    )

    # The rows that count toward shares and value: the filing's signal kind, or all of them
    # for routine and other filings.
    counted_kind = first["signal"].map(
        {
            "buy": TransactionKind.OPEN_MARKET_BUY.value,
            "sale": TransactionKind.OPEN_MARKET_SELL.value,
            "planned_sale": TransactionKind.OPEN_MARKET_SELL.value,
        }
    )
    row_kind = key.map(counted_kind)
    counted = row_kind.isna() | (kind == row_kind)
    shares = t["shares"].astype("float64").where(counted)
    price = t["price"].astype("float64").where(counted)
    priced = shares.notna() & price.notna()
    value = (shares * price).where(priced).groupby(key, sort=False).sum(min_count=1)
    priced_shares = shares.where(priced).groupby(key, sort=False).sum()
    last_trade = pd.to_datetime(t["trade_date"]).where(counted).groupby(key, sort=False).max()

    first["shares"] = shares.groupby(key, sort=False).sum()
    first["value"] = value
    first["price"] = (value / priced_shares).where(priced_shares > 0)
    first["trade_date"] = last_trade
    first["lag_days"] = (first["filing_date"] - last_trade).dt.days
    out = first.reset_index()[FILING_COLUMNS]
    return out.sort_values(["filing_date", "accession"], ascending=[False, True]).reset_index(
        drop=True
    )
