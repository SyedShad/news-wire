"""Versioned tracked defaults with private local overrides."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .config import RuntimePaths


class InvalidConfiguration(ValueError):
    """Raised when tracked or local configuration is malformed."""


def _read_packaged_json(directory: str, name: str) -> dict[str, Any]:
    resource = files("open_source_ai_news_wire").joinpath(directory, name)
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InvalidConfiguration(f"{directory}/{name} must contain a JSON object")
    return payload


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _local_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidConfiguration(f"Invalid local configuration: {path}") from error
    if not isinstance(value, dict):
        raise InvalidConfiguration(f"Local configuration must be a JSON object: {path}")
    return value


@dataclass(frozen=True, slots=True)
class AppSettings:
    values: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        value = self.values.get(name)
        if not isinstance(value, dict):
            raise InvalidConfiguration(f"Missing configuration section: {name}")
        return deepcopy(value)


def load_settings(paths: RuntimePaths) -> AppSettings:
    defaults = _read_packaged_json("definitions", "defaults.json")
    local = _local_json(paths.config / "settings.local.json")
    merged = _merge(defaults, local)
    if merged.get("schema_version") != 1:
        raise InvalidConfiguration("Unsupported settings schema version")
    return AppSettings(merged)


def load_source_definitions(paths: RuntimePaths) -> list[dict[str, Any]]:
    tracked = _read_packaged_json("definitions", "sources.json")
    local = _local_json(paths.config / "sources.local.json")
    definitions = tracked.get("sources")
    if tracked.get("schema_version") != 1 or not isinstance(definitions, list):
        raise InvalidConfiguration("Unsupported source-registry schema")
    overrides = local.get("sources", {})
    if overrides and not isinstance(overrides, dict):
        raise InvalidConfiguration("Local source overrides must be keyed by source ID")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in definitions:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise InvalidConfiguration("Every tracked source requires a stable ID")
        source_id = item["id"]
        if source_id in seen:
            raise InvalidConfiguration(f"Duplicate source ID: {source_id}")
        seen.add(source_id)
        override = overrides.get(source_id, {}) if isinstance(overrides, dict) else {}
        if override and not isinstance(override, dict):
            raise InvalidConfiguration(f"Invalid local source override: {source_id}")
        merged = _merge(item, override)
        merged["checked_date"] = tracked.get("checked_date")
        result.append(merged)
    unknown = set(overrides) - seen if isinstance(overrides, dict) else set()
    if unknown:
        raise InvalidConfiguration(f"Unknown local source IDs: {', '.join(sorted(unknown))}")
    return result
