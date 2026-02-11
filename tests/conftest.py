"""Conftest: mock Home Assistant modules for testing without the HA package.

Python 3.9 cannot install homeassistant (requires >=3.12), so we provide
lightweight stand-ins for the HA types used by calendar.py and its transitive
imports (coordinator.py, __init__.py, models.py, config_flow.py).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntFlag
from typing import Any
from unittest.mock import MagicMock


# --------------------------------------------------------------------------- #
#  Lightweight CalendarEvent stand-in
# --------------------------------------------------------------------------- #


@dataclass
class CalendarEvent:
    """Minimal stand-in for homeassistant.components.calendar.CalendarEvent."""

    summary: str
    start: date | datetime
    end: date | datetime
    description: str | None = None
    location: str | None = None
    uid: str | None = None


class CalendarEntityFeature(IntFlag):
    CREATE_EVENT = 1
    DELETE_EVENT = 2
    UPDATE_EVENT = 4


class _CalendarEntity:
    pass


class _CoordinatorEntity:
    """Stand-in base class for CoordinatorEntity."""
    def __init__(self, coordinator=None):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        """Support CoordinatorEntity[T] generic syntax."""
        return cls


class _DataUpdateCoordinator:
    """Stand-in for DataUpdateCoordinator."""
    def __init__(self, *args, **kwargs):
        pass

    def __class_getitem__(cls, item):
        return cls


class _Platform:
    """Stand-in for homeassistant.const.Platform."""
    CALENDAR = "calendar"


# --------------------------------------------------------------------------- #
#  Build fake module tree and inject into sys.modules
# --------------------------------------------------------------------------- #

def _make_module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# homeassistant top-level
ha = _make_module("homeassistant")
ha_components = _make_module("homeassistant.components")
ha_components_calendar = _make_module(
    "homeassistant.components.calendar",
    CalendarEntity=_CalendarEntity,
    CalendarEntityFeature=CalendarEntityFeature,
    CalendarEvent=CalendarEvent,
)
ha_config_entries = _make_module(
    "homeassistant.config_entries",
    ConfigEntry=MagicMock,
)
ha_const = _make_module(
    "homeassistant.const",
    Platform=_Platform,
)
ha_core = _make_module(
    "homeassistant.core",
    HomeAssistant=MagicMock,
)
ha_helpers = _make_module("homeassistant.helpers")
ha_helpers_entity_platform = _make_module(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=MagicMock,
)
ha_helpers_update_coordinator = _make_module(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_CoordinatorEntity,
    DataUpdateCoordinator=_DataUpdateCoordinator,
    UpdateFailed=Exception,
)
ha_exceptions = _make_module(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=Exception,
    ConfigEntryNotReady=Exception,
)

# config_flow may be imported transitively
ha_data_entry_flow = _make_module(
    "homeassistant.data_entry_flow",
    FlowResult=dict,
)
ha_helpers_config_entry_flow = _make_module(
    "homeassistant.helpers.config_entry_flow",
)

# Wire up the package hierarchy
ha.components = ha_components  # type: ignore[attr-defined]
ha.helpers = ha_helpers  # type: ignore[attr-defined]

_all_mods = {
    "homeassistant": ha,
    "homeassistant.components": ha_components,
    "homeassistant.components.calendar": ha_components_calendar,
    "homeassistant.config_entries": ha_config_entries,
    "homeassistant.const": ha_const,
    "homeassistant.core": ha_core,
    "homeassistant.data_entry_flow": ha_data_entry_flow,
    "homeassistant.helpers": ha_helpers,
    "homeassistant.helpers.config_entry_flow": ha_helpers_config_entry_flow,
    "homeassistant.helpers.entity_platform": ha_helpers_entity_platform,
    "homeassistant.helpers.update_coordinator": ha_helpers_update_coordinator,
    "homeassistant.exceptions": ha_exceptions,
}

for name, mod in _all_mods.items():
    sys.modules[name] = mod
