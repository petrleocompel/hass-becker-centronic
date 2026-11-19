"""Persistent storage of the per-unit rolling increment counters.

Replaces the SQLite database of the previous implementations with Home
Assistant's own JSON storage. Supports a one-time import of an existing
``centronic-stick.db`` so already-paired units keep working.
"""

from __future__ import annotations

import logging
import sqlite3
from random import randrange
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .protocol import INCREMENT_MAX, NUM_UNITS, unit_code_for

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Persist on the next loop iteration: a counter that was transmitted but not
# saved is reused after a crash, and receivers ignore reused increments.
SAVE_DELAY = 0


class UnitData(TypedDict):
    """Stored state of one emulated sender unit."""

    code: str
    increment: int
    configured: bool
    # Last increment transmitted per channel ("1".."7"). All channels of a unit
    # share one counter, so a receiver sees the counter jump by every frame
    # sent to its sibling channels in the meantime.
    channels: dict[str, int]


class UnitStore:
    """Store for the increment counters of the stick's 5 emulated units."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._units: dict[int, UnitData] = {}

    async def async_load(self) -> None:
        """Load stored data, initializing defaults for missing units."""
        data = await self._store.async_load() or {}
        stored_units = data.get("units", {})
        for unit in range(1, NUM_UNITS + 1):
            stored = stored_units.get(str(unit))
            if stored is None:
                self._units[unit] = UnitData(
                    code=unit_code_for(unit), increment=0, configured=False, channels={}
                )
            else:
                self._units[unit] = UnitData(
                    code=stored.get("code", unit_code_for(unit)),
                    increment=int(stored.get("increment", 0)),
                    configured=bool(stored.get("configured", False)),
                    channels={
                        str(channel): int(increment)
                        for channel, increment in stored.get("channels", {}).items()
                    },
                )

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "units": {
                str(unit): {**data, "channels": dict(data["channels"])}
                for unit, data in self._units.items()
            }
        }

    async def async_save_now(self) -> None:
        """Persist immediately (used on unload)."""
        await self._store.async_save(self._data_to_save())

    async def async_remove(self) -> None:
        """Remove the storage file (used when the entry is deleted)."""
        await self._store.async_remove()

    def unit_code(self, unit: int) -> str:
        """Return the RF unit code for a unit number."""
        return self._units[unit]["code"]

    def is_configured(self, unit: int) -> bool:
        """Return if the unit has been initialized/used before."""
        return self._units[unit]["configured"]

    def current_increment(self, unit: int) -> int:
        """Return the current increment of a unit without consuming it."""
        return self._units[unit]["increment"]

    def take_increment(self, unit: int, channel: int) -> int:
        """Return the current increment, advance the counter, remember the channel."""
        data = self._units[unit]
        increment = data["increment"]
        data["increment"] = (increment + 1) & INCREMENT_MAX
        data["channels"][str(channel)] = increment
        self._schedule_save()
        return increment

    def channel_gap(self, unit: int, channel: int) -> int | None:
        """Return how far the next increment is ahead of the channel's last one.

        None if nothing has been sent on the channel yet (e.g. after upgrading).
        """
        data = self._units[unit]
        last = data["channels"].get(str(channel))
        if last is None:
            return None
        return (data["increment"] - last) & INCREMENT_MAX

    def seed_unit(self, unit: int) -> None:
        """Mark a fresh unit as configured with a random start increment."""
        data = self._units[unit]
        data["increment"] = randrange(10, 40)
        data["configured"] = True
        self._schedule_save()

    def set_configured(self, unit: int, configured: bool = True) -> None:
        """Mark a unit as configured."""
        self._units[unit]["configured"] = configured
        self._schedule_save()

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the stored data (for diagnostics)."""
        return self._data_to_save()

    async def async_import_sqlite(self, hass: HomeAssistant, path: str) -> bool:
        """Import increments from a legacy centronic-stick.db file."""
        try:
            rows = await hass.async_add_executor_job(_read_sqlite_units, path)
        except (sqlite3.Error, OSError) as err:
            _LOGGER.error("Import of %s failed: %s", path, err)
            return False
        imported = False
        code_to_unit = {
            data["code"].upper(): unit for unit, data in self._units.items()
        }
        for index, (code, increment, configured) in enumerate(rows[:NUM_UNITS]):
            unit = code_to_unit.get(str(code).upper(), index + 1)
            self._units[unit] = UnitData(
                code=str(code).upper(),
                increment=int(increment) & INCREMENT_MAX,
                configured=bool(configured),
                channels={},
            )
            imported = True
            _LOGGER.info(
                "Imported unit %s (code %s) with increment %s (configured: %s)",
                unit,
                code,
                increment,
                bool(configured),
            )
        if imported:
            self._schedule_save()
        return imported


def _read_sqlite_units(path: str) -> list[tuple[str, int, int]]:
    """Read unit rows from a legacy sqlite database (executor job)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(
            "SELECT code, increment, configured FROM unit ORDER BY rowid ASC"
        )
        return [(str(row[0]), int(row[1]), int(row[2])) for row in cursor.fetchall()]
    finally:
        conn.close()
