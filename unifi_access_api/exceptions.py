"""Exceptions for the UniFi Access API client."""

from __future__ import annotations


class UnifiAccessError(Exception):
    """Base exception for UniFi Access API errors."""


class ApiAuthError(UnifiAccessError):
    """Authentication error - invalid or expired API token."""

    def __init__(self, message: str = "Invalid or expired API token") -> None:
        super().__init__(message)


class ApiError(UnifiAccessError):
    """General API error - unexpected response from the server."""

    def __init__(self, message: str = "", status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message or f"API error (status {status_code})")


class ApiForbiddenError(ApiError):
    """Forbidden - HTTP 403."""

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message, status_code=403)


class ApiNotFoundError(ApiError):
    """Resource not found - HTTP 404."""

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message, status_code=404)


class ApiRateLimitError(ApiError):
    """Rate limit exceeded - HTTP 429."""

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message, status_code=429)


class ApiConnectionError(UnifiAccessError):
    """Connection error - cannot reach the UniFi Access server."""


class ApiSSLError(UnifiAccessError):
    """SSL certificate validation error."""
