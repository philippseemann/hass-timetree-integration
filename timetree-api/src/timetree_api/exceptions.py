"""Exception hierarchy for the TimeTree API client."""

from __future__ import annotations


class TimeTreeError(Exception):
    """Base exception for all TimeTree API errors."""


class AuthenticationError(TimeTreeError):
    """Authentication failed or session expired."""


class ApiConnectionError(TimeTreeError):
    """API is unreachable (network error, DNS, timeout)."""


class ApiResponseError(TimeTreeError):
    """API returned an unexpected error response.

    Attributes:
        status_code: HTTP status code, if available.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(ApiResponseError):
    """API returned 429 Too Many Requests.

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the server.
    """

    def __init__(
        self,
        message: str = "Rate limited",
        *,
        status_code: int = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after
