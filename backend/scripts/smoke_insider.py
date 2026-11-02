"""Manual check: download two quarters of the SEC insider data set and parse them."""

from __future__ import annotations

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.adapters.insider_bulk import BulkFormatError
from whymoved.adapters.insider_source import EdgarInsiderSource
from whymoved.config import JobSettings

SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA", "JPM"]
QUARTERS = [(2025, 4), (2026, 3)]


def main() -> None:
    settings = JobSettings()
    with from_settings(settings) as edgar:
        try:
            tickers = edgar.ticker_map()
            ticker_by_cik = {tickers[s]: s for s in SYMBOLS if s in tickers}
            source = EdgarInsiderSource(edgar, ticker_by_cik)
            for year, quarter in QUARTERS:
                frame = source.trades(year, quarter)
                if frame is None:
                    print(f"{year}q{quarter}: not available")
                    continue
                print(
                    f"{year}q{quarter}: {len(frame)} rows | "
                    f"kinds {frame['kind'].value_counts().to_dict()} | "
                    f"plan {frame['plan'].value_counts().to_dict()}"
                )
                print(frame.head(3).to_string())
        except (EdgarError, BulkFormatError) as exc:
            print(f"FAILED - {exc}")


if __name__ == "__main__":
    main()
