"""JSON key transformation between camelCase and snake_case."""

from __future__ import annotations

import re
from typing import Any

_CAMEL_TO_SNAKE = re.compile(r"(?<=[a-z0-9])([A-Z])")
_SNAKE_TO_CAMEL = re.compile(r"_([a-z])")


def _to_snake(name: str) -> str:
    return _CAMEL_TO_SNAKE.sub(r"_\1", name).lower()


def _to_camel(name: str) -> str:
    return _SNAKE_TO_CAMEL.sub(lambda m: m.group(1).upper(), name)


def decamelize(data: Any) -> Any:
    """Recursively convert all dict keys from camelCase to snake_case."""
    if isinstance(data, dict):
        return {_to_snake(k): decamelize(v) for k, v in data.items()}
    if isinstance(data, list):
        return [decamelize(item) for item in data]
    return data


def camelize(data: Any) -> Any:
    """Recursively convert all dict keys from snake_case to camelCase."""
    if isinstance(data, dict):
        return {_to_camel(k): camelize(v) for k, v in data.items()}
    if isinstance(data, list):
        return [camelize(item) for item in data]
    return data
