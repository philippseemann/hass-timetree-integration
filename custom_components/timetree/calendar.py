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
        look_ahead = now + timedelta(days=30)

        candidates: list[CalendarEvent] = []
        for ev in (self.coordinator.data or {}).values():
            try:
                if ev.is_recurring:
                    candidates.extend(_expand_recurring(ev, now, look_ahead))
                else:
                    end = _to_datetime(ev, use_end=True)
                    if end > now:
                        candidates.append(_map_event(ev))
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Skipping event %s in state: %s", ev.id, exc_info=True)

        if not candidates:
            return None

        candidates.sort(key=_sort_key)
        return candidates[0]

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events within the requested time range."""
        events: list[CalendarEvent] = []
        all_events = self.coordinator.data or {}

        for ev in all_events.values():
            try:
                # Recurring events must NOT be filtered by the original
                # occurrence dates – they are expanded below and the expansion
                # itself applies the date-range filter.
                if ev.is_recurring:
                    events.extend(
                        _expand_recurring(ev, start_date, end_date)
                    )
                    continue

                ev_start = _to_datetime(ev, use_end=False)
                ev_end = _to_datetime(ev, use_end=True)

                if ev_end <= start_date or ev_start >= end_date:
                    continue

                events.append(_map_event(ev))
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "Failed to process event %s (%s)",
                    ev.id, ev.title, exc_info=True,
                )

        events.sort(key=_sort_key)
        return events

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create a new event on this calendar."""
        _LOGGER.debug("async_create_event called with kwargs: %s", kwargs)
        mutation = _kwargs_to_mutation(kwargs)
        _LOGGER.debug("Created mutation: %s", mutation)
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


def _fix_rrule_until_tz(rule_str: str) -> str:
    """Ensure UNTIL values in RRULE strings are UTC-qualified.

    dateutil requires UNTIL to be in UTC (suffixed with 'Z') when dtstart is
    timezone-aware.  TimeTree sometimes provides bare UNTIL values like
    ``UNTIL=20210429`` which cause a ``ValueError``.  This helper appends
    ``T000000Z`` when no time/zone component is present.
    """
    import re  # noqa: PLC0415

    def _fix_until(m: re.Match) -> str:
        val = m.group(1)
        # Already has a 'Z' or time component → leave as-is
        if "T" in val or val.endswith("Z"):
            return m.group(0)
        # Bare date like "20210429" → make it UTC midnight
        return f"UNTIL={val}T000000Z"

    return re.sub(r"UNTIL=([^;]+)", _fix_until, rule_str)


