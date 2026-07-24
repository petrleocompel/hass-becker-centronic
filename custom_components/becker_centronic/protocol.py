"""Pure protocol implementation for the Becker Centronic USB stick.

The wire format (reverse engineered, see centronic-stick TECHNICAL.md):

<STX> 0000000002010B IIII 000000 UUUUU 021 RR CC 00 KK SS <ETX>

* IIII  - rolling increment (hex4)
* UUUUU - unit code (5 hex chars, stick emulates units 1737B-1737F)
* RR    - "01" for remote emulation, "00" for wall-mounted sender (channel 0)
* CC    - channel (hex2, 1-7, 15/0x0F = broadcast)
* KK    - command byte (hex2)
* SS    - checksum over the 40 preceding chars
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

STX = b"\x02"
ETX = b"\x03"

CODE_PREFIX = "0000000002010B"  # constant preamble, required for RF transmission
CODE_SUFFIX = "000000"
CODE_21 = "021"
CODE_REMOTE = "01"  # Centronic remotes send "02", CentralControl uses "01"

# The stick emulates 5 sender units with fixed unit codes
UNIT_CODES = ("1737B", "1737C", "1737D", "1737E", "1737F")
NUM_UNITS = len(UNIT_CODES)

BROADCAST_CHANNEL = 15
VALID_CHANNELS = tuple(range(1, 8))

INCREMENT_MAX = 0xFFFF
# The receiver accepts the next increment only within +49 of the last seen one
INCREMENT_WINDOW = 49

DEFAULT_DEVICE_NAME = (
    "/dev/serial/by-id/usb-BECKER-ANTRIEBE_GmbH_CDC_RS232_v125_Centronic-if00"
)


class Command(IntEnum):
    """Command bytes understood by Centronic receivers."""

    RELEASE = 0x00  # button release
    HALT = 0x10
    UP = 0x20
    UP_INTERMEDIATE = 0x24  # intermediate position "up" (ventilation)
    DOWN = 0x40
    DOWN_INTERMEDIATE = 0x44  # intermediate position "down" (sun protection)
    PAIR = 0x80  # program button press
    PAIR2 = 0x81  # program button held for 3 seconds
    PAIR3 = 0x82  # program button held for 6 seconds
    PAIR4 = 0x83  # program button held for 10 seconds
    CLEARPOS = 0x90
    CLEARPOS2 = 0x91
    CLEARPOS3 = 0x92
    CLEARPOS4 = 0x93


# Three TRAIN variants exist in the wild; receivers differ in what they accept.
# "double_tap" is the sequence proven with the previous Home Assistant
# component, "press_release" is what centronic-stick.py uses and
# "double_press" is upstream pybecker's variant.
TRAIN_SEQUENCES: dict[str, tuple[Command, ...]] = {
    "double_tap": (
        Command.PAIR,
        Command.PAIR2,
        Command.RELEASE,
        Command.PAIR,
        Command.PAIR2,
        Command.RELEASE,
    ),
    "press_release": (Command.PAIR2, Command.RELEASE, Command.PAIR2),
    "double_press": (Command.PAIR2, Command.PAIR2),
}
DEFAULT_TRAIN_VARIANT = "double_tap"

REMOVE_SEQUENCE: tuple[Command, ...] = (
    Command.PAIR2,
    Command.RELEASE,
    Command.PAIR2,
    Command.PAIR3,
    Command.PAIR4,
)

TRAIN_MASTER_SEQUENCE: tuple[Command, ...] = (
    Command.PAIR,
    Command.PAIR2,
    Command.PAIR3,
    Command.PAIR4,
)

CLEARPOS_SEQUENCE: tuple[Command, ...] = (
    Command.PAIR,
    Command.CLEARPOS,
    Command.CLEARPOS2,
    Command.CLEARPOS3,
    Command.CLEARPOS4,
)

# Received packets from remotes; the command byte is split into a command
# nibble and an argument nibble (e.g. UP_INTERMEDIATE 0x24 -> command 2, arg 4)
PACKET_RE = re.compile(
    STX
    + CODE_PREFIX.encode()
    + rb"(?P<increment>[0-9A-F]{4})"
    + CODE_SUFFIX.encode()
    + rb"(?P<unit_id>[0-9A-F]{5})"
    + rb"[0-9A-F]{6}"
    + rb"(?P<channel>[0-9A-F])"
    + rb"00"
    + rb"(?P<command>[0-9A-F])"
    + rb"(?P<argument>[0-9A-F])"
    + rb"[0-9A-F]{2}"
    + ETX,
    re.I,
)

_COMMAND_NAMES = {
    0x00: "release",
    0x10: "halt",
    0x20: "up",
    0x24: "up_intermediate",
    0x40: "down",
    0x44: "down_intermediate",
    0x80: "train",
}

_MAX_BUFFER = 4096


def hex2(value: int) -> str:
    """Format a byte as 2 uppercase hex chars."""
    return "%02X" % (value & 0xFF)


def hex4(value: int) -> str:
    """Format a word as 4 uppercase hex chars."""
    return "%04X" % (value & 0xFFFF)


def checksum(code: str) -> str:
    """Append the checksum to a 40 char code."""
    if len(code) != 40:
        raise ValueError("The code must be 40 characters long")
    code_sum = sum(int(code[i : i + 2], 16) for i in range(0, 40, 2))
    return f"{code.upper()}{hex2(0x03 - code_sum)}"


def encode_code(
    unit_code: str, increment: int, channel: int, command: int
) -> str:
    """Build the 42 char code (payload + checksum) for one command."""
    if channel == 0:
        # channel 0 emulates a wall-mounted (master) sender
        code = (
            CODE_PREFIX
            + hex4(increment)
            + CODE_SUFFIX
            + unit_code.upper()
            + CODE_21
            + "00"
            + hex2(channel)
            + "00"
            + hex2(command)
        )
    else:
        code = (
            CODE_PREFIX
            + hex4(increment)
            + CODE_SUFFIX
            + unit_code.upper()
            + CODE_21
            + CODE_REMOTE
            + hex2(channel)
            + "00"
            + hex2(command)
        )
    return checksum(code)


def encode_packet(
    unit_code: str, increment: int, channel: int, command: int
) -> bytes:
    """Build a full framed packet ready to be written to the stick."""
    return STX + encode_code(unit_code, increment, channel, command).encode() + ETX


@dataclass(frozen=True, slots=True)
class ReceivedPacket:
    """A packet received from the stick (i.e. sent by a remote)."""

    unit_id: str  # 5 hex chars, uppercase
    channel: str  # 1 hex char, uppercase; "F" = all channels of a remote
    command: int  # full command byte, e.g. 0x24
    increment: int
    raw: bytes

    @property
    def command_name(self) -> str | None:
        """Return a friendly command name, if known."""
        name = _COMMAND_NAMES.get(self.command)
        if name is None and (self.command & 0xF0) == 0x80:
            return "train"
        return name

    @property
    def remote_key(self) -> str:
        """Key used to match configured remote IDs (unit_id + channel)."""
        return f"{self.unit_id}{self.channel}"


def _packet_from_match(match: re.Match[bytes]) -> ReceivedPacket:
    return ReceivedPacket(
        unit_id=match.group("unit_id").decode().upper(),
        channel=match.group("channel").decode().upper(),
        command=int((match.group("command") + match.group("argument")).decode(), 16),
        increment=int(match.group("increment").decode(), 16),
        raw=match.group(0),
    )


class PacketParser:
    """Incremental parser for the byte stream coming from the stick."""

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buffer = b""

    def feed(self, data: bytes) -> list[ReceivedPacket]:
        """Feed received bytes, return all complete packets."""
        self._buffer += data
        packets = []
        end = 0
        for match in PACKET_RE.finditer(self._buffer):
            packets.append(_packet_from_match(match))
            end = match.end()
        self._buffer = self._buffer[end:]
        # Don't let garbage grow the buffer indefinitely
        if len(self._buffer) > _MAX_BUFFER:
            self._buffer = self._buffer[-_MAX_BUFFER:]
        return packets

    def reset(self) -> None:
        """Drop any buffered partial data (e.g. after reconnect)."""
        self._buffer = b""


def next_increment(current: int, step: int = 1) -> int:
    """Return the next increment value, wrapping at 0xFFFF."""
    return (current + step) & INCREMENT_MAX


def unit_code_for(unit: int) -> str:
    """Return the stick's unit code for unit number 1-5."""
    if not 1 <= unit <= NUM_UNITS:
        raise ValueError(f"Unit must be in range 1-{NUM_UNITS}")
    return UNIT_CODES[unit - 1]
