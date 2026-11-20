"""End-to-end setup test with a mocked stick connection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.becker_centronic.const import DOMAIN
from custom_components.becker_centronic.protocol import Command, encode_packet

SOCKET_DATA = {"connection": "socket", "host": "pi.local", "port": 5000}

COVER_SUBENTRY = ConfigSubentryData(
    data={
        "unit": 1,
        "channel": 2,
        "device_class": "shutter",
        "tilt_mode": "intermediate",
        "track_intermediate": True,
        "intermediate_position_up": 25,
        "intermediate_position_down": 75,
        "tilt_time_blind": 0.3,
        "travelling_time_up": 20.0,
        "travelling_time_down": 20.0,
    },
    subentry_id="coversub1",
    subentry_type="cover",
    title="Kitchen",
    unique_id="1:2",
)


@pytest.fixture
def mock_stick():
    """Mock the stream connection to the stick."""
    reader = asyncio.StreamReader()  # never fed -> read() stays pending
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.get_extra_info = MagicMock(return_value=None)
    with patch(
        "custom_components.becker_centronic.hub.async_open_connection",
        side_effect=lambda config: (reader, writer),
    ):
        yield reader, writer


async def _wait_for_write(writer, minimum_calls: int = 1) -> None:
    for _ in range(50):
        if writer.write.call_count >= minimum_calls:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"Expected {minimum_calls} writes, got {writer.write.call_count}"
    )


async def _setup_entry(
    hass: HomeAssistant,
    hass_storage: dict,
    channels: dict[str, int] | None = None,
    subentries: list[ConfigSubentryData] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=SOCKET_DATA,
        title="Stick",
        entry_id="test_stick",
        unique_id="pi.local:5000",
        subentries_data=subentries or [COVER_SUBENTRY],
    )
    # Pre-configure unit 1, with channel 2 in sync unless told otherwise
    hass_storage[f"{DOMAIN}.test_stick"] = {
        "version": 1,
        "data": {
            "units": {
                "1": {
                    "code": "1737B",
                    "increment": 100,
                    "configured": True,
                    "channels": {"2": 99} if channels is None else channels,
                },
            }
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_and_sends_commands(
    hass: HomeAssistant, hass_storage: dict, mock_stick
) -> None:
    """Entry setup creates cover + button entities and commands hit the wire."""
    _reader, writer = mock_stick
    entry = await _setup_entry(hass, hass_storage)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get("cover.kitchen")
    assert state is not None
    assert state.attributes["unit"] == 1
    assert state.attributes["channel"] == 2

    assert hass.states.get("button.kitchen_resync") is not None

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.kitchen"}, blocking=True
    )
    await _wait_for_write(writer)
    expected = encode_packet("1737B", 100, 2, Command.UP)
    assert writer.write.call_args.args[0] == expected

    assert hass.states.get("cover.kitchen").state == "opening"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_value_template_sets_position(
    hass: HomeAssistant, hass_storage: dict, mock_stick
) -> None:
    """A value template overrides the cover position."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=SOCKET_DATA,
        title="Stick",
        entry_id="test_stick",
        unique_id="pi.local:5000",
        subentries_data=[
            ConfigSubentryData(
                data={
                    "unit": 1,
                    "channel": 3,
                    "device_class": "window",
                    "tilt_mode": "none",
                    "track_intermediate": False,
                    "intermediate_position_up": 25,
                    "intermediate_position_down": 75,
                    "tilt_time_blind": 0.3,
                    "value_template": "{{ 42 }}",
                },
                subentry_id="coversub2",
                subentry_type="cover",
                title="Roof window",
                unique_id="1:3",
            )
        ],
    )
    hass_storage[f"{DOMAIN}.test_stick"] = {
        "version": 1,
        "data": {
            "units": {"1": {"code": "1737B", "increment": 5, "configured": True}}
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("cover.roof_window")
    assert state is not None
    assert state.attributes["current_position"] == 42


@pytest.fixture
def no_resync_delay():
    """Skip the 0.5-0.9 s pauses of the resync ritual."""
    with patch("custom_components.becker_centronic.hub.randrange", return_value=0):
        yield


async def test_resync_service(
    hass: HomeAssistant, hass_storage: dict, mock_stick, no_resync_delay
) -> None:
    """The resync entity service sends HALT five times."""
    _reader, writer = mock_stick
    await _setup_entry(hass, hass_storage)

    await hass.services.async_call(
        DOMAIN, "resync", {"entity_id": "cover.kitchen"}, blocking=True
    )
    await _wait_for_write(writer, minimum_calls=5)
    sent = [call.args[0] for call in writer.write.call_args_list]
    assert sent == [
        encode_packet("1737B", increment, 2, Command.HALT)
        for increment in range(100, 105)
    ]


@pytest.mark.parametrize("channels", [{"2": 40, "3": 99}, {}])
async def test_command_resyncs_receiver_first_when_out_of_window(
    hass: HomeAssistant, hass_storage: dict, mock_stick, no_resync_delay, channels
) -> None:
    """Sibling traffic (or unknown history) on a shared unit triggers a resync."""
    _reader, writer = mock_stick
    await _setup_entry(hass, hass_storage, channels=channels)

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.kitchen"}, blocking=True
    )
    await _wait_for_write(writer, minimum_calls=6)
    sent = [call.args[0] for call in writer.write.call_args_list]
    assert sent == [
        *(
            encode_packet("1737B", increment, 2, Command.HALT)
            for increment in range(100, 105)
        ),
        encode_packet("1737B", 105, 2, Command.UP),
    ]


def _record_writes(writer) -> list[tuple[float, bytes]]:
    loop = asyncio.get_running_loop()
    sent: list[tuple[float, bytes]] = []
    writer.write.side_effect = lambda packet: sent.append((loop.time(), packet))
    return sent


async def test_resync_halts_keep_quiet_air_towards_other_covers(
    hass: HomeAssistant, hass_storage: dict, mock_stick
) -> None:
    """Frames of other covers queued ahead do not squeeze a resync's HALTs on air."""
    _reader, writer = mock_stick
    sent = _record_writes(writer)
    office = ConfigSubentryData(
        data={**COVER_SUBENTRY["data"], "channel": 3},
        subentry_id="coversub3",
        subentry_type="cover",
        title="Office",
        unique_id="1:3",
    )
    await _setup_entry(
        hass,
        hass_storage,
        channels={"2": 40, "3": 99},
        subentries=[COVER_SUBENTRY, office],
    )

    with (
        patch("custom_components.becker_centronic.hub.TX_GAP", 0.05),
        patch("custom_components.becker_centronic.hub.randrange", return_value=2),
    ):
        # Two frames for the office cover are still waiting to be sent ...
        for _ in range(2):
            await hass.services.async_call(
                "cover", "stop_cover", {"entity_id": "cover.office"}, blocking=True
            )
        # ... when the kitchen cover needs a resync before its command
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": "cover.kitchen"}, blocking=True
        )
        await hass.services.async_call(
            "cover", "stop_cover", {"entity_id": "cover.office"}, blocking=True
        )
        await _wait_for_write(writer, minimum_calls=9)

    assert [packet for _, packet in sent] == [
        encode_packet("1737B", 100, 3, Command.HALT),
        encode_packet("1737B", 101, 3, Command.HALT),
        *(
            encode_packet("1737B", increment, 2, Command.HALT)
            for increment in range(102, 107)
        ),
        encode_packet("1737B", 107, 2, Command.UP),
        encode_packet("1737B", 108, 3, Command.HALT),
    ]
    gaps = [b - a for (a, _), (b, _) in zip(sent, sent[1:])]
    # randrange 2 -> 0.2 s of quiet air on both sides of every resync HALT,
    # towards the office cover's frame before the first one as well
    assert min(gaps[1:7]) >= 0.19
    # plain frames keep the plain gap
    assert max(gaps[0], gaps[7]) < 0.19


