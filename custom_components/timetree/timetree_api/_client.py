"""TimeTree API client."""

from __future__ import annotations

import uuid
from typing import Any

import aiohttp

from ._auth import TimeTreeAuth
from ._serialization import camelize, decamelize
from ._throttle import RequestThrottle
from .const import (
    CALENDARS_ENDPOINT,
    CALENDAR_EVENT_DETAIL_ENDPOINT,
    CALENDAR_EVENT_ENDPOINT,
    CALENDAR_EVENTS_ENDPOINT,
    CALENDAR_LABELS_ENDPOINT,
    DEFAULT_THROTTLE_SECONDS,
    USER_ENDPOINT,
)
from .exceptions import (
    ApiConnectionError,
    ApiResponseError,
    AuthenticationError,
    RateLimitError,
)
from .models import Calendar, Event, EventMutation, Label, User


class TimeTreeApiClient:
    """Async client for the TimeTree calendar API.

    Usage::

        async with aiohttp.ClientSession() as session:
            client = TimeTreeApiClient(session)
            await client.authenticate("user@example.com", "password")
            calendars = await client.async_get_calendars()

    If no session is provided, the client creates and manages its own.
    The caller is responsible for calling ``async_close()`` when done
    (or use the client as an async context manager).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        request_interval: float = DEFAULT_THROTTLE_SECONDS,
    ) -> None:
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession()
        self._auth = TimeTreeAuth(self._session)
        self._throttle = RequestThrottle(min_interval=request_interval)

    async def __aenter__(self) -> TimeTreeApiClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.async_close()

    @property
    def authenticated(self) -> bool:
        """Whether the client has an active authenticated session."""
        return self._auth.is_authenticated

    # ------------------------------------------------------------------ #
    #  Authentication
    # ------------------------------------------------------------------ #

    async def authenticate(self, email: str, password: str) -> None:
        """Authenticate with email and password.

        Performs the full login flow: fetches CSRF token from the signin page,
        then submits credentials. The session cookie is stored in the
        session's cookie jar for subsequent requests.

        Raises:
            AuthenticationError: On invalid credentials or CSRF failure.
            ApiConnectionError: If the server is unreachable.
        """
        await self._auth.authenticate(email, password)

    async def async_validate_session(self) -> bool:
        """Check whether the current session is still valid.

        Raises:
            AuthenticationError: If the session has expired.
        """
        return await self._auth.validate_session()

    async def async_close(self) -> None:
        """Close the HTTP session if the client owns it."""
        if self._owns_session:
            await self._session.close()

    # ------------------------------------------------------------------ #
    #  Calendars
    # ------------------------------------------------------------------ #

    async def async_get_calendars(self) -> list[Calendar]:
        """Fetch all calendars the authenticated user has access to."""
        data = await self._request("GET", CALENDARS_ENDPOINT)
        raw = data if isinstance(data, list) else data.get("calendars", [])
        return [Calendar.from_api_response(c) for c in raw]

    async def async_get_labels(self, calendar_id: str) -> list[Label]:
        """Fetch labels (color tags) for a calendar."""
        url = CALENDAR_LABELS_ENDPOINT.format(calendar_id=calendar_id)
        data = await self._request("GET", url)
        raw = data if isinstance(data, list) else data.get("labels", [])
        return [Label.from_api_response(lb) for lb in raw]

    # ------------------------------------------------------------------ #
    #  Events
    # ------------------------------------------------------------------ #

    async def async_get_events(
        self,
        calendar_id: str,
        *,
        since: int | None = None,
    ) -> list[Event]:
        """Fetch events from a calendar.

        Args:
            calendar_id: The calendar's internal numeric ID.
            since: Unix timestamp in milliseconds for delta sync.
                Only events modified after this time are returned.

        Returns:
            List of events (may include soft-deleted ones with ``deleted_at`` set).
        """
        url = CALENDAR_EVENTS_ENDPOINT.format(calendar_id=calendar_id)
        params: dict[str, str] = {}
        if since is not None:
            params["since"] = str(since)
        data = await self._request("GET", url, params=params)
        raw = data if isinstance(data, list) else data.get("events", [])
        return [Event.from_api_response(e, calendar_id=calendar_id) for e in raw]

    async def async_create_event(
        self,
        calendar_id: str,
        event: EventMutation,
    ) -> Event:
        """Create a new event on a calendar."""
        url = CALENDAR_EVENT_ENDPOINT.format(calendar_id=calendar_id)
        body = event.to_api_dict()
        body["id"] = uuid.uuid4().hex
        data = await self._request("POST", url, json_body=body)
        event_data = data.get("event", data) if isinstance(data, dict) else data
        return Event.from_api_response(event_data, calendar_id=calendar_id)

    async def async_update_event(
        self,
        calendar_id: str,
        event_id: str,
        event: EventMutation,
    ) -> Event:
        """Update an existing event."""
        url = CALENDAR_EVENT_DETAIL_ENDPOINT.format(
            calendar_id=calendar_id, event_id=event_id
        )
        body = event.to_api_dict()
        data = await self._request("PUT", url, json_body=body)
        event_data = data.get("event", data) if isinstance(data, dict) else data
        return Event.from_api_response(event_data, calendar_id=calendar_id)

    async def async_delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event from a calendar."""
        url = CALENDAR_EVENT_DETAIL_ENDPOINT.format(
            calendar_id=calendar_id, event_id=event_id
        )
        await self._request("DELETE", url)

    # ------------------------------------------------------------------ #
    #  User
    # ------------------------------------------------------------------ #

    async def async_get_user(self) -> User:
        """Fetch the current authenticated user's profile."""
        data = await self._request("GET", USER_ENDPOINT)
        # Unwrap if response is nested under a "user" key
        user_data = data.get("user", data) if isinstance(data, dict) else data
        return User.from_api_response(user_data)

    # ------------------------------------------------------------------ #
    #  Internal HTTP layer
    # ------------------------------------------------------------------ #

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an API request with throttling, auth, and serialization.

        All outgoing JSON bodies are camelized; all incoming JSON responses
        are decamelized. The 100ms throttle is applied before each request.

        Raises:
            AuthenticationError: On 401/403 responses.
            RateLimitError: On 429 responses.
            ApiResponseError: On other non-2xx responses.
            ApiConnectionError: On network errors.
        """
        await self._throttle.acquire()

        mutating = method in ("POST", "PUT", "DELETE")
        headers = self._auth.get_headers(mutating=mutating)

        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = camelize(json_body)

        try:
            async with self._session.request(method, url, **kwargs) as resp:
                if resp.status in (401, 403):
                    self._auth.mark_unauthenticated()
                    raise AuthenticationError(f"Authentication failed: HTTP {resp.status}")

                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    raise RateLimitError(
                        retry_after=float(retry_after) if retry_after else None,
                    )

                if resp.status == 204:
                    return None

                if resp.status >= 400:
                    body = await resp.text()
                    raise ApiResponseError(
                        f"API error: HTTP {resp.status} - {body}",
                        status_code=resp.status,
                    )

                data = await resp.json()
                return decamelize(data)

        except aiohttp.ClientError as err:
            raise ApiConnectionError(f"Connection error: {err}") from err
