"""Config flow for the Becker Centronic integration."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
)
from serial.tools import list_ports

from .const import (
    CONF_CHANNEL,
    CONF_CONNECTION,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_PATH,
    CONF_INTERMEDIATE_POSITION_DOWN,
    CONF_INTERMEDIATE_POSITION_UP,
    CONF_LEGACY_DB_PATH,
    CONF_REMOTE_IDS,
    CONF_TILT_MODE,
    CONF_TILT_TIME_BLIND,
    CONF_TRACK_INTERMEDIATE,
    CONF_TRAVELLING_TIME_DOWN,
    CONF_TRAVELLING_TIME_UP,
    CONF_UNIT,
    CONF_VALUE_TEMPLATE,
    CONNECTION_SERIAL,
    CONNECTION_SOCKET,
    DEFAULT_INTERMEDIATE_POSITION_DOWN,
    DEFAULT_INTERMEDIATE_POSITION_UP,
    DEFAULT_PORT,
    DEFAULT_TILT_TIME_BLIND,
    DOMAIN,
    SUBENTRY_TYPE_COVER,
    TILT_MODE_BLIND,
    TILT_MODE_INTERMEDIATE,
    TILT_MODE_NONE,
)
from .hub import CannotConnect, async_test_connection
from .protocol import NUM_UNITS, TRAIN_SEQUENCES, VALID_CHANNELS

_LOGGER = logging.getLogger(__name__)

LEGACY_DB_FILENAME = "centronic-stick.db"

REMOTE_ID_RE = re.compile(r"^[0-9A-Fa-f]{5}:[0-9A-Fa-f]$")

DEVICE_CLASSES = ["shutter", "blind", "awning", "window", "shade"]

TRAIN_VARIANT_ORDER = list(TRAIN_SEQUENCES)


def _find_serial_ports() -> list[SelectOptionDict]:
    """Enumerate serial ports, preferring stable /dev/serial/by-id paths."""
    by_id_dir = "/dev/serial/by-id"
    by_id: dict[str, str] = {}
    if os.path.isdir(by_id_dir):
        for name in os.listdir(by_id_dir):
            path = os.path.join(by_id_dir, name)
            by_id[os.path.realpath(path)] = path

    options: list[SelectOptionDict] = []
    for port in list_ports.comports():
        device = by_id.get(os.path.realpath(port.device), port.device)
        label = f"{device} ({port.description})" if port.description else device
        options.append(SelectOptionDict(value=device, label=label))
    # Also offer by-id paths that pyserial did not report
    known = {option["value"] for option in options}
    options.extend(
        SelectOptionDict(value=path, label=path)
        for path in by_id.values()
        if path not in known
    )
    return options


def _default_port(options: list[SelectOptionDict]) -> str | None:
    """Preselect the Becker stick if it can be identified."""
    for option in options:
        label = option["label"].upper()
        if "BECKER" in label or "CENTRONIC" in label:
            return option["value"]
    return None


class BeckerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for a Centronic USB stick."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._connection_data: dict[str, Any] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry flow types."""
        return {SUBENTRY_TYPE_COVER: CoverSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select how the stick is connected."""
        return self.async_show_menu(
            step_id="user", menu_options=[CONNECTION_SERIAL, CONNECTION_SOCKET]
        )

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a locally attached USB stick."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_CONNECTION: CONNECTION_SERIAL,
                CONF_DEVICE_PATH: user_input[CONF_DEVICE_PATH].strip(),
            }
            try:
                await async_test_connection(data)
            except CannotConnect as err:
                _LOGGER.debug("Serial connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_connection(
                    data, data[CONF_DEVICE_PATH]
                )

        options = await self.hass.async_add_executor_job(_find_serial_ports)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_PATH,
                    default=_default_port(options) or vol.UNDEFINED,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="serial", data_schema=schema, errors=errors
        )

    async def async_step_socket(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a stick reachable over the network (ser2net)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_CONNECTION: CONNECTION_SOCKET,
                "host": user_input["host"].strip(),
                "port": int(user_input["port"]),
            }
            try:
                await async_test_connection(data)
            except CannotConnect as err:
                _LOGGER.debug("Socket connection test failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_connection(
                    data, f"{data['host']}:{data['port']}"
                )

        schema = vol.Schema(
            {
                vol.Required("host"): TextSelector(),
                vol.Required("port", default=DEFAULT_PORT): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="socket", data_schema=schema, errors=errors
        )

    async def _async_finish_connection(
        self, data: dict[str, Any], unique_id: str
    ) -> ConfigFlowResult:
        """Handle a verified connection: reconfigure, import step or create."""
        if self.source == "reconfigure":
            entry = self._get_reconfigure_entry()
            return self.async_update_reload_and_abort(
                entry, data={**entry.data, **data}, unique_id=unique_id
            )

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        self._connection_data = data
        legacy_db = os.path.join(self.hass.config.config_dir, LEGACY_DB_FILENAME)
        if await self.hass.async_add_executor_job(os.path.isfile, legacy_db):
            return await self.async_step_import_db()
        return self._async_create_stick_entry()

    async def async_step_import_db(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to import increments from an existing centronic-stick.db."""
        if user_input is not None:
            if user_input["import_increments"]:
                self._connection_data[CONF_LEGACY_DB_PATH] = os.path.join(
                    self.hass.config.config_dir, LEGACY_DB_FILENAME
                )
            return self._async_create_stick_entry()

        return self.async_show_form(
            step_id="import_db",
            data_schema=vol.Schema(
                {vol.Required("import_increments", default=True): BooleanSelector()}
            ),
        )

    @callback
    def _async_create_stick_entry(self) -> ConfigFlowResult:
        if self._connection_data[CONF_CONNECTION] == CONNECTION_SOCKET:
            title = f"Centronic stick ({self._connection_data['host']})"
        else:
            title = "Centronic stick"
        return self.async_create_entry(title=title, data=self._connection_data)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure the connection to the stick."""
        return self.async_show_menu(
            step_id="reconfigure", menu_options=[CONNECTION_SERIAL, CONNECTION_SOCKET]
        )


class CoverSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a cover, including the guided pairing wizard."""

    def __init__(self) -> None:
        """Initialize the subentry flow."""
        self._data: dict[str, Any] = {}
        self._title: str = ""
        self._train_variant_index = 0

    @property
    def _hub(self):
        return self._get_entry().runtime_data

    def _used_channels(self) -> set[tuple[int, int]]:
        """Return (unit, channel) pairs already used by other covers."""
        return {
            (subentry.data[CONF_UNIT], subentry.data[CONF_CHANNEL])
            for subentry in self._get_entry().subentries.values()
            if subentry.subentry_type == SUBENTRY_TYPE_COVER
        }

    def _suggested_unit_channel(self) -> tuple[int, int]:
        """Suggest the next free unit/channel combination."""
        used = self._used_channels()
        for unit in range(1, NUM_UNITS + 1):
            for channel in VALID_CHANNELS:
                if (unit, channel) not in used:
                    return unit, channel
        return 1, 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """First step: name, unit/channel, device type."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        errors: dict[str, str] = {}
        if user_input is not None:
            unit = int(user_input[CONF_UNIT])
            channel = int(user_input[CONF_CHANNEL])
            if (unit, channel) in self._used_channels():
                errors["base"] = "channel_in_use"
            else:
                self._title = user_input["name"]
                self._data = {
                    CONF_UNIT: unit,
                    CONF_CHANNEL: channel,
                    CONF_DEVICE_CLASS: user_input[CONF_DEVICE_CLASS],
                }
                if user_input["already_paired"]:
                    return await self.async_step_settings()
                return await self.async_step_pair()

        suggested_unit, suggested_channel = self._suggested_unit_channel()
        used = self._used_channels()
        unit_options = [
            SelectOptionDict(value=str(unit), label=f"Unit {unit}")
            for unit in range(1, NUM_UNITS + 1)
        ]
        channel_options = [
            SelectOptionDict(value=str(channel), label=f"Channel {channel}")
            for channel in VALID_CHANNELS
        ]
        schema = vol.Schema(
            {
                vol.Required("name"): TextSelector(),
                vol.Required(
                    CONF_UNIT, default=str(suggested_unit)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=unit_options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    CONF_CHANNEL, default=str(suggested_channel)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=channel_options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    CONF_DEVICE_CLASS, default="shutter"
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=DEVICE_CLASSES,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="device_class",
                    )
                ),
                vol.Required("already_paired", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "used_channels": ", ".join(
                    f"{unit}:{channel}" for unit, channel in sorted(used)
                )
                or "-"
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Instruct the user and send the TRAIN sequence."""
        if user_input is not None:
            await self._async_send_train()
            return await self.async_step_pair_confirm()

        variant = TRAIN_VARIANT_ORDER[self._train_variant_index]
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            description_placeholders=self._pair_placeholders(variant),
        )

    async def _async_send_train(self) -> None:
        variant = TRAIN_VARIANT_ORDER[self._train_variant_index]
        _LOGGER.info(
            "Sending TRAIN (%s) to unit %s channel %s",
            variant,
            self._data[CONF_UNIT],
            self._data[CONF_CHANNEL],
        )
        await self._hub.async_train(
            self._data[CONF_UNIT], self._data[CONF_CHANNEL], variant
        )

    def _pair_placeholders(self, variant: str) -> dict[str, str]:
        return {
            "unit": str(self._data[CONF_UNIT]),
            "channel": str(self._data[CONF_CHANNEL]),
            "variant": variant,
        }

    async def async_step_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask whether pairing was confirmed by the cover."""
        return self.async_show_menu(
            step_id="pair_confirm",
            menu_options=["pair_done", "pair_retry", "pair_retry_alt"],
            description_placeholders=self._pair_placeholders(
                TRAIN_VARIANT_ORDER[self._train_variant_index]
            ),
        )

    async def async_step_pair_done(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pairing confirmed, continue with settings."""
        return await self.async_step_settings()

    async def async_step_pair_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Retry pairing with the same sequence variant."""
        return await self.async_step_pair()

    async def async_step_pair_retry_alt(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Retry pairing with the next sequence variant."""
        self._train_variant_index = (self._train_variant_index + 1) % len(
            TRAIN_VARIANT_ORDER
        )
        return await self.async_step_pair()

    def _settings_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_TRAVELLING_TIME_UP): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(CONF_TRAVELLING_TIME_DOWN): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_TILT_MODE, default=TILT_MODE_INTERMEDIATE
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            TILT_MODE_NONE,
                            TILT_MODE_INTERMEDIATE,
                            TILT_MODE_BLIND,
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="tilt_mode",
                    )
                ),
                vol.Required(
                    CONF_TRACK_INTERMEDIATE, default=True
                ): BooleanSelector(),
                vol.Required(
                    CONF_INTERMEDIATE_POSITION_UP,
                    default=DEFAULT_INTERMEDIATE_POSITION_UP,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_INTERMEDIATE_POSITION_DOWN,
                    default=DEFAULT_INTERMEDIATE_POSITION_DOWN,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=100, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_TILT_TIME_BLIND, default=DEFAULT_TILT_TIME_BLIND
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.1,
                        max=5,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(CONF_REMOTE_IDS): TextSelector(),
                vol.Optional(CONF_VALUE_TEMPLATE): TemplateSelector(),
            }
        )

    def _validate_settings(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any]:
        """Validate and normalize the settings form."""
        settings: dict[str, Any] = {
            CONF_TILT_MODE: user_input[CONF_TILT_MODE],
            CONF_TRACK_INTERMEDIATE: user_input[CONF_TRACK_INTERMEDIATE],
            CONF_INTERMEDIATE_POSITION_UP: int(
                user_input[CONF_INTERMEDIATE_POSITION_UP]
            ),
            CONF_INTERMEDIATE_POSITION_DOWN: int(
                user_input[CONF_INTERMEDIATE_POSITION_DOWN]
            ),
            CONF_TILT_TIME_BLIND: float(user_input[CONF_TILT_TIME_BLIND]),
        }
        for key in (CONF_TRAVELLING_TIME_UP, CONF_TRAVELLING_TIME_DOWN):
            if user_input.get(key) is not None:
                settings[key] = float(user_input[key])

        remote_ids: list[str] = []
        raw_remote_ids = (user_input.get(CONF_REMOTE_IDS) or "").strip()
        if raw_remote_ids:
            for part in raw_remote_ids.split(","):
                part = part.strip().upper()
                if not REMOTE_ID_RE.match(part):
                    errors[CONF_REMOTE_IDS] = "invalid_remote_id"
                    break
                remote_ids.append(part)
        if remote_ids:
            settings[CONF_REMOTE_IDS] = ",".join(remote_ids)

        template = (user_input.get(CONF_VALUE_TEMPLATE) or "").strip()
        if template:
            settings[CONF_VALUE_TEMPLATE] = template
        return settings

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Cover behavior settings, then create the subentry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            settings = self._validate_settings(user_input, errors)
            if not errors:
                data = {**self._data, **settings}
                return self.async_create_entry(
                    title=self._title,
                    data=data,
                    unique_id=f"{data[CONF_UNIT]}:{data[CONF_CHANNEL]}",
                )

        return self.async_show_form(
            step_id="settings", data_schema=self._settings_schema(), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure the settings of an existing cover."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            settings = self._validate_settings(user_input, errors)
            if not errors:
                data = {
                    CONF_UNIT: subentry.data[CONF_UNIT],
                    CONF_CHANNEL: subentry.data[CONF_CHANNEL],
                    CONF_DEVICE_CLASS: subentry.data[CONF_DEVICE_CLASS],
                    **settings,
                }
                return self.async_update_and_abort(
                    self._get_entry(), subentry, data=data
                )

        schema = self.add_suggested_values_to_schema(
            self._settings_schema(), dict(subentry.data)
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
