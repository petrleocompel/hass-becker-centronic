"""Tests for the pure protocol layer."""

import pytest

from custom_components.becker_centronic import protocol
from custom_components.becker_centronic.protocol import (
    Command,
    PacketParser,
    checksum,
    encode_code,
    encode_packet,
    next_increment,
    unit_code_for,
)


def reference_generate_code(channel, unit, cmd_code):
    """Reference implementation copied from ole1986/centronic-stick v0.9.

    Kept verbatim (modulo formatting) to guard against codec regressions.
    """

    def hex2(n):
        return "%02X" % (n & 0xFF)

    def hex4(n):
        return "%04X" % (n & 0xFFFF)

    def ref_checksum(code):
        assert len(code) == 40
        total = 0
        i = 0
        while i < 40:
            total += int(code[i] + code[i + 1], 16)
            i += 2
        return "%s%s" % (code.upper(), hex2(0x03 - total))  # noqa: UP031

    unit_id = unit[0]
    unit_inc = unit[1]
    if channel == 0:
        code = (
            "0000000002010B" + hex4(unit_inc) + "000000" + unit_id + "021"
            + "00" + hex2(channel) + "00" + hex2(cmd_code)
        )
    else:
        code = (
            "0000000002010B" + hex4(unit_inc) + "000000" + unit_id + "021"
            + "01" + hex2(channel) + "00" + hex2(cmd_code)
        )
    return ref_checksum(code)


@pytest.mark.parametrize("unit", [1, 2, 5])
@pytest.mark.parametrize("channel", [0, 1, 7, 15])
@pytest.mark.parametrize("increment", [0, 1, 825, 0xFFFF])
@pytest.mark.parametrize(
    "command",
    [Command.UP, Command.DOWN, Command.HALT, Command.RELEASE, Command.PAIR2],
)
def test_encode_matches_reference(unit, channel, increment, command):
    """Our codec must produce byte-identical codes to centronic-stick.py."""
    unit_code = unit_code_for(unit)
    expected = reference_generate_code(channel, [unit_code, increment], int(command))
    assert encode_code(unit_code, increment, channel, command) == expected


def test_checksum_length_and_value():
    """Checksum must be over 40 chars and wrap correctly."""
    code = "0000000002010B0000000000173770210101002040"[:40]
    result = checksum(code)
    assert len(result) == 42
    total = sum(int(result[i : i + 2], 16) for i in range(0, 42, 2))
    # Sum of all bytes including checksum must be 0x03 modulo 256
    assert total & 0xFF == 0x03

    with pytest.raises(ValueError):
        checksum("00")


def test_packet_framing():
    """Full packet must be framed with STX/ETX."""
    packet = encode_packet("1737B", 825, 1, Command.UP)
    assert packet.startswith(b"\x02")
    assert packet.endswith(b"\x03")
    assert len(packet) == 44  # STX + 42 chars + ETX


def test_parser_roundtrip():
    """Encoded packets must be parseable back to their fields."""
    parser = PacketParser()
    packet = encode_packet("1737C", 0x1234, 2, Command.UP_INTERMEDIATE)
    packets = parser.feed(packet)
    assert len(packets) == 1
    received = packets[0]
    assert received.unit_id == "1737C"
    assert received.channel == "2"
    assert received.command == 0x24
    assert received.command_name == "up_intermediate"
    assert received.increment == 0x1234
    assert received.remote_key == "1737C2"


def test_parser_handles_fragmentation_and_garbage():
    """Parser must survive partial reads and garbage between packets."""
    parser = PacketParser()
    p1 = encode_packet("12345", 10, 1, Command.UP)
    p2 = encode_packet("12345", 11, 1, Command.HALT)
    stream = b"garbage" + p1 + b"\x00\xff" + p2
    # Feed in small chunks
    received = []
    for i in range(0, len(stream), 7):
        received.extend(parser.feed(stream[i : i + 7]))
    assert [p.command for p in received] == [0x20, 0x10]


def test_parser_buffer_capped():
    """Garbage must not grow the parser buffer unboundedly."""
    parser = PacketParser()
    for _ in range(100):
        parser.feed(b"\x00" * 1000)
    assert len(parser._buffer) <= 4096


def test_command_name_train_variants():
    """All 0x8x commands map to train."""
    packet = encode_packet("12345", 1, 1, Command.PAIR2)
    received = PacketParser().feed(packet)[0]
    assert received.command_name == "train"


def test_next_increment_wraps():
    """Increment wraps at 0xFFFF as documented in TECHNICAL.md."""
    assert next_increment(0) == 1
    assert next_increment(0xFFFF) == 0
    assert next_increment(0xFFFE, 3) == 1


def test_train_sequences():
    """All three known TRAIN variants must be available."""
    assert set(protocol.TRAIN_SEQUENCES) == {
        "double_tap",
        "press_release",
        "double_press",
    }
    assert protocol.TRAIN_SEQUENCES["double_tap"] == (
        Command.PAIR,
        Command.PAIR2,
        Command.RELEASE,
        Command.PAIR,
        Command.PAIR2,
        Command.RELEASE,
    )
    assert protocol.DEFAULT_TRAIN_VARIANT in protocol.TRAIN_SEQUENCES


def test_unit_codes():
    """Unit numbers 1-5 map to the fixed stick unit codes."""
    assert unit_code_for(1) == "1737B"
    assert unit_code_for(5) == "1737F"
    with pytest.raises(ValueError):
        unit_code_for(6)
    with pytest.raises(ValueError):
        unit_code_for(0)
