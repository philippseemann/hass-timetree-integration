"""Data models for TimeTree API responses."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class EventCategory(enum.IntEnum):
    """Event category types.

    The TimeTree API uses numeric values: 1 = schedule, 2 = memo.
    """

    SCHEDULE = 1
    MEMO = 2


@dataclass(frozen=True)
class User:
    """TimeTree user."""

    id: str
    name: str
    image_url: str | None = None
    email: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> User:
        """Construct from a decamelized API response dict."""
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            image_url=data.get("image_url"),
            email=data.get("email"),
        )


@dataclass(frozen=True)
class Calendar:
    """TimeTree calendar."""

    id: str
    name: str
    color: str | None = None
    image_url: str | None = None
    created_at: int | None = None
    order: int | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> Calendar:
        """Construct from a decamelized API response dict."""
        return cls(
            id=str(data["id"]),
            name=data.get("name", ""),
            color=data.get("color"),
            image_url=data.get("image_url"),
            created_at=data.get("created_at"),
            order=data.get("order"),
        )


@dataclass(frozen=True)
class Label:
    """Calendar label (color tag)."""

    id: int
    name: str
    color: str

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> Label:
        """Construct from a decamelized API response dict."""
        return cls(
            id=int(data["id"]),
            name=data.get("name", ""),
            color=data.get("color", ""),
        )


@dataclass(frozen=True)
class Event:
    """TimeTree calendar event."""

    id: str
    calendar_id: str
    title: str
    all_day: bool
    start_at: int  # Unix milliseconds
    end_at: int  # Unix milliseconds
    start_timezone: str
    end_timezone: str
    category: EventCategory = EventCategory.SCHEDULE
    label_id: int | None = None
    note: str | None = None
    location: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    attendees: tuple[str, ...] = field(default_factory=tuple)
    recurrences: tuple[str, ...] = field(default_factory=tuple)
    alerts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    parent_id: str | None = None
    deleted_at: int | None = None  # Non-None = soft-deleted
    updated_at: int | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], *, calendar_id: str) -> Event:
        """Construct from a decamelized API response dict.

        Args:
            data: Decamelized response dictionary.
            calendar_id: The calendar this event belongs to (not always in the payload).
        """
        return cls(
            id=str(data["id"]),
            calendar_id=calendar_id,
            title=data.get("title", ""),
            all_day=data.get("all_day", False),
            start_at=data["start_at"],
            end_at=data["end_at"],
            start_timezone=data.get("start_timezone", "UTC"),
            end_timezone=data.get("end_timezone", "UTC"),
            category=_parse_category(data.get("category")),
            label_id=data.get("label_id"),
            note=data.get("note"),
            location=data.get("location"),
            location_lat=data.get("location_lat"),
            location_lon=data.get("location_lon"),
            attendees=tuple(data.get("attendees") or ()),
            recurrences=tuple(data.get("recurrences") or ()),
            alerts=tuple(data.get("alerts") or ()),
            parent_id=data.get("parent_id"),
            deleted_at=data.get("deleted_at"),
            updated_at=data.get("updated_at"),
        )

    @property
    def is_deleted(self) -> bool:
        """Whether this event has been soft-deleted."""
        return self.deleted_at is not None

    @property
    def is_recurring(self) -> bool:
        """Whether this event has recurrence rules."""
        return any(r.startswith("RRULE:") for r in self.recurrences)


@dataclass(frozen=True)
class EventMutation:
    """Data for creating or updating an event.

    Use ``dataclasses.replace()`` to derive modified copies.
    """

    title: str
    all_day: bool
    start_at: int  # Unix milliseconds
    end_at: int  # Unix milliseconds
    start_timezone: str = "UTC"
    end_timezone: str = "UTC"
    category: EventCategory = EventCategory.SCHEDULE
    label_id: int | None = None
    note: str | None = None
    location: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    attendees: tuple[str, ...] = field(default_factory=tuple)
    recurrences: tuple[str, ...] = field(default_factory=tuple)
    alerts: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to a dict for the API request body (snake_case).

        The client will camelize keys before sending.
        Only includes fields that are set to avoid sending unnecessary nulls.
        """
        result: dict[str, Any] = {
            "title": self.title,
            "all_day": self.all_day,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "start_timezone": self.start_timezone,
            "end_timezone": self.end_timezone,
            "category": self.category.value,
        }
        if self.label_id is not None:
            result["label_id"] = self.label_id
        if self.note is not None:
            result["note"] = self.note
        if self.location is not None:
            result["location"] = self.location
        if self.location_lat is not None:
            result["location_lat"] = self.location_lat
        if self.location_lon is not None:
            result["location_lon"] = self.location_lon
        if self.attendees:
            result["attendees"] = list(self.attendees)
        if self.recurrences:
            result["recurrences"] = list(self.recurrences)
        if self.alerts:
            result["alerts"] = list(self.alerts)
        return result


def _parse_category(value: Any) -> EventCategory:
    """Parse an event category, defaulting to SCHEDULE for unknown values."""
    if value is None:
        return EventCategory.SCHEDULE
    try:
        return EventCategory(value)
    except (ValueError, KeyError):
        return EventCategory.SCHEDULE
