"""Tests for calendar.py helper functions.

These tests cover the pure mapping/expansion functions without needing
a running Home Assistant instance. They protect against the real-world
bugs we encountered:
- TypeError when sorting mixed date/datetime events
- Recurring events filtered out before RRULE expansion
- RRULE UNTIL values without timezone crashing dateutil
- EXDATE parsing failures
- All-day event end-date off-by-one errors
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homeassistant.components.calendar import CalendarEvent  # noqa: E402 (mocked in conftest)

from custom_components.timetree.timetree_api.models import Event, EventCategory

# We import the private helpers directly – they are the core logic under test.
from custom_components.timetree.calendar import (
    _expand_recurring,
    _fix_rrule_until_tz,
    _kwargs_to_mutation,
    _map_event,
    _sort_key,
    _to_datetime,
    _ts_to_date,
)

# ---------------------------------------------------------------------------
#  Timezone helpers
# ---------------------------------------------------------------------------
UTC = ZoneInfo("UTC")
BERLIN = ZoneInfo("Europe/Berlin")


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0, tz: ZoneInfo = UTC) -> int:
    """Build a Unix-millisecond timestamp from date parts."""
    return int(datetime(year, month, day, hour, minute, tzinfo=tz).timestamp() * 1000)


# ---------------------------------------------------------------------------
#  Event factory helpers
# ---------------------------------------------------------------------------

def _make_event(
    *,
    title: str = "Test Event",
    all_day: bool = False,
    start_at: int | None = None,
    end_at: int | None = None,
    start_timezone: str = "Europe/Berlin",
    end_timezone: str = "Europe/Berlin",
    recurrences: tuple[str, ...] = (),
    note: str | None = None,
    location: str | None = None,
    event_id: str = "evt_1",
) -> Event:
    if start_at is None:
        start_at = _ts(2026, 2, 10, 18, 30, BERLIN)
    if end_at is None:
        end_at = _ts(2026, 2, 10, 21, 30, BERLIN)
    return Event(
        id=event_id,
        calendar_id="cal_1",
        title=title,
        all_day=all_day,
        start_at=start_at,
        end_at=end_at,
        start_timezone=start_timezone,
        end_timezone=end_timezone,
        recurrences=recurrences,
        note=note,
        location=location,
    )


def _make_allday_event(
    *,
    title: str = "All Day",
    start_date: tuple[int, int, int] = (2026, 2, 10),
    end_date: tuple[int, int, int] | None = None,
    recurrences: tuple[str, ...] = (),
    event_id: str = "evt_allday",
) -> Event:
    """Create an all-day event. end_date defaults to same day as start (single day)."""
    if end_date is None:
        end_date = start_date
    return _make_event(
        title=title,
        all_day=True,
        start_at=_ts(*start_date, tz=UTC),
        end_at=_ts(*end_date, tz=UTC),
        start_timezone="Europe/Berlin",
        end_timezone="Europe/Berlin",
        recurrences=recurrences,
        event_id=event_id,
    )


# =========================================================================== #
#  1. _to_datetime / _ts_to_date
# =========================================================================== #


class TestToDatetime:
    """Test timestamp → datetime conversion."""

    def test_converts_to_aware_datetime(self):
        ev = _make_event(start_at=_ts(2026, 3, 15, 10, 0, UTC))
        result = _to_datetime(ev, use_end=False)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_respects_event_timezone(self):
        ev = _make_event(
            start_at=_ts(2026, 6, 15, 10, 0, UTC),
            start_timezone="Europe/Berlin",
        )
        result = _to_datetime(ev, use_end=False)
        # Berlin is UTC+2 in summer
        assert result.hour == 12

    def test_use_end_flag(self):
        ev = _make_event(
            start_at=_ts(2026, 2, 10, 10, 0, UTC),
            end_at=_ts(2026, 2, 10, 12, 0, UTC),
        )
        start = _to_datetime(ev, use_end=False)
        end = _to_datetime(ev, use_end=True)
        assert end > start
        assert (end - start) == timedelta(hours=2)


class TestTsToDate:
    """Test timestamp → date conversion."""

    def test_returns_date_object(self):
        result = _ts_to_date(_ts(2026, 3, 15, 0, 0, UTC), "Europe/Berlin")
        assert isinstance(result, date)
        assert not isinstance(result, datetime)

    def test_correct_date(self):
        result = _ts_to_date(_ts(2026, 7, 4, 0, 0, UTC), "UTC")
        assert result == date(2026, 7, 4)


# =========================================================================== #
#  2. _map_event – timed events
# =========================================================================== #


class TestMapTimedEvent:
    """Test mapping of timed (non-all-day) events."""

    def test_basic_timed_event(self):
        ev = _make_event(title="Meeting", note="Room 5", location="Office")
        cal_ev = _map_event(ev)
        assert cal_ev.summary == "Meeting"
        assert cal_ev.description == "Room 5"
        assert cal_ev.location == "Office"
        assert cal_ev.uid == "evt_1"
        assert isinstance(cal_ev.start, datetime)
        assert isinstance(cal_ev.end, datetime)

    def test_start_before_end(self):
        ev = _make_event()
        cal_ev = _map_event(ev)
        assert cal_ev.start < cal_ev.end


# =========================================================================== #
#  3. _map_event – all-day events
# =========================================================================== #


class TestMapAllDayEvent:
    """Test mapping of all-day events."""

    def test_single_day_event(self):
        ev = _make_allday_event(title="Holiday", start_date=(2026, 12, 25))
        cal_ev = _map_event(ev)
        assert cal_ev.summary == "Holiday"
        assert isinstance(cal_ev.start, date)
        assert not isinstance(cal_ev.start, datetime)
        assert cal_ev.start == date(2026, 12, 25)
        # HA expects exclusive end → single day should be start + 1 day
        assert cal_ev.end == date(2026, 12, 26)

    def test_multi_day_event(self):
        ev = _make_allday_event(
            title="Trip",
            start_date=(2026, 3, 1),
            end_date=(2026, 3, 5),
        )
        cal_ev = _map_event(ev)
        assert cal_ev.start == date(2026, 3, 1)
        assert cal_ev.end == date(2026, 3, 5)

    def test_end_never_before_start(self):
        """Even with weird data, end should never be <= start."""
        ev = _make_allday_event(start_date=(2026, 5, 1), end_date=(2026, 5, 1))
        cal_ev = _map_event(ev)
        assert cal_ev.end > cal_ev.start


# =========================================================================== #
#  4. _sort_key – mixed date/datetime sorting
# =========================================================================== #


class TestSortKey:
    """Sorting must handle mixed date and datetime CalendarEvent.start values.

    This was the very first bug: TypeError when sorting a list that
    contained both all-day (date) and timed (datetime) events.
    """

    def test_sort_key_for_datetime(self):

        ev = CalendarEvent(
            summary="T",
            start=datetime(2026, 2, 10, 18, 0, tzinfo=UTC),
            end=datetime(2026, 2, 10, 19, 0, tzinfo=UTC),
        )
        key = _sort_key(ev)
        assert isinstance(key, datetime)

    def test_sort_key_for_date(self):

        ev = CalendarEvent(
            summary="T",
            start=date(2026, 2, 10),
            end=date(2026, 2, 11),
        )
        key = _sort_key(ev)
        assert isinstance(key, datetime)

    def test_mixed_list_sorts_without_error(self):
        """The original crash: sorting a list with both date and datetime starts."""

        events = [
            CalendarEvent(
                summary="Timed",
                start=datetime(2026, 2, 10, 14, 0, tzinfo=UTC),
                end=datetime(2026, 2, 10, 15, 0, tzinfo=UTC),
            ),
            CalendarEvent(
                summary="All-day",
                start=date(2026, 2, 10),
                end=date(2026, 2, 11),
            ),
            CalendarEvent(
                summary="Timed Later",
                start=datetime(2026, 2, 11, 9, 0, tzinfo=UTC),
                end=datetime(2026, 2, 11, 10, 0, tzinfo=UTC),
            ),
        ]
        # Must not raise TypeError
        events.sort(key=_sort_key)
        assert events[0].summary == "All-day"
        assert events[1].summary == "Timed"
        assert events[2].summary == "Timed Later"


# =========================================================================== #
#  5. _fix_rrule_until_tz
# =========================================================================== #


class TestFixRruleUntilTz:
    """dateutil crashes when UNTIL has no timezone but dtstart is tz-aware."""

    def test_bare_date_gets_utc_suffix(self):
        assert _fix_rrule_until_tz("RRULE:FREQ=WEEKLY;UNTIL=20210429") == (
            "RRULE:FREQ=WEEKLY;UNTIL=20210429T000000Z"
        )

    def test_already_utc_untouched(self):
        rule = "RRULE:FREQ=WEEKLY;UNTIL=20210429T000000Z"
        assert _fix_rrule_until_tz(rule) == rule

    def test_with_time_component_untouched(self):
        rule = "RRULE:FREQ=DAILY;UNTIL=20210429T120000"
        assert _fix_rrule_until_tz(rule) == rule

    def test_no_until_untouched(self):
        rule = "RRULE:FREQ=YEARLY"
        assert _fix_rrule_until_tz(rule) == rule

    def test_until_mid_rule(self):
        rule = "RRULE:FREQ=WEEKLY;UNTIL=20251215;BYDAY=TU"
        result = _fix_rrule_until_tz(rule)
        assert "UNTIL=20251215T000000Z" in result
        assert "BYDAY=TU" in result


# =========================================================================== #
#  6. _expand_recurring – weekly
# =========================================================================== #


class TestExpandRecurringWeekly:
    """Test RRULE:FREQ=WEEKLY expansion (e.g. 'Büro' every Tuesday)."""

    def test_weekly_tuesday(self):
        ev = _make_allday_event(
            title="Büro",
            start_date=(2025, 10, 14),  # a Tuesday
            recurrences=("RRULE:FREQ=WEEKLY;BYDAY=TU",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 4  # Feb 3, 10, 17, 24

        dates = [r.start for r in results]
        assert all(isinstance(d, date) for d in dates)
        # All should be Tuesdays
        assert all(d.weekday() == 1 for d in dates)

    def test_weekly_with_until_in_past(self):
        """UNTIL in the past → no occurrences in future range."""
        ev = _make_event(
            title="Tennis",
            start_at=_ts(2020, 1, 7, 17, 30, BERLIN),
            end_at=_ts(2020, 1, 7, 19, 0, BERLIN),
            recurrences=("RRULE:FREQ=WEEKLY;UNTIL=20210429",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert results == []

    def test_weekly_with_until_bare_date_does_not_crash(self):
        """The exact bug: bare UNTIL=20210429 crashes dateutil without the fix."""
        ev = _make_event(
            title="Tennis",
            start_at=_ts(2020, 1, 7, 17, 30, BERLIN),
            end_at=_ts(2020, 1, 7, 19, 0, BERLIN),
            recurrences=("RRULE:FREQ=WEEKLY;UNTIL=20210429",),
        )
        range_start = datetime(2021, 4, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2021, 5, 1, 0, 0, tzinfo=UTC)

        # Must NOT raise ValueError
        results = _expand_recurring(ev, range_start, range_end)
        assert isinstance(results, list)


# =========================================================================== #
#  7. _expand_recurring – yearly (birthdays)
# =========================================================================== #


class TestExpandRecurringYearly:
    """Test RRULE:FREQ=YEARLY expansion (e.g. birthdays)."""

    def test_birthday_appears_every_year(self):
        ev = _make_allday_event(
            title="Fiona Geburtstag",
            start_date=(2020, 2, 9),
            recurrences=("RRULE:FREQ=YEARLY",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert results[0].summary == "Fiona Geburtstag"
        assert results[0].start == date(2026, 2, 9)

    def test_birthday_not_in_wrong_month(self):
        ev = _make_allday_event(
            title="March Birthday",
            start_date=(2000, 3, 15),
            recurrences=("RRULE:FREQ=YEARLY",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert results == []

    def test_birthday_original_far_in_past(self):
        """Birthday from 1960 should still appear in 2026."""
        ev = _make_allday_event(
            title="Opa Geburtstag",
            start_date=(1960, 6, 20),
            recurrences=("RRULE:FREQ=YEARLY",),
        )
        range_start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert results[0].start == date(2026, 6, 20)


# =========================================================================== #
#  8. _expand_recurring – monthly
# =========================================================================== #


class TestExpandRecurringMonthly:
    """Test RRULE:FREQ=MONTHLY expansion."""

    def test_monthly_first_wednesday(self):
        ev = _make_allday_event(
            title="Büro Monatlich",
            start_date=(2025, 4, 2),  # first Wednesday of April 2025
            recurrences=("RRULE:FREQ=MONTHLY;BYDAY=1WE",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert results[0].start == date(2026, 2, 4)  # first Wed of Feb 2026
        assert results[0].start.weekday() == 2  # Wednesday


# =========================================================================== #
#  9. _expand_recurring – daily
# =========================================================================== #


class TestExpandRecurringDaily:
    """Test RRULE:FREQ=DAILY expansion."""

    def test_daily_event(self):
        ev = _make_event(
            title="Standup",
            start_at=_ts(2026, 1, 1, 9, 0, BERLIN),
            end_at=_ts(2026, 1, 1, 9, 15, BERLIN),
            recurrences=("RRULE:FREQ=DAILY",),
        )
        range_start = datetime(2026, 2, 10, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 13, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        # Feb 10, 11, 12 = 3 occurrences
        assert len(results) == 3
        assert all(isinstance(r.start, datetime) for r in results)


# =========================================================================== #
#  10. _expand_recurring – EXDATE exclusions
# =========================================================================== #


class TestExpandRecurringWithExdate:
    """Test that EXDATE entries correctly exclude occurrences."""

    def test_exdate_excludes_occurrence(self):
        # EXDATE must match the occurrence timestamp exactly.
        # _make_allday_event uses _ts(2025, 4, 2, tz=UTC) which is midnight UTC.
        # In Berlin this is 02:00 CEST (summer) / 01:00 CET (winter).
        # The RRULE expansion generates occurrences at 02:00 Berlin time,
        # so Feb 4 2026 occurrence is at 02:00 CET = 01:00 UTC.
        ev = _make_allday_event(
            title="Büro",
            start_date=(2025, 4, 2),
            recurrences=(
                "RRULE:FREQ=MONTHLY;BYDAY=1WE",
                "EXDATE:20260204T010000Z",  # Matches Feb 4, 02:00 CET = 01:00 UTC
            ),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 0  # The only occurrence was excluded

    def test_exdate_only_removes_targeted_date(self):
        ev = _make_allday_event(
            title="Weekly",
            start_date=(2026, 2, 2),  # Monday
            recurrences=(
                "RRULE:FREQ=WEEKLY;BYDAY=MO",
                "EXDATE:20260209T000000Z",  # Exclude Feb 9
            ),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        dates = [r.start for r in results]
        assert date(2026, 2, 9) not in dates
        assert date(2026, 2, 2) in dates
        assert date(2026, 2, 16) in dates
        assert date(2026, 2, 23) in dates


# =========================================================================== #
#  11. _expand_recurring – edge cases
# =========================================================================== #


class TestExpandRecurringEdgeCases:
    """Edge cases that caused real crashes."""

    def test_no_rrule_only_exdate(self):
        """Event with EXDATE but no RRULE should return single mapped event."""
        ev = _make_event(
            title="Weird",
            recurrences=("EXDATE:20260210T000000Z",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert results[0].summary == "Weird"

    def test_empty_recurrence_string(self):
        """Event with empty string in recurrences should not crash."""
        ev = _make_event(
            title="Empty Rec",
            recurrences=("",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1  # Falls back to single event

    def test_allday_recurring_has_exclusive_end_date(self):
        """All-day recurring events must have end > start (exclusive end for HA)."""
        ev = _make_allday_event(
            title="Daily",
            start_date=(2026, 2, 1),
            recurrences=("RRULE:FREQ=DAILY",),
        )
        range_start = datetime(2026, 2, 10, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 12, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        for r in results:
            assert r.end > r.start

    def test_recurring_event_uid_unique_per_occurrence(self):
        """Each occurrence must have a unique UID."""
        ev = _make_event(
            title="Daily",
            start_at=_ts(2026, 1, 1, 9, 0, BERLIN),
            end_at=_ts(2026, 1, 1, 10, 0, BERLIN),
            recurrences=("RRULE:FREQ=DAILY",),
            event_id="parent_123",
        )
        range_start = datetime(2026, 2, 10, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 13, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        uids = [r.uid for r in results]
        assert len(uids) == len(set(uids)), "UIDs must be unique"
        assert all(uid.startswith("parent_123_") for uid in uids)


# =========================================================================== #
#  12. _kwargs_to_mutation
# =========================================================================== #


class TestKwargsToMutation:
    """Test service call data → EventMutation conversion."""

    def test_timed_event(self):
        mutation = _kwargs_to_mutation({
            "summary": "Team Meeting",
            "dtstart": datetime(2026, 3, 1, 10, 0, tzinfo=BERLIN),
            "dtend": datetime(2026, 3, 1, 11, 0, tzinfo=BERLIN),
            "description": "Weekly sync",
            "location": "Room A",
        })
        assert mutation.title == "Team Meeting"
        assert mutation.all_day is False
        assert mutation.note == "Weekly sync"
        assert mutation.location == "Room A"
        assert mutation.start_at < mutation.end_at

    def test_allday_event(self):
        mutation = _kwargs_to_mutation({
            "summary": "Vacation",
            "dtstart": date(2026, 7, 1),
            "dtend": date(2026, 7, 8),
        })
        assert mutation.title == "Vacation"
        assert mutation.all_day is True
        assert mutation.start_at < mutation.end_at

    def test_start_end_keys(self):
        """HA may pass 'start'/'end' instead of 'dtstart'/'dtend'."""
        mutation = _kwargs_to_mutation({
            "summary": "Test",
            "start": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            "end": datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        })
        assert mutation.title == "Test"
        assert mutation.all_day is False

    def test_raises_for_invalid_timed_event(self):
        with pytest.raises((ValueError, Exception)):
            _kwargs_to_mutation({
                "summary": "Bad",
                "dtstart": "not a datetime",
                "dtend": "not a datetime",
            })

    def test_ha_ui_start_date_time_keys(self):
        """HA UI sends start_date_time/end_date_time for timed events."""
        mutation = _kwargs_to_mutation({
            "summary": "From UI",
            "start_date_time": datetime(2026, 3, 1, 10, 0, tzinfo=BERLIN),
            "end_date_time": datetime(2026, 3, 1, 11, 0, tzinfo=BERLIN),
        })
        assert mutation.title == "From UI"
        assert mutation.all_day is False

    def test_ha_ui_start_date_keys(self):
        """HA UI sends start_date/end_date for all-day events."""
        mutation = _kwargs_to_mutation({
            "summary": "All day from UI",
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 2),
        })
        assert mutation.title == "All day from UI"
        assert mutation.all_day is True

    def test_string_datetime_parsed(self):
        """String datetimes should be auto-parsed."""
        mutation = _kwargs_to_mutation({
            "summary": "Parsed",
            "start_date_time": "2026-03-01 10:00:00",
            "end_date_time": "2026-03-01 11:00:00",
        })
        assert mutation.title == "Parsed"
        assert mutation.all_day is False
        assert mutation.start_at < mutation.end_at


# =========================================================================== #
#  13. Integration: recurring events with original dates in the past
# =========================================================================== #


class TestRecurringEventsFutureExpansion:
    """The main bug: recurring events with original dates far in the past
    must still appear in future date ranges.

    Previously, async_get_events filtered by original start/end dates
    BEFORE calling _expand_recurring, so these events were silently dropped.
    """

    def test_weekly_event_from_past_appears_in_future(self):
        """Büro starting Oct 2025 must appear in Feb 2026."""
        ev = _make_allday_event(
            title="Büro",
            start_date=(2025, 10, 14),  # Tuesday
            recurrences=("RRULE:FREQ=WEEKLY;BYDAY=TU",),
        )
        # Simulate the date range HA would request for Feb 2026
        range_start = datetime(2026, 2, 9, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 3, 9, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) > 0
        # Should include Feb 10 (Tuesday)
        dates = [r.start for r in results]
        assert date(2026, 2, 10) in dates

    def test_yearly_birthday_from_decades_ago(self):
        """Birthday from 1985 must appear in 2026."""
        ev = _make_allday_event(
            title="Marco Geburtstag",
            start_date=(1985, 8, 12),
            recurrences=("RRULE:FREQ=YEARLY",),
        )
        range_start = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert results[0].start == date(2026, 8, 12)

    def test_timed_recurring_preserves_time(self):
        """A weekly timed event (Padel 19:00) should keep the correct time."""
        ev = _make_event(
            title="Padel",
            start_at=_ts(2025, 9, 1, 19, 0, BERLIN),
            end_at=_ts(2025, 9, 1, 21, 0, BERLIN),
            recurrences=("RRULE:FREQ=WEEKLY;BYDAY=MO",),
        )
        range_start = datetime(2026, 2, 9, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 16, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        assert len(results) == 1
        assert isinstance(results[0].start, datetime)
        # Should be Monday at 19:00 Berlin time
        assert results[0].start.weekday() == 0  # Monday
        berlin_start = results[0].start.astimezone(BERLIN)
        assert berlin_start.hour == 19
        assert berlin_start.minute == 0

    def test_recurring_event_duration_preserved(self):
        """Event duration (end - start) must be consistent across occurrences."""
        ev = _make_event(
            title="Meeting",
            start_at=_ts(2026, 1, 5, 10, 0, BERLIN),
            end_at=_ts(2026, 1, 5, 11, 30, BERLIN),  # 90 min
            recurrences=("RRULE:FREQ=WEEKLY;BYDAY=MO",),
        )
        range_start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
        range_end = datetime(2026, 2, 28, 0, 0, tzinfo=UTC)

        results = _expand_recurring(ev, range_start, range_end)
        for r in results:
            duration = r.end - r.start
            assert duration == timedelta(minutes=90)
