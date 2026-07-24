"""Button platform: per-cover resync button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BeckerConfigEntry
from .const import CONF_CHANNEL, CONF_UNIT, DOMAIN, MANUFACTURER, SUBENTRY_TYPE_COVER
from .hub import BeckerHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BeckerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up resync buttons for all covers."""
    hub = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_COVER:
            continue
        async_add_entities(
            [BeckerResyncButton(hub, subentry)], config_subentry_id=subentry_id
        )


class BeckerResyncButton(ButtonEntity):
    """Button that resyncs the increment counter with the receiver."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "resync"

    def __init__(self, hub: BeckerHub, subentry: ConfigSubentry) -> None:
        """Initialize the button."""
        self._hub = hub
        self._unit: int = subentry.data[CONF_UNIT]
        self._channel: int = subentry.data[CONF_CHANNEL]
        self._attr_unique_id = f"{subentry.subentry_id}_resync"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer=MANUFACTURER,
            model="Centronic cover",
        )

    @property
    def available(self) -> bool:
        """Button is available when the stick is connected."""
        return self._hub.connected

    async def async_press(self) -> None:
        """Send HALT 3x so the receiver re-adopts the increment counter."""
        await self._hub.async_resync(self._unit, self._channel)
