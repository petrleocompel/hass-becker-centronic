"""Cover platform for the Becker Centronic integration."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    TrackTemplate,
    async_call_later,
    async_track_template_result,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.template import Template

from . import BeckerConfigEntry
from .const import (
    CLOSED_POSITION,
    CONF_CHANNEL,
    CONF_DEVICE_CLASS,
    CONF_INTERMEDIATE_POSITION_DOWN,
    CONF_INTERMEDIATE_POSITION_UP,
    CONF_REMOTE_IDS,
    CONF_TILT_MODE,
    CONF_TILT_TIME_BLIND,
    CONF_TRACK_INTERMEDIATE,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_TRAVELLING_TIME_UP,
    CONF_UNIT,
    CONF_VALUE_TEMPLATE,
    DEFAULT_INTERMEDIATE_POSITION_DOWN,
    DEFAULT_INTERMEDIATE_POSITION_UP,
    DEFAULT_TILT_TIME_BLIND,
    DOMAIN,
    MANUFACTURER,
    OPEN_POSITION,
    SERVICE_CLEAR_POSITION,
    SERVICE_PAIR_MASTER,
    SERVICE_RESYNC,
    SERVICE_UNPAIR,
    SUBENTRY_TYPE_COVER,
    TILT_MODE_BLIND,
    TILT_MODE_INTERMEDIATE,
    TILT_RECEIVE_TIMEOUT,
)
from .hub import BeckerHub
from .protocol import Command, ReceivedPacket
from .travelcalculator import TravelCalculator

_LOGGER = logging.getLogger(__name__)

TEMPLATE_VALID_OPEN = ("open", "true", True)
TEMPLATE_VALID_CLOSE = ("closed", "false", False)
TEMPLATE_UNKNOWN_STATES = ("unknown", "unavailable", "none", None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BeckerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Becker covers from config subentries."""
    hub = entry.runtime_data

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_RESYNC, None, "async_resync")
    platform.async_register_entity_service(SERVICE_UNPAIR, None, "async_unpair")
    platform.async_register_entity_service(
        SERVICE_PAIR_MASTER, None, "async_pair_master"
    )
    platform.async_register_entity_service(
        SERVICE_CLEAR_POSITION, None, "async_clear_position"
    )

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_COVER:
            continue
        async_add_entities(
            [BeckerCover(hub, entry.entry_id, subentry)],
            config_subentry_id=subentry_id,
        )


