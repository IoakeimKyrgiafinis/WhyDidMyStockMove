"""Manual check of Alpaca's news endpoint with the real API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from whymoved.adapters.alpaca import AlpacaError, build_client
from whymoved.adapters.alpaca_news import AlpacaNews
from whymoved.config import JobSettings


def main() -> None:
    settings = JobSettings()
    end = datetime.now(UTC).replace(tzinfo=None)
    start = end - timedelta(days=3)
    client = build_client(settings.alpaca_key_id, settings.alpaca_secret_key)
    with AlpacaNews(client) as provider:
        try:
            items = provider.news(["NVDA", "AAPL"], start, end)
        except AlpacaError as exc:
            print(f"FAILED - {exc}")
            return
    print(f"{len(items)} articles in the last 3 days")
    for item in items[-5:]:
        print(item.created_at, item.source, item.symbols[:4], item.headline[:90])


if __name__ == "__main__":
    main()
