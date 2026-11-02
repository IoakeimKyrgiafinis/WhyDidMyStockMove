from __future__ import annotations

from whymoved.domain.filings import FilingFormatError, FilingNotFound


class EdgarError(Exception):
    """Base class for EDGAR adapter failures."""


class EdgarFormatError(EdgarError, FilingFormatError):
    """A response or filing did not have the expected structure."""


class EdgarNotFound(EdgarError, FilingNotFound):
    """The requested document does not exist (HTTP 404)."""


class EdgarBlocked(EdgarError):
    """The SEC refused the request (HTTP 403): bad User-Agent or rate limit exceeded."""


class EdgarUnavailable(EdgarError):
    """Rate limiting, server errors or network failures persisted through every retry."""