def _sort_key(ev: CalendarEvent) -> datetime:
    """Normalise a CalendarEvent.start to a tz-aware datetime for sorting.

    All-day events store ``start`` as ``date``; timed events as ``datetime``.
    We convert ``date`` → midnight UTC so both types are comparable.
    """
    if isinstance(ev.start, datetime):
        return ev.start
    return datetime.combine(ev.start, datetime.min.time(), tzinfo=ZoneInfo("UTC"))


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
        from dateutil.parser import parse as dtparse  # noqa: PLC0415
    except ImportError:
        _LOGGER.warning("dateutil not available, skipping RRULE expansion")
        return [_map_event(event)]

    duration_ms = event.end_at - event.start_at
    tz = ZoneInfo(event.start_timezone)
    dt_start = datetime.fromtimestamp(event.start_at / 1000, tz=tz)

    rset = rruleset()
    has_rrule = False
    for rule_str in event.recurrences:
        if rule_str.startswith("RRULE:"):
            try:
                fixed = _fix_rrule_until_tz(rule_str)
                rule = rrulestr(fixed, dtstart=dt_start)
                rset.rrule(rule)
                has_rrule = True
            except (ValueError, TypeError):
                _LOGGER.debug("Failed to parse RRULE: %s", rule_str)
                return [_map_event(event)]
        elif rule_str.startswith("EXDATE:"):
            # EXDATE contains individual excluded timestamps, not a rule.
            # Format: "EXDATE:20250402T000000Z" or comma-separated list.
            raw = rule_str[len("EXDATE:"):]
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    exdt = dtparse(part)
                    if exdt.tzinfo is None:
                        exdt = exdt.replace(tzinfo=tz)
                    rset.exdate(exdt)
                except (ValueError, TypeError):
                    _LOGGER.debug("Failed to parse EXDATE value: %s", part)

    if not has_rrule:
        # No parseable rules – return single mapped event if in range
        return [_map_event(event)]

    occurrences = rset.between(range_start, range_end, inc=True)
    if not occurrences:
        return []

    results: list[CalendarEvent] = []
    for occ_start in occurrences:
        occ_start_aware = occ_start if occ_start.tzinfo else occ_start.replace(tzinfo=tz)
        occ_end_aware = occ_start_aware + timedelta(milliseconds=duration_ms)

        if event.all_day:
            occ_start_date = occ_start_aware.date()
            occ_end_date = occ_end_aware.date()
            # HA expects exclusive end date; ensure at least 1-day span.
            if occ_end_date <= occ_start_date:
                occ_end_date = occ_start_date + timedelta(days=1)
            results.append(
                CalendarEvent(
                    summary=event.title,
                    start=occ_start_date,
                    end=occ_end_date,
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
    """Convert HA calendar service call data to an EventMutation.

    HA passes different keys depending on the source:
    - UI/service call: start_date_time / end_date_time (timed)
                       start_date / end_date (all-day)
    - Automation:      dtstart / dtend  OR  start / end
    """
    # Determine event type based on which keys are present.
    # HA uses start_date/end_date for all-day, start_date_time/end_date_time for timed.
    if data.get("start_date_time") or (data.get("dtstart") and isinstance(data.get("dtstart"), datetime)):
        # Timed event
        dtstart = data.get("start_date_time") or data.get("dtstart") or data.get("start")
        dtend = data.get("end_date_time") or data.get("dtend") or data.get("end")
        all_day = False
    elif data.get("start_date") or (data.get("dtstart") and isinstance(data.get("dtstart"), date) and not isinstance(data.get("dtstart"), datetime)):
        # All-day event
        dtstart = data.get("start_date") or data.get("dtstart") or data.get("start")
        dtend = data.get("end_date") or data.get("dtend") or data.get("end")
        all_day = True
    else:
        # Fallback: try generic keys
        dtstart = data.get("dtstart") or data.get("start")
        dtend = data.get("dtend") or data.get("end")
        all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)

    # Parse strings if needed (HA may pass strings from service calls)
    if isinstance(dtstart, str):
        from dateutil.parser import parse as dtparse  # noqa: PLC0415
        dtstart = dtparse(dtstart)
    if isinstance(dtend, str):
        from dateutil.parser import parse as dtparse  # noqa: PLC0415
        dtend = dtparse(dtend)

    if all_day:
        # Ensure we have date objects
        if isinstance(dtstart, datetime):
            dtstart = dtstart.date()
        if isinstance(dtend, datetime):
            dtend = dtend.date()
        # HA uses exclusive end dates; TimeTree uses inclusive.
        # Convert: HA end_date (exclusive) → TimeTree end_at (inclusive)
        # e.g. HA start=Feb12, end=Feb13 (1 day) → TT start=Feb12, end=Feb12
        tt_end = dtend - timedelta(days=1)
        if tt_end < dtstart:
            tt_end = dtstart
        tz_name = "UTC"
        start_ms = int(
            datetime.combine(dtstart, datetime.min.time(), tzinfo=ZoneInfo(tz_name)).timestamp()
            * 1000
        )
        end_ms = int(
            datetime.combine(tt_end, datetime.min.time(), tzinfo=ZoneInfo(tz_name)).timestamp()
            * 1000
        )
    else:
        if not isinstance(dtstart, datetime) or not isinstance(dtend, datetime):
            msg = f"Expected datetime for timed events, got {type(dtstart).__name__}/{type(dtend).__name__}"
            raise ValueError(msg)
        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=ZoneInfo("UTC"))
        if dtend.tzinfo is None:
            dtend = dtend.replace(tzinfo=ZoneInfo("UTC"))
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
