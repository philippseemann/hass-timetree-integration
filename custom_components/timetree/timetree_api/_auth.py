"""Authentication handler for the TimeTree API."""

from __future__ import annotations

import re
import uuid

import aiohttp

from .const import (
    AUTH_SIGNIN_ENDPOINT,
    AUTH_VALIDATE_ENDPOINT,
    HEADER_CSRF,
    HEADER_TIMETREE_APP,
    SIGNIN_URL,
    TIMETREE_APP_ID,
)
from .exceptions import ApiConnectionError, AuthenticationError

_CSRF_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"')


class TimeTreeAuth:
    """Manages TimeTree session authentication and CSRF tokens.

    Lifecycle:
        1. Call ``authenticate(email, password)`` to establish a session.
        2. The session cookie (``_session_id``) is stored in the cookie jar.
        3. Use ``get_headers()`` to obtain headers for API calls.
        4. On 401/403, the client should call ``authenticate()`` again.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._csrf_token: str | None = None
        self._device_uuid: str = uuid.uuid4().hex
        self._authenticated: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    async def authenticate(self, email: str, password: str) -> None:
        """Perform the full login flow.

        1. GET ``/signin`` to extract CSRF token and obtain a session cookie.
        2. PUT ``/api/v1/auth/email/signin`` with credentials.
        3. Refresh the CSRF token from an authenticated page.

        Step 3 is needed because Rails rotates the CSRF token after login.
        The token obtained from ``/signin`` is only valid for the login
        request itself; subsequent mutating API calls require a fresh token.

        Raises:
            AuthenticationError: On invalid credentials or missing CSRF token.
            ApiConnectionError: If the server is unreachable.
        """
        await self._fetch_csrf_token()
        await self._submit_credentials(email, password)
        # Rails rotates the CSRF token after login; fetch a fresh one.
        await self._fetch_csrf_token()
        self._authenticated = True

    async def validate_session(self) -> bool:
        """Check whether the current session cookie is still valid.

        Returns:
            True if the session is valid.

        Raises:
            AuthenticationError: If the session is expired or invalid.
        """
        try:
            async with self._session.get(
                AUTH_VALIDATE_ENDPOINT,
                headers=self._build_headers(),
            ) as resp:
                if resp.status == 200:
                    self._authenticated = True
                    return True
                self._authenticated = False
                raise AuthenticationError(f"Session validation failed: HTTP {resp.status}")
        except aiohttp.ClientError as err:
            raise ApiConnectionError(f"Connection error during session validation: {err}") from err

    def get_headers(self, *, mutating: bool = False) -> dict[str, str]:
        """Build headers for an API request.

        Args:
            mutating: Include CSRF token (required for POST/PUT/DELETE).

        Raises:
            AuthenticationError: If not yet authenticated.
        """
        if not self._authenticated or self._csrf_token is None:
            raise AuthenticationError("Not authenticated. Call authenticate() first.")
        headers = self._build_headers()
        if mutating:
            headers[HEADER_CSRF] = self._csrf_token
        return headers

    def mark_unauthenticated(self) -> None:
        """Mark the session as no longer authenticated (e.g. after a 401)."""
        self._authenticated = False

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            HEADER_TIMETREE_APP: TIMETREE_APP_ID,
        }
        if self._csrf_token is not None:
            headers[HEADER_CSRF] = self._csrf_token
        return headers

    async def _fetch_csrf_token(self) -> None:
        """GET /signin and extract the CSRF token from the HTML meta tag."""
        try:
            async with self._session.get(SIGNIN_URL) as resp:
                if resp.status != 200:
                    raise AuthenticationError(
                        f"Failed to load signin page: HTTP {resp.status}"
                    )
                html = await resp.text()
        except aiohttp.ClientError as err:
            raise ApiConnectionError(f"Connection error fetching signin page: {err}") from err

        match = _CSRF_RE.search(html)
        if not match:
            raise AuthenticationError("Could not extract CSRF token from signin page")
        self._csrf_token = match.group(1)

    async def _submit_credentials(self, email: str, password: str) -> None:
        """PUT credentials to the auth endpoint."""
        payload = {
            "uid": email,
            "password": password,
            "uuid": self._device_uuid,
        }
        try:
            async with self._session.put(
                AUTH_SIGNIN_ENDPOINT,
                json=payload,
                headers=self._build_headers(),
            ) as resp:
                if resp.status == 200:
                    return
                if resp.status in (401, 403):
                    raise AuthenticationError("Invalid email or password")
                body = await resp.text()
                raise AuthenticationError(f"Login failed: HTTP {resp.status} - {body}")
        except aiohttp.ClientError as err:
            raise ApiConnectionError(f"Connection error during login: {err}") from err
