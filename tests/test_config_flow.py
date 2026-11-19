"""Tests for the config and subentry flows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.becker_centronic.const import DOMAIN
from custom_components.becker_centronic.hub import CannotConnect

SOCKET_DATA = {"connection": "socket", "host": "pi.local", "port": 5000}


@pytest.fixture
def mock_connection():
    """Mock a successful connection test."""
    with patch(
        "custom_components.becker_centronic.config_flow.async_test_connection",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


async def test_socket_flow(hass: HomeAssistant, mock_connection) -> None:
    """Full happy path for a network-connected stick."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "socket"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "socket"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "pi.local", "port": 5000}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == SOCKET_DATA
    assert result["result"].unique_id == "pi.local:5000"


async def test_serial_flow(hass: HomeAssistant, mock_connection) -> None:
    """Full happy path for a locally connected stick."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(
        "custom_components.becker_centronic.config_flow._find_serial_ports",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "serial"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "serial"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_path": "/dev/serial/by-id/becker"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "connection": "serial",
        "device_path": "/dev/serial/by-id/becker",
    }


async def test_socket_flow_cannot_connect(
    hass: HomeAssistant, mock_connection
) -> None:
    """Connection errors show a form error and allow retrying."""
    mock_connection.side_effect = CannotConnect("nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "socket"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "pi.local", "port": 5000}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_connection.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "pi.local", "port": 5000}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_stick_aborts(hass: HomeAssistant, mock_connection) -> None:
    """The same stick cannot be added twice."""
    MockConfigEntry(
        domain=DOMAIN, data=SOCKET_DATA, unique_id="pi.local:5000"
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "socket"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "pi.local", "port": 5000}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _setup_stick_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create a loaded stick entry with a mocked setup."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=SOCKET_DATA, unique_id="pi.local:5000", title="Stick"
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.becker_centronic.async_setup_entry", return_value=True
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    return entry


COVER_USER_INPUT = {
    "name": "Kitchen",
    "unit": "1",
    "channel": "1",
    "device_class": "shutter",
    "already_paired": True,
}

COVER_SETTINGS_INPUT = {
    "travelling_time_up": 30.0,
    "tilt_mode": "intermediate",
    "track_intermediate": True,
    "intermediate_position_up": 25,
    "intermediate_position_down": 75,
    "tilt_time_blind": 0.3,
    "remote_ids": "12345:2",
}


async def test_add_cover_already_paired(hass: HomeAssistant) -> None:
    """Add a cover that is already paired (skips the pairing wizard)."""
    entry = await _setup_stick_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_SETTINGS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    subentry = next(iter(entry.subentries.values()))
    assert subentry.title == "Kitchen"
    assert subentry.unique_id == "1:1"
    assert subentry.data["unit"] == 1
    assert subentry.data["channel"] == 1
    assert subentry.data["travelling_time_up"] == 30.0
    assert subentry.data["remote_ids"] == "12345:2"
    assert "travelling_time_down" not in subentry.data


async def test_add_cover_with_pairing(hass: HomeAssistant) -> None:
    """Add a cover through the pairing wizard, including a variant retry."""
    entry = await _setup_stick_entry(hass)
    hub = MagicMock()
    hub.async_train = AsyncMock()
    entry.runtime_data = hub

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_USER_INPUT, "already_paired": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"

    # Submit -> TRAIN is sent with the default variant
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pair_confirm"
    hub.async_train.assert_awaited_once_with(1, 1, "double_tap")

    # Retry with the alternative sequence
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pair_retry_alt"}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    assert result["step_id"] == "pair_confirm"
    assert hub.async_train.await_count == 2
    assert hub.async_train.await_args.args == (1, 1, "press_release")

    # Confirm and finish
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pair_done"}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_SETTINGS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_add_cover_channel_in_use(hass: HomeAssistant) -> None:
    """Adding a second cover on the same unit/channel shows an error."""
    entry = await _setup_stick_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_USER_INPUT
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_SETTINGS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_USER_INPUT, "name": "Duplicate"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "channel_in_use"}


async def test_add_cover_invalid_remote_id(hass: HomeAssistant) -> None:
    """Invalid remote IDs are rejected with a field error."""
    entry = await _setup_stick_entry(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_USER_INPUT
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_SETTINGS_INPUT, "remote_ids": "not-valid"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"remote_ids": "invalid_remote_id"}


def _schema_defaults(result) -> dict:
    return {
        str(key): key.default()
        for key in result["data_schema"].schema
        if callable(key.default)
    }


async def _add_cover(hass, entry, unit: str, channel: str, name: str) -> None:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**COVER_USER_INPUT, "name": name, "unit": unit, "channel": channel},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COVER_SETTINGS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_suggests_a_unit_of_its_own(hass: HomeAssistant) -> None:
    """Covers get their own unit while one is free, then free channels."""
    entry = await _setup_stick_entry(hass)
    for unit in ("1", "2", "3", "4", "5"):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
        )
        defaults = _schema_defaults(result)
        assert (defaults["unit"], defaults["channel"]) == (unit, "1")
        await _add_cover(hass, entry, unit, "1", f"Cover {unit}")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "cover"), context={"source": config_entries.SOURCE_USER}
    )
    defaults = _schema_defaults(result)
    assert (defaults["unit"], defaults["channel"]) == ("1", "2")


async def test_reconfigure_settings(hass: HomeAssistant) -> None:
    """The settings form is still reachable from the reconfigure menu."""
    entry = await _setup_stick_entry(hass)
    await _add_cover(hass, entry, "1", "1", "Kitchen")
    subentry = next(iter(entry.subentries.values()))

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "reconfigure_settings"}
    )
    assert result["step_id"] == "reconfigure_settings"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**COVER_SETTINGS_INPUT, "travelling_time_up": 12.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.subentries[subentry.subentry_id].data["travelling_time_up"] == 12.0


async def test_repair_keeps_the_subentry(hass: HomeAssistant) -> None:
    """Pairing again from reconfigure updates the same subentry in place."""
    entry = await _setup_stick_entry(hass)
    await _add_cover(hass, entry, "1", "1", "Kitchen")
    await _add_cover(hass, entry, "1", "2", "Office")
    subentry = next(s for s in entry.subentries.values() if s.title == "Office")
    hub = MagicMock()
    hub.async_train = AsyncMock()
    entry.runtime_data = hub

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "repair"}
    )
    assert result["step_id"] == "repair"
    assert _schema_defaults(result) == {"unit": "1", "channel": "2"}

    # Another cover's unit/channel is refused
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"unit": "1", "channel": "1"}
    )
    assert result["errors"] == {"base": "channel_in_use"}

    # Move the cover to a unit of its own
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"unit": "2", "channel": "1"}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {}
    )
    hub.async_train.assert_awaited_once_with(2, 1, "double_tap")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "pair_done"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    updated = entry.subentries[subentry.subentry_id]
    assert updated.title == "Office"
    assert updated.unique_id == "2:1"
    assert updated.data == {**subentry.data, "unit": 2, "channel": 1}
