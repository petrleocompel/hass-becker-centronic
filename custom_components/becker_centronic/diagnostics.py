"""Diagnostics support for Becker Centronic."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import BeckerConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BeckerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
        },
        "connected": hub.connected,
        "units": hub.store.snapshot(),
        "covers": [
            {
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
                "data": dict(subentry.data),
            }
            for subentry in entry.subentries.values()
        ],
        "recent_packets": list(hub.recent_packets),
    }
