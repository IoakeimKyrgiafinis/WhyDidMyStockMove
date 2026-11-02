"""Manual check against the real SEC EDGAR service."""

from __future__ import annotations

from datetime import date, timedelta

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.config import JobSettings

TICKER = "NVDA"
YEARS = 6


def main() -> None:
    settings = JobSettings()
    with from_settings(settings) as edgar:
        try:
            tickers = edgar.ticker_map()
            print(f"tickers known: {len(tickers)}")
            cik = tickers[TICKER]

            since = date.today() - timedelta(days=YEARS * 365)
            refs = edgar.recent_filings(cik, {"4", "4/A", "8-K"}, since)
            forms = [ref.form for ref in refs]
            print(
                f"{TICKER} (CIK {cik}) since {since}: {forms.count('4')} Form 4, "
                f"{forms.count('4/A')} Form 4/A, {forms.count('8-K')} 8-K"
            )
            if refs:
                print(f"oldest filing: {refs[0].filing_date}, newest: {refs[-1].filing_date}")

            latest = next((ref for ref in reversed(refs) if ref.form == "4"), None)
            if latest is None:
                print("no Form 4 in the window")
                return
            filing = edgar.insider_filing(latest)
            print(
                f"latest Form 4: filed {latest.filing_date}, schema {filing.schema_version}, "
                f"under plan: {filing.under_plan}"
            )
            for owner in filing.owners:
                print(f"  owner: {owner.name} ({owner.role})")
            for tx in filing.transactions:
                print(
                    f"  {tx.kind.value:17} code {tx.code or '-':2} {tx.shares} shares "
                    f"at {tx.price} on {tx.transaction_date}"
                )
        except EdgarError as exc:
            print(f"FAILED - {exc}")


if __name__ == "__main__":
    main()
