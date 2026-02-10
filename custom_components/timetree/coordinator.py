"""DataUpdateCoordinator for a single TimeTree calendar."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .timetree_api import (
    ApiConnectionError,
    ApiResponseError,
    AuthenticationError,
    Event,
    TimeTreeApiClient,
)

from .const import CONF_EMAIL, CONF_PASSWORD, DEFAULT_UPDATE_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TimeTreeCalendarCoordinator(DataUpdateCoordinator[dict[str, Event]]):
    """Coordinator that manages delta-sync for a single TimeTree calendar.

    Stores a dict of event_id → Event. On each update, merges new/modified
    events into the store and removes soft-deleted ones.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: Any,
        client: TimeTreeApiClient,
        config_entry: ConfigEntry,
        calendar_id: str,
        calendar_name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{calendar_name}",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL_SECONDS),
        )
        self._client = client
        self._calendar_id = calendar_id
        self._calendar_name = calendar_name
        self._last_sync_ms: int | None = None
        self._events: dict[str, Event] = {}

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    @property
    def calendar_name(self) -> str:
        return self._calendar_name

    async def _async_update_data(self) -> dict[str, Event]:
        """Fetch events via delta sync and merge into the local store."""
        try:
            events = await self._client.async_get_events(
                self._calendar_id, since=self._last_sync_ms
            )
        except AuthenticationError:
            if await self._try_reauth():
                events = await self._client.async_get_events(
                    self._calendar_id, since=self._last_sync_ms
                )
            else:
                raise ConfigEntryAuthFailed(
                    "Session expired and re-authentication failed"
                )
        except ApiConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except ApiResponseError as err:
            raise UpdateFailed(f"API error: {err}") from err

        # Merge into store
        latest_updated: int = self._last_sync_ms or 0
        for event in events:
            if event.is_deleted:
                self._events.pop(event.id, None)
            else:
                self._events[event.id] = event
            if event.updated_at and event.updated_at > latest_updated:
                latest_updated = event.updated_at

        if latest_updated > (self._last_sync_ms or 0):
            self._last_sync_ms = latest_updated

        return self._events

    async def _try_reauth(self) -> bool:
        """Attempt to re-authenticate with stored credentials.

        Returns True if re-auth succeeded, False otherwise.
        """
        try:
            email = self.config_entry.data[CONF_EMAIL]
            password = self.config_entry.data[CONF_PASSWORD]
            await self._client.authenticate(email, password)
        except (AuthenticationError, ApiConnectionError):
            return False
        else:
            return True
