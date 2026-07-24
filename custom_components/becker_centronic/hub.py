"""Connection hub for the Becker Centronic USB stick.

Handles the serial (local USB) or TCP (ser2net) connection with automatic
reconnection, paces outgoing packets, parses incoming remote packets and
maintains the rolling increment counters via the unit store.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from random import randrange
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from serial_asyncio_fast import open_serial_connection

from .const import (
    ATTR_CHANNEL,
    ATTR_COMMAND,
    ATTR_UNIT,
    CONF_CONNECTION,
    CONF_DEVICE_PATH,
    CONNECTION_SOCKET,
    DEFAULT_PORT,
    EVENT_REMOTE_PACKET,
    SIGNAL_CONNECTION,
    SIGNAL_PACKET,
)
from .protocol import (
    CLEARPOS_SEQUENCE,
    DEFAULT_TRAIN_VARIANT,
    REMOVE_SEQUENCE,
    TRAIN_MASTER_SEQUENCE,
    TRAIN_SEQUENCES,
    Command,
    PacketParser,
    ReceivedPacket,
    encode_packet,
)
from .storage import UnitStore

_LOGGER = logging.getLogger(__name__)

# Minimum gap between transmitted packets. The stick/receivers drop or garble
# commands when packets are sent back-to-back (root cause of instability in
# upstream pybecker, which used a 0.01 s loop).
TX_GAP = 0.3
# Hold off transmissions briefly after RF receive traffic.
RX_HOLDOFF = 0.3

CONNECT_TIMEOUT = 10.0
RECONNECT_BACKOFF_MAX = 60.0
QUEUE_SIZE = 100
RECENT_PACKETS = 50


class CannotConnect(HomeAssistantError):
    """Raised when the connection to the stick cannot be established."""


async def async_open_connection(
    config: dict[str, Any],
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a stream connection to the stick (serial or TCP)."""
    if config.get(CONF_CONNECTION) == CONNECTION_SOCKET:
        host = config["host"]
        port = config.get("port", DEFAULT_PORT)
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                reader, writer = await asyncio.open_connection(host, port)
        except (TimeoutError, OSError) as err:
            raise CannotConnect(f"Cannot connect to {host}:{port}: {err}") from err
        _enable_keepalive(writer)
        return reader, writer

    device = config[CONF_DEVICE_PATH]
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            reader, writer = await open_serial_connection(url=device, baudrate=115200)
    except (TimeoutError, OSError, ValueError) as err:
        raise CannotConnect(f"Cannot open serial port {device}: {err}") from err
    return reader, writer


def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Enable TCP keepalive to detect half-dead ser2net connections."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Platform dependent fine tuning (Linux: TCP_KEEPIDLE, macOS: TCP_KEEPALIVE)
        for name, value in (
            ("TCP_KEEPIDLE", 30),
            ("TCP_KEEPALIVE", 30),
            ("TCP_KEEPINTVL", 10),
            ("TCP_KEEPCNT", 3),
        ):
            option = getattr(socket, name, None)
            if option is not None:
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.IPPROTO_TCP, option, value)


async def async_test_connection(config: dict[str, Any]) -> None:
    """Try to open and close a connection (used by the config flow)."""
    reader, writer = await async_open_connection(config)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