async def test_command_right_after_resync_follows_its_halts(
    hass: HomeAssistant, hass_storage: dict, mock_stick
) -> None:
    """A press during a resync goes on air after the HALTs, with quiet air."""
    _reader, writer = mock_stick
    sent = _record_writes(writer)
    await _setup_entry(hass, hass_storage)

    with (
        patch("custom_components.becker_centronic.hub.TX_GAP", 0.05),
        patch("custom_components.becker_centronic.hub.randrange", return_value=2),
    ):
        await hass.services.async_call(
            DOMAIN, "resync", {"entity_id": "cover.kitchen"}, blocking=True
        )
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": "cover.kitchen"}, blocking=True
        )
        await _wait_for_write(writer, minimum_calls=6)

    assert [packet for _, packet in sent] == [
        *(
            encode_packet("1737B", increment, 2, Command.HALT)
            for increment in range(100, 105)
        ),
        encode_packet("1737B", 105, 2, Command.UP),
    ]
    assert sent[-1][0] - sent[-2][0] >= 0.19


async def test_resync_button_follows_connection(
    hass: HomeAssistant, hass_storage: dict, mock_stick
) -> None:
    """The resync button becomes available once the stick connects."""
    await _setup_entry(hass, hass_storage)
    for _ in range(50):
        if hass.states.get("button.kitchen_resync").state != "unavailable":
            break
        await asyncio.sleep(0.05)
    assert hass.states.get("button.kitchen_resync").state != "unavailable"
