"""The Becker Centronic integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import CONF_LEGACY_DB_PATH, DOMAIN, MANUFACTURER, MODEL_STICK
from .hub import BeckerHub, CannotConnect
from .storage import UnitStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.COVER]

type BeckerConfigEntry = ConfigEntry[BeckerHub]


async def async_setup_entry(hass: HomeAssistant, entry: BeckerConfigEntry) -> bool:
    """Set up a Becker Centronic USB stick from a config entry."""
    store = UnitStore(hass, entry.entry_id)
    await store.async_load()

    # One-time import of increment counters from a legacy centronic-stick.db
    legacy_db_path = entry.data.get(CONF_LEGACY_DB_PATH)
    if legacy_db_path:
        await store.async_import_sqlite(hass, legacy_db_path)
        data = dict(entry.data)
        data.pop(CONF_LEGACY_DB_PATH)
        hass.config_entries.async_update_entry(entry, data=data)

    hub = BeckerHub(hass, entry.entry_id, dict(entry.data), store)
    try:
        await hub.async_start()
    except CannotConnect as err:
        raise ConfigEntryNotReady(f"Cannot connect to Centronic stick: {err}") from err
    entry.runtime_data = hub

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL_STICK,
        name=entry.title,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: BeckerConfigEntry) -> None:
    """Reload the entry when its data or subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BeckerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the increment store when the entry is deleted."""
    store = UnitStore(hass, entry.entry_id)
    await store.async_remove()
