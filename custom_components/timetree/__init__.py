"""The TimeTree integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from .timetree_api import (
    ApiConnectionError,
    AuthenticationError,
    TimeTreeApiClient,
)

from .const import CONF_EMAIL, CONF_PASSWORD
from .coordinator import TimeTreeCalendarCoordinator
from .models import TimeTreeRuntimeData

PLATFORMS: list[Platform] = [Platform.CALENDAR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TimeTree from a config entry."""
    client = TimeTreeApiClient()

    try:
        await client.authenticate(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except AuthenticationError as err:
        await client.async_close()
        raise ConfigEntryAuthFailed("Invalid credentials") from err
    except ApiConnectionError as err:
        await client.async_close()
        raise ConfigEntryNotReady("Cannot connect to TimeTree") from err

    calendars = await client.async_get_calendars()

    coordinators: list[TimeTreeCalendarCoordinator] = []
    for cal in calendars:
        coordinator = TimeTreeCalendarCoordinator(
            hass, client, entry, calendar_id=cal.id, calendar_name=cal.name
        )
        await coordinator.async_config_entry_first_refresh()
        coordinators.append(coordinator)

    entry.runtime_data = TimeTreeRuntimeData(
        client=client, coordinators=coordinators
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a TimeTree config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime_data: TimeTreeRuntimeData = entry.runtime_data
        await runtime_data.client.async_close()
    return unload_ok
