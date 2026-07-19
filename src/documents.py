# documents.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Portable JSON solar-system document helpers."""

from __future__ import annotations

import json
from typing import Iterable

from .models import ModelError, SolarSystem


def parse_document(data: bytes | str) -> SolarSystem:
    """Parse and validate a serialized solar-system document."""

    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
    except UnicodeDecodeError as error:
        raise ModelError("document is not valid UTF-8") from error
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise ModelError(f"document is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ModelError("solar-system document must contain a JSON object")
    return SolarSystem.from_dict(payload)


def serialize_document(system: SolarSystem) -> bytes:
    """Return a canonical, human-readable UTF-8 document."""

    system.validate()
    text = json.dumps(system.to_dict(), indent=2, sort_keys=True)
    return f"{text}\n".encode("utf-8")


def unique_import_name(name: str, existing_names: Iterable[str]) -> str:
    """Choose a stable imported-copy name that does not collide case-insensitively."""

    base = name.strip() or "Imported System"
    used = {item.strip().casefold() for item in existing_names}
    if base.casefold() not in used:
        return base
    imported = f"{base} Imported"
    if imported.casefold() not in used:
        return imported
    suffix = 2
    while f"{imported} {suffix}".casefold() in used:
        suffix += 1
    return f"{imported} {suffix}"