class BeckerCover(CoverEntity, RestoreEntity):
    """Representation of a Becker Centronic cover."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(
        self, hub: BeckerHub, entry_id: str, subentry: ConfigSubentry
    ) -> None:
        """Initialize the cover from its subentry configuration."""
        config = subentry.data
        self._hub = hub
        self._unit: int = config[CONF_UNIT]
        self._channel: int = config[CONF_CHANNEL]

        self._attr_unique_id = subentry.subentry_id
        self._attr_device_class = CoverDeviceClass(config[CONF_DEVICE_CLASS])
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer=MANUFACTURER,
            model="Centronic cover",
            via_device=(DOMAIN, entry_id),
        )

        # Position tracking configuration
        self._travel_time_up = config.get(CONF_TRAVELLING_TIME_UP)
        self._travel_time_down = config.get(CONF_TRAVELLING_TIME_DOWN)
        # Template object is created in async_added_to_hass (needs hass)
        self._template_str: str | None = config.get(CONF_VALUE_TEMPLATE) or None

        # Tilt / intermediate configuration
        self._tilt_mode = config.get(CONF_TILT_MODE, TILT_MODE_INTERMEDIATE)
        self._track_intermediate = config.get(CONF_TRACK_INTERMEDIATE, True)
        self._intermediate_pos_up = config.get(
            CONF_INTERMEDIATE_POSITION_UP, DEFAULT_INTERMEDIATE_POSITION_UP
        )
        self._intermediate_pos_down = config.get(
            CONF_INTERMEDIATE_POSITION_DOWN, DEFAULT_INTERMEDIATE_POSITION_DOWN
        )
        self._tilt_time_blind = config.get(
            CONF_TILT_TIME_BLIND, DEFAULT_TILT_TIME_BLIND
        )
        self._tilt_timeout = time.time()

        # Supported features
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        has_travel_time = (
            self._travel_time_up is not None or self._travel_time_down is not None
        )
        if has_travel_time and self._template_str is None:
            features |= CoverEntityFeature.SET_POSITION
        if self._tilt_mode in (TILT_MODE_INTERMEDIATE, TILT_MODE_BLIND):
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
        self._attr_supported_features = features

        travel_time_down = self._travel_time_down or self._travel_time_up or 0
        travel_time_up = self._travel_time_up or self._travel_time_down or 0
        self._tc = TravelCalculator(travel_time_down, travel_time_up)

        # Remote IDs to track ("12345:2" also matches the remote's ALL channel F)
        self._remote_keys: set[str] = set()
        for remote_id in (config.get(CONF_REMOTE_IDS) or "").split(","):
            remote_id = remote_id.strip().upper()
            if not remote_id:
                continue
            unit_id, _, remote_channel = remote_id.partition(":")
            self._remote_keys.add(f"{unit_id}{remote_channel}")
            self._remote_keys.add(f"{unit_id}F")

        self._callbacks: dict[str, Any] = {}
        self._template: Template | None = None

    async def async_added_to_hass(self) -> None:
        """Restore state and subscribe to hub signals."""
        await super().async_added_to_hass()
        if self._template_str is not None:
            self._template = Template(self._template_str, self.hass)

        # Restore last known position
        old_state = await self.async_get_last_state()
        if old_state is not None:
            position = old_state.attributes.get(ATTR_CURRENT_POSITION)
            if position is not None:
                # In TravelCalculator 0 is open, 100 is closed.
                self._tc.set_position(100 - int(position))
        if self._tc.current_position() is None:
            self._tc.set_position(100 - CLOSED_POSITION)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.signal_packet, self._async_packet_received
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._hub.signal_connection, self._async_connection_changed
            )
        )
        if self._template is not None:
            info = async_track_template_result(
                self.hass,
                [TrackTemplate(self._template, None)],
                self._async_on_template_update,
            )
            self.async_on_remove(info.async_remove)
            info.async_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending callbacks."""
        for cancel in self._callbacks.values():
            cancel()
        self._callbacks.clear()

    # ------------------------------------------------------------- properties

    @property
    def available(self) -> bool:
        """Cover is available when the stick is connected."""
        return self._hub.connected

    @property
    def current_cover_position(self) -> int | None:
        """Return current position: 0 is closed, 100 is fully open."""
        position = self._tc.current_position()
        if position is None:
            return None
        # In TravelCalculator 0 is open, 100 is closed.
        return 100 - position

    @property
    def is_closed(self) -> bool | None:
        """Return true if cover is closed."""
        if self._tc.current_position() is None:
            return None
        return self._tc.is_closed()

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        if not self._attr_supported_features & CoverEntityFeature.SET_POSITION:
            return False
        return self._tc.is_opening()

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        if not self._attr_supported_features & CoverEntityFeature.SET_POSITION:
            return False
        return self._tc.is_closing()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return channel information as attributes."""
        return {"unit": self._unit, "channel": self._channel}

    # ---------------------------------------------------------------- actions

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        self._travel_to_position(OPEN_POSITION)
        await self._hub.async_move_up(self._unit, self._channel)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        self._travel_to_position(CLOSED_POSITION)
        await self._hub.async_move_down(self._unit, self._channel)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        self._travel_stop()
        await self._hub.async_halt(self._unit, self._channel)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open tilt: intermediate up position or short blind move."""
        if self._tilt_mode == TILT_MODE_BLIND:
            await self.async_open_cover()
            self._schedule_stop_travel(self._tilt_time_blind)
        elif self._tilt_mode == TILT_MODE_INTERMEDIATE:
            self._travel_to_position(self._intermediate_pos_up)
            await self._hub.async_move_up_intermediate(self._unit, self._channel)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close tilt: intermediate down position or short blind move."""
        if self._tilt_mode == TILT_MODE_BLIND:
            await self.async_close_cover()
            self._schedule_stop_travel(self._tilt_time_blind)
        elif self._tilt_mode == TILT_MODE_INTERMEDIATE:
            self._travel_to_position(self._intermediate_pos_down)
            await self._hub.async_move_down_intermediate(self._unit, self._channel)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        if ATTR_POSITION not in kwargs:
            return
        position = kwargs[ATTR_POSITION]
        travel_time = self._travel_to_position(position)
        if self._tc.is_closing():
            await self._hub.async_move_down(self._unit, self._channel)
        elif self._tc.is_opening():
            await self._hub.async_move_up(self._unit, self._channel)
        if CLOSED_POSITION < position < OPEN_POSITION:
            self._schedule_stop_travel(travel_time)

    # Maintenance actions (entity services)

    async def async_resync(self) -> None:
        """Repeat HALT 3x to force the receiver to adopt the increment."""
        await self._hub.async_resync(self._unit, self._channel)

    async def async_unpair(self) -> None:
        """Unpair this cover from the stick (receiver must be in pairing mode)."""
        await self._hub.async_unpair(self._unit, self._channel)

    async def async_pair_master(self) -> None:
        """Register the stick as master sender (receiver in pairing mode)."""
        await self._hub.async_train_master(self._unit, self._channel)

    async def async_clear_position(self) -> None:
        """Send CLEARPOS to clear the receiver's stored end positions."""
        await self._hub.async_clear_position(self._unit, self._channel)

    # ------------------------------------------------------ position tracking

    def _travel_to_position(self, position: int) -> float:
        """Start the travel calculator towards a position, return travel time."""
        travel_time = self._tc.calculate_travel_time(
            position, self.current_cover_position or 0
        )
        if self._template_str is None:
            _LOGGER.debug(
                "%s is travelling from %s to %s in %.1f seconds",
                self.entity_id,
                self.current_cover_position,
                position,
                travel_time,
            )
            # In TravelCalculator 0 is open, 100 is closed.
            self._tc.start_travel(100 - position)
            self._schedule_ha_state_update(travel_time)
        return travel_time

    def _travel_stop(self) -> None:
        """Stop the travel calculator and update state."""
        self._tc.stop()
        if (
            not self._attr_supported_features & CoverEntityFeature.SET_POSITION
            and self._template_str is None
        ):
            # Without travel times the stop position is unknown; assume half way
            self._tc.set_position(50)
        _LOGGER.debug(
            "%s stopped at position %s", self.entity_id, self.current_cover_position
        )
        self._schedule_ha_state_update(0)

    def _schedule_ha_state_update(self, delay: float) -> None:
        """Update HA state now and again once the travel time has elapsed."""
        self._callbacks.pop("update_ha", lambda: None)()
        self.async_write_ha_state()
        if delay > 0:
            self._callbacks["update_ha"] = async_call_later(
                self.hass, delay, self._async_update_ha_state_later
            )

    def _schedule_stop_travel(self, delay: float) -> None:
        """Schedule a physical stop command after a delay."""
        self._callbacks.pop("travel_stop", lambda: None)()
        if delay >= 0:
            self._callbacks["travel_stop"] = async_call_later(
                self.hass, delay, self._async_stop_travel_later
            )

    @callback
    def _async_update_ha_state_later(self, _now: Any) -> None:
        self._callbacks.pop("update_ha", None)
        self.async_write_ha_state()

    async def _async_stop_travel_later(self, _now: Any) -> None:
        self._callbacks.pop("travel_stop", None)
        self._travel_stop()
        await self._hub.async_halt(self._unit, self._channel)

    @callback
    def _async_connection_changed(self, _connected: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _async_packet_received(self, packet: ReceivedPacket) -> None:
        """Track commands sent by paired remotes."""
        if packet.remote_key not in self._remote_keys:
            return
        _LOGGER.debug(
            "%s received remote command %s", self.entity_id, packet.command_name
        )
        command = packet.command
        if command == Command.HALT:
            self._travel_stop()
        elif command == Command.RELEASE and self._tilt_timeout > time.time():
            if self._tilt_mode == TILT_MODE_BLIND and (
                self.is_opening or self.is_closing
            ):
                self._travel_stop()
        elif command == Command.UP_INTERMEDIATE and self._track_intermediate:
            self._travel_to_position(self._intermediate_pos_up)
            self._tilt_timeout = time.time()  # reset timeout
        elif command & 0xF0 == 0x20:
            self._travel_to_position(OPEN_POSITION)
            self._tilt_timeout = time.time() + TILT_RECEIVE_TIMEOUT
        elif command == Command.DOWN_INTERMEDIATE and self._track_intermediate:
            self._travel_to_position(self._intermediate_pos_down)
            self._tilt_timeout = time.time()  # reset timeout
        elif command & 0xF0 == 0x40:
            self._travel_to_position(CLOSED_POSITION)
            self._tilt_timeout = time.time() + TILT_RECEIVE_TIMEOUT

    @callback
    def _async_on_template_update(self, _event: Any, updates: list[Any]) -> None:
        """Update the position from a template result."""
        result = updates.pop().result
        if isinstance(result, TemplateError):
            _LOGGER.error("%s: template update failed: %s", self.entity_id, result)
            return
        if isinstance(result, str):
            result = result.lower()
        if result in TEMPLATE_VALID_OPEN:
            position = OPEN_POSITION
        elif result in TEMPLATE_VALID_CLOSE:
            position = CLOSED_POSITION
        elif isinstance(result, (int, float)):
            position = round(max(min(result, OPEN_POSITION), CLOSED_POSITION))
        elif result in TEMPLATE_UNKNOWN_STATES:
            return
        else:
            _LOGGER.error(
                "%s: invalid template result: %s", self.entity_id, result
            )
            return
        _LOGGER.debug("%s: position %s from template", self.entity_id, position)
        # In TravelCalculator 0 is open, 100 is closed.
        self._tc.set_position(100 - position)
        self.async_write_ha_state()
