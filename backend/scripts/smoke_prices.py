"""Manual check against the real Alpaca API: compare IEX and SIP coverage on small caps."""

from __future__ import annotations

from datetime import date

from whymoved.adapters.alpaca import AlpacaDailyBars, AlpacaError, build_client
from whymoved.config import JobSettings

SYMBOLS = ["ZEUS", "AAON", "SPY"]
START, END = date(2026, 10, 1), date(2026, 10, 30)


def main() -> None:
    settings = JobSettings()
    for feed in ("iex", "sip"):
        client = build_client(settings.alpaca_key_id, settings.alpaca_secret_key)
        with AlpacaDailyBars(client, feed=feed) as provider:  # type: ignore[arg-type]
            try:
                bars = provider.daily_bars(SYMBOLS, START, END)
            except AlpacaError as exc:
                print(f"{feed}: FAILED - {exc}")
                continue
        print(f"{feed}: bars per symbol {bars.close.count().to_dict()}")


if __name__ == "__main__":
    main()
