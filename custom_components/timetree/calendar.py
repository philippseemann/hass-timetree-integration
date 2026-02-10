"""Calendar entity for the TimeTree integration."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .timetree_api import Event, EventCategory, EventMutation

from .const import DOMAIN
from .coordinator import TimeTreeCalendarCoordinator
from .models import TimeTreeRuntimeData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TimeTree calendar entities from a config entry."""
    runtime_data: TimeTreeRuntimeData = entry.runtime_data
    async_add_entities(
        TimeTreeCalendarEntity(coordinator)
        for coordinator in runtime_data.coordinators
    )


class TimeTreeCalendarEntity(
    CoordinatorEntity[TimeTreeCalendarCoordinator], CalendarEntity
):
    """A calendar entity backed by a single TimeTree calendar."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT
        | CalendarEntityFeature.DELETE_EVENT
        | CalendarEntityFeature.UPDATE_EVENT
    )

    def __init__(self, coordinator: TimeTreeCalendarCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.calendar_id}"
        self._attr_name = coordinator.calendar_name

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event or the current active event."""
        now = datetime.now(tz=ZoneInfo("UTC"))
        events = self._get_sorted_events()

        for ev in events:
            start = _to_datetime(ev, use_end=False)
            end = _to_datetime(ev, use_end=True)
            if end > now:
                return _map_event(ev)

        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events within the requested time range."""
        events: list[CalendarEvent] = []

        for ev in (self.coordinator.data or {}).values():
            ev_start = _to_datetime(ev, use_end=False)
            ev_end = _to_datetime(ev, use_end=True)

            if ev_end <= start_date or ev_start >= end_date:
                continue

            # Expand recurring events
            if ev.is_recurring:
                events.extend(
                    _expand_recurring(ev, start_date, end_date)
                )
            else:
                events.append(_map_event(ev))

        events.sort(key=lambda e: e.start)
        return events

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create a new event on this calendar."""
        mutation = _kwargs_to_mutation(kwargs)
        await self.coordinator._client.async_create_event(
            self.coordinator.calendar_id, mutation
        )
        await self.coordinator.async_request_refresh()

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Update an existing event."""
        mutation = _kwargs_to_mutation(event)
        await self.coordinator._client.async_update_event(
            self.coordinator.calendar_id, uid, mutation
        )
        await self.coordinator.async_request_refresh()

    async def async_delete_event(
        self,
        uid: str,
        **kwargs: Any,
    ) -> None:
        """Delete an event from this calendar."""
        await self.coordinator._client.async_delete_event(
            self.coordinator.calendar_id, uid
        )
        await self.coordinator.async_request_refresh()

    def _get_sorted_events(self) -> list[Event]:
        """Return all events sorted by start time."""
        events = list((self.coordinator.data or {}).values())
        events.sort(key=lambda e: e.start_at)
        return events


# --------------------------------------------------------------------------- #
#  Mapping helpers
# --------------------------------------------------------------------------- #


def _to_datetime(event: Event, *, use_end: bool) -> datetime:
    """Convert a TimeTree event timestamp to a tz-aware datetime."""
    ts_ms = event.end_at if use_end else event.start_at
    tz_name = event.end_timezone if use_end else event.start_timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo(tz_name))


def _map_event(event: Event) -> CalendarEvent:
    """Map a TimeTree Event to a HA CalendarEvent."""
    if event.all_day:
        start = _ts_to_date(event.start_at, event.start_timezone)
        end = _ts_to_date(event.end_at, event.end_timezone)
        # HA expects end date to be exclusive; TimeTree stores inclusive end
        # Only add a day if start == end (single-day all-day event)
        if end <= start:
            end = start + timedelta(days=1)
        return CalendarEvent(
            summary=event.title,
            start=start,
            end=end,
            description=event.note,
            location=event.location,
            uid=event.id,
        )

    return CalendarEvent(
        summary=event.title,
        start=_to_datetime(event, use_end=False),
        end=_to_datetime(event, use_end=True),
        description=event.note,
        location=event.location,
        uid=event.id,
    )


def _ts_to_date(ts_ms: int, tz_name: str) -> date:
    """Convert a Unix-ms timestamp to a local date."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(tz_name))
    return dt.date()


