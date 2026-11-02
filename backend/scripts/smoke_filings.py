"""Manual check: collect two weeks of Form 4 and a year of 8-Ks for one company."""

from __future__ import annotations

from datetime import date, timedelta

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.config import JobSettings
from whymoved.domain.corporate_events import item_label
from whymoved.services.filings import collect_filings

TICKER = "NVDA"


def main() -> None:
    settings = JobSettings()
    today = date.today()
    with from_settings(settings) as edgar:
        try:
            cik = edgar.ticker_map()[TICKER]
            result = collect_filings(
                edgar,
                {TICKER: cik},
                set(),
                form4_since=today - timedelta(days=14),
                events_since=today - timedelta(days=365),
            )
        except EdgarError as exc:
            print(f"FAILED - {exc}")
            return

    print(result.report)
    print("\n8-K events (latest 5):")
    for row in result.events.tail(5).to_dict("records"):
        labels = [item_label(item) for item in str(row["items"]).split(",") if item]
        print(f"  {row['filing_date'].date()}  {row['items']!r}  {labels}")
    print("\nForm 4 trades (first 10):")
    print(result.trades.head(10).to_string())


if __name__ == "__main__":
    main()
