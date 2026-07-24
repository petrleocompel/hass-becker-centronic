"""Device triggers for remote commands received for a Becker cover."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CHANNEL,
    ATTR_COMMAND,
    ATTR_UNIT,
    CONF_REMOTE_IDS,
    DOMAIN,
    EVENT_REMOTE_PACKET,
    SUBENTRY_TYPE_COVER,
)

TRIGGER_COMMANDS = {
    "remote_up": "up",
    "remote_down": "down",
    "remote_halt": "halt",
    "remote_up_intermediate": "up_intermediate",
    "remote_down_intermediate": "down_intermediate",
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_COMMANDS)}
)


@callback
def _subentry_for_device(
    hass: HomeAssistant, device_id: str
) -> ConfigSubentry | None:
    """Return the cover subentry a device belongs to."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    subentry_ids = {
        identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN
    }
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        for subentry_id, subentry in entry.subentries.items():
            if subentry_id in subentry_ids and (
                subentry.subentry_type == SUBENTRY_TYPE_COVER
            ):
                return subentry
    return None


@callback
def _remote_keys(subentry: ConfigSubentry) -> set[str]:
    """Return the remote keys (unit_id + channel) tracked by a cover."""
    keys: set[str] = set()
    for remote_id in (subentry.data.get(CONF_REMOTE_IDS) or "").split(","):
        remote_id = remote_id.strip().upper()
        if not remote_id:
            continue
        unit_id, _, channel = remote_id.partition(":")
        keys.add(f"{unit_id}{channel}")
        keys.add(f"{unit_id}F")
    return keys


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for covers with configured remotes."""
    subentry = _subentry_for_device(hass, device_id)
    if subentry is None or not subentry.data.get(CONF_REMOTE_IDS):
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_COMMANDS
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger for a remote command."""
    subentry = _subentry_for_device(hass, config[CONF_DEVICE_ID])
    keys = _remote_keys(subentry) if subentry is not None else set()
    command = TRIGGER_COMMANDS[config[CONF_TYPE]]
    job = HassJob(action, f"becker_centronic device trigger {config}")
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle_event(event: Event) -> None:
        if event.data.get(ATTR_COMMAND) != command:
            return
        key = f"{event.data.get(ATTR_UNIT, '')}{event.data.get(ATTR_CHANNEL, '')}"
        if key not in keys:
            return
        hass.async_run_hass_job(
            job,
            {
                "trigger": {
                    **trigger_data,
                    **config,
                    "event": event,
                    "description": f"remote command {command}",
                }
            },
            event.context,
        )

    return hass.bus.async_listen(EVENT_REMOTE_PACKET, _handle_event)