def _expand_recurring(
    event: Event,
    range_start: datetime,
    range_end: datetime,
) -> list[CalendarEvent]:
    """Expand a recurring event into individual occurrences within a range.

    Uses dateutil.rrule for RRULE parsing. Falls back to a single occurrence
    on parse errors.
    """
    try:
        from dateutil.rrule import rruleset, rrulestr  # noqa: PLC0415
    except ImportError:
        _LOGGER.warning("dateutil not available, skipping RRULE expansion")
        return [_map_event(event)]

    duration_ms = event.end_at - event.start_at
    tz = ZoneInfo(event.start_timezone)
    dt_start = datetime.fromtimestamp(event.start_at / 1000, tz=tz)

    rset = rruleset()
    for rule_str in event.recurrences:
        if rule_str.startswith("RRULE:"):
            try:
                rule = rrulestr(rule_str, dtstart=dt_start)
                rset.rrule(rule)
            except (ValueError, TypeError):
                _LOGGER.debug("Failed to parse RRULE: %s", rule_str)
                return [_map_event(event)]
        elif rule_str.startswith("EXDATE:"):
            # Handle exclusion dates if present
            try:
                ex_rule = rrulestr(rule_str, dtstart=dt_start)
                rset.exrule(ex_rule)
            except (ValueError, TypeError):
                pass

    occurrences = rset.between(range_start, range_end, inc=True)
    if not occurrences:
        return []

    results: list[CalendarEvent] = []
    for occ_start in occurrences:
        occ_start_aware = occ_start if occ_start.tzinfo else occ_start.replace(tzinfo=tz)
        occ_end_aware = occ_start_aware + timedelta(milliseconds=duration_ms)

        if event.all_day:
            results.append(
                CalendarEvent(
                    summary=event.title,
                    start=occ_start_aware.date(),
                    end=occ_end_aware.date(),
                    description=event.note,
                    location=event.location,
                    uid=f"{event.id}_{int(occ_start_aware.timestamp() * 1000)}",
                )
            )
        else:
            results.append(
                CalendarEvent(
                    summary=event.title,
                    start=occ_start_aware,
                    end=occ_end_aware,
                    description=event.note,
                    location=event.location,
                    uid=f"{event.id}_{int(occ_start_aware.timestamp() * 1000)}",
                )
            )

    return results


def _kwargs_to_mutation(data: dict[str, Any]) -> EventMutation:
    """Convert HA calendar service call data to an EventMutation."""
    dtstart = data.get("dtstart") or data.get("start")
    dtend = data.get("dtend") or data.get("end")

    all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)

    if all_day:
        tz_name = "UTC"
        start_ms = int(
            datetime.combine(dtstart, datetime.min.time(), tzinfo=ZoneInfo(tz_name)).timestamp()
            * 1000
        )
        end_ms = int(
            datetime.combine(dtend, datetime.min.time(), tzinfo=ZoneInfo(tz_name)).timestamp()
            * 1000
        )
    else:
        if not isinstance(dtstart, datetime) or not isinstance(dtend, datetime):
            msg = "Expected datetime for timed events"
            raise ValueError(msg)
        tz_name = str(dtstart.tzinfo) if dtstart.tzinfo else "UTC"
        start_ms = int(dtstart.timestamp() * 1000)
        end_ms = int(dtend.timestamp() * 1000)

    return EventMutation(
        title=data.get("summary", ""),
        all_day=all_day,
        start_at=start_ms,
        end_at=end_ms,
        start_timezone=tz_name,
        end_timezone=tz_name,
        note=data.get("description"),
        location=data.get("location"),
    )
