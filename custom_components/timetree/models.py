"""Runtime data models for the TimeTree integration."""

from __future__ import annotations

from dataclasses import dataclass

from .timetree_api import TimeTreeApiClient

from .coordinator import TimeTreeCalendarCoordinator


@dataclass
class TimeTreeRuntimeData:
    """Data stored in config_entry.runtime_data."""

    client: TimeTreeApiClient
    coordinators: list[TimeTreeCalendarCoordinator]
