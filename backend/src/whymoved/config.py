from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the API process. Deliberately lenient so the API can start without keys."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="WHYMOVED_", extra="ignore")

    # Comma-separated list of allowed browser origins (not a secret).
    cors_origins: str = "http://localhost:5173"

    # Where the nightly snapshot lives. With a snapshot URL (the GitHub release download
    # prefix), the API downloads the files there at startup and refreshes them periodically.
    data_dir: Path = Path("data")
    snapshot_url: str = ""
    snapshot_refresh_hours: float = 6.0

    # Optional: intraday headline reactions fetch 5-minute bars on demand. Without keys the
    # rest of the site works and the reaction chips say they are unavailable.
    alpaca_key_id: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    alpaca_feed: Literal["iex", "sip"] = "iex"
    # Intraday reactions spend the Alpaca quota: allow a burst, then a steady rate per visitor.
    reaction_burst: int = 10
    reaction_requests_per_minute: float = 20.0

    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        """Allowed origins, normalised the way browsers send them: no trailing slash, lower
        case, and https:// assumed when the scheme is left out. Pasting a URL copied from the
        address bar ("https://site.vercel.app/") then still works."""
        origins: list[str] = []
        for raw in self.cors_origins.split(","):
            origin = raw.strip().rstrip("/").lower()
            if not origin:
                continue
            if "://" not in origin:
                origin = f"https://{origin}"
            origins.append(origin)
        return origins


class JobSettings(BaseSettings):
    """Settings for the nightly data job. Required secrets have no defaults, so a missing
    one stops the job at startup with a clear error instead of failing halfway through."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="WHYMOVED_", extra="ignore")

    alpaca_key_id: SecretStr
    alpaca_secret_key: SecretStr
    sec_user_agent: str
    alpaca_feed: Literal["iex", "sip"] = "iex"

    @field_validator("sec_user_agent")
    @classmethod
    def _needs_contact(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("must include a contact email, e.g. 'Your Name you@example.com'")
        return value.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