class BeckerHub:
    """Manages the connection to one Centronic USB stick."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: dict[str, Any],
        store: UnitStore,
    ) -> None:
        """Initialize the hub."""
        self._hass = hass
        self._entry_id = entry_id
        self._config = config
        self.store = store
        self._parser = PacketParser()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._runner: asyncio.Task | None = None
        self._closed = False
        self._connected = False
        self._next_tx_time = 0.0
        self.recent_packets: deque[dict[str, Any]] = deque(maxlen=RECENT_PACKETS)

    @property
    def connected(self) -> bool:
        """Return if the stick is currently connected."""
        return self._connected

    @property
    def signal_packet(self) -> str:
        """Dispatcher signal for received packets."""
        return SIGNAL_PACKET.format(self._entry_id)

    @property
    def signal_connection(self) -> str:
        """Dispatcher signal for connection state changes."""
        return SIGNAL_CONNECTION.format(self._entry_id)

    async def async_start(self) -> None:
        """Start the connection runner after an initial connect check."""
        # Fail setup fast if the stick is not reachable at all
        await async_test_connection(self._config)
        self._runner = self._hass.async_create_background_task(
            self._run(), name="becker_centronic connection runner"
        )

    async def async_stop(self) -> None:
        """Stop the hub and persist counters."""
        self._closed = True
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None
        await self.store.async_save_now()

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            _LOGGER.info("Connected to Becker Centronic stick")
        else:
            _LOGGER.warning("Connection to Becker Centronic stick lost")
        async_dispatcher_send(self._hass, self.signal_connection, connected)

    async def _run(self) -> None:
        """Run the connection loop: connect, pump data, reconnect on failure."""
        backoff = 1.0
        while not self._closed:
            try:
                reader, writer = await async_open_connection(self._config)
            except CannotConnect as err:
                self._set_connected(False)
                _LOGGER.debug("Connect failed (%s), retry in %.0f s", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
                continue

            backoff = 1.0
            self._parser.reset()
            self._set_connected(True)
            sender = self._hass.async_create_background_task(
                self._sender(writer), name="becker_centronic sender"
            )
            try:
                while True:
                    data = await reader.read(256)
                    if not data:
                        raise ConnectionResetError("Connection closed by peer")
                    self._hold_off_tx()
                    for packet in self._parser.feed(data):
                        self._handle_packet(packet)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - any I/O error triggers reconnect
                _LOGGER.debug("Connection error: %s", err)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            self._set_connected(False)
            await asyncio.sleep(1)

    def _hold_off_tx(self) -> None:
        loop_time = self._hass.loop.time()
        self._next_tx_time = max(self._next_tx_time, loop_time + RX_HOLDOFF)

    async def _sender(self, writer: asyncio.StreamWriter) -> None:
        """Send queued packets with enforced pacing."""
        while True:
            packet = await self._queue.get()
            try:
                while (delay := self._next_tx_time - self._hass.loop.time()) > 0:
                    await asyncio.sleep(delay)
                writer.write(packet)
                await writer.drain()
                self._next_tx_time = self._hass.loop.time() + TX_GAP
                _LOGGER.debug("Sent packet: %s", packet)
            finally:
                self._queue.task_done()

    def _handle_packet(self, packet: ReceivedPacket) -> None:
        """Dispatch a received remote packet."""
        _LOGGER.debug(
            "Received packet: unit_id: %s, channel: %s, command: %s, packet: %s",
            packet.unit_id,
            packet.channel,
            packet.command_name or f"0x{packet.command:02X}",
            packet.raw,
        )
        self._record_packet("rx", packet.unit_id, packet.channel, packet.command_name)
        async_dispatcher_send(self._hass, self.signal_packet, packet)
        event_data = {
            ATTR_UNIT: packet.unit_id,
            ATTR_CHANNEL: packet.channel,
            ATTR_COMMAND: packet.command_name or f"0x{packet.command:02X}",
        }
        self._hass.bus.async_fire(EVENT_REMOTE_PACKET, event_data)

    def _record_packet(
        self, direction: str, unit: Any, channel: Any, command: Any
    ) -> None:
        self.recent_packets.append(
            {
                "time": datetime.now(UTC).isoformat(),
                "direction": direction,
                "unit": unit,
                "channel": channel,
                "command": command,
            }
        )

    # ---------------------------------------------------------------- sending

    def _queue_command(self, unit: int, channel: int, command: Command) -> None:
        """Encode one command with the next increment and queue it."""
        increment = self.store.take_increment(unit)
        packet = encode_packet(self.store.unit_code(unit), increment, channel, command)
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull as err:
            raise HomeAssistantError(
                "Becker Centronic command queue is full; the stick seems unresponsive"
            ) from err
        self._record_packet("tx", unit, channel, command.name.lower())

    async def async_send_sequence(
        self, unit: int, channel: int, commands: Iterable[Command]
    ) -> None:
        """Queue a sequence of commands."""
        self._ensure_unit_seeded(unit)
        for command in commands:
            self._queue_command(unit, channel, command)

    def _ensure_unit_seeded(self, unit: int) -> None:
        if not self.store.is_configured(unit):
            _LOGGER.info("Initializing unit %s with a fresh increment", unit)
            self.store.seed_unit(unit)

    async def async_ensure_unit_synced(self, unit: int) -> None:
        """Initialize a fresh unit and send sync calls (needed for Roto covers).

        Ports the behavior of the previous integration: seed the increment and
        send 5 HALT commands with randomized delays so receivers adopt it.
        """
        if self.store.is_configured(unit):
            return
        self.store.seed_unit(unit)
        for attempt in range(1, 6):
            _LOGGER.debug("Unit %s init call #%d", unit, attempt)
            self._queue_command(unit, 1, Command.HALT)
            # 0.5 to 0.9 seconds between calls (works with Roto covers)
            await asyncio.sleep(randrange(5, 10) / 10)

    # Cover commands

    async def async_move_up(self, unit: int, channel: int) -> None:
        """Move the cover up."""
        await self.async_send_sequence(unit, channel, (Command.UP,))

    async def async_move_down(self, unit: int, channel: int) -> None:
        """Move the cover down."""
        await self.async_send_sequence(unit, channel, (Command.DOWN,))

    async def async_halt(self, unit: int, channel: int) -> None:
        """Stop the cover."""
        await self.async_send_sequence(unit, channel, (Command.HALT,))

    async def async_move_up_intermediate(self, unit: int, channel: int) -> None:
        """Move the cover to the upper intermediate (ventilation) position."""
        await self.async_send_sequence(unit, channel, (Command.UP_INTERMEDIATE,))

    async def async_move_down_intermediate(self, unit: int, channel: int) -> None:
        """Move the cover to the lower intermediate (sun protection) position."""
        await self.async_send_sequence(unit, channel, (Command.DOWN_INTERMEDIATE,))

    # Pairing and maintenance

    async def _async_wait_sent(self) -> None:
        """Wait until all queued packets have been transmitted."""
        try:
            async with asyncio.timeout(30):
                await self._queue.join()
        except TimeoutError as err:
            raise HomeAssistantError(
                "Timeout while sending commands; the stick seems disconnected"
            ) from err

    async def async_train(
        self, unit: int, channel: int, variant: str = DEFAULT_TRAIN_VARIANT
    ) -> None:
        """Send a TRAIN (pair) sequence while the receiver is in pairing mode."""
        sequence = TRAIN_SEQUENCES[variant]
        await self.async_send_sequence(unit, channel, sequence)
        self.store.set_configured(unit)
        # Wait until the sequence has actually been transmitted
        await self._async_wait_sent()

    async def async_unpair(self, unit: int, channel: int) -> None:
        """Send a REMOVE sequence while the receiver is in pairing mode."""
        await self.async_send_sequence(unit, channel, REMOVE_SEQUENCE)
        await self._async_wait_sent()

    async def async_train_master(self, unit: int, channel: int) -> None:
        """Register the stick as master sender (TRAINMASTER)."""
        await self.async_send_sequence(unit, channel, TRAIN_MASTER_SEQUENCE)
        self.store.set_configured(unit)
        await self._async_wait_sent()

    async def async_clear_position(self, unit: int, channel: int) -> None:
        """Send a CLEARPOS sequence (clears end positions in the receiver)."""
        await self.async_send_sequence(unit, channel, CLEARPOS_SEQUENCE)
        await self._async_wait_sent()

    async def async_resync(self, unit: int, channel: int) -> None:
        """Force the receiver to re-adopt the current increment.

        Repeating a command 3 times in a row makes the receiver accept the
        sender's current increment (documented in centronic TECHNICAL.md).
        """
        await self.async_send_sequence(
            unit, channel, (Command.HALT, Command.HALT, Command.HALT)
        )
