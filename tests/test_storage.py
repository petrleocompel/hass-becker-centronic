"""Tests for the unit increment store."""

import sqlite3

from homeassistant.core import HomeAssistant

from custom_components.becker_centronic.storage import UnitStore


async def test_defaults(hass: HomeAssistant) -> None:
    """Fresh store initializes all 5 units unconfigured."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    for unit in range(1, 6):
        assert not store.is_configured(unit)
        assert store.current_increment(unit) == 0
    assert store.unit_code(1) == "1737B"
    assert store.unit_code(5) == "1737F"


async def test_take_increment_advances_and_wraps(hass: HomeAssistant) -> None:
    """take_increment returns the current value and advances the counter."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    assert store.take_increment(1, 1) == 0
    assert store.take_increment(1, 1) == 1
    assert store.current_increment(1) == 2

    store._units[2]["increment"] = 0xFFFF
    assert store.take_increment(2, 1) == 0xFFFF
    assert store.current_increment(2) == 0


async def test_channel_gap_counts_sibling_frames(hass: HomeAssistant) -> None:
    """The gap of a channel grows with every frame sent on its siblings."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    assert store.channel_gap(1, 4) is None
    store.take_increment(1, 4)
    assert store.channel_gap(1, 4) == 1
    for _ in range(49):
        store.take_increment(1, 1)
    assert store.channel_gap(1, 4) == 50
    assert store.channel_gap(1, 1) == 1

    store._units[1]["increment"] = 2
    store._units[1]["channels"]["4"] = 0xFFFE
    assert store.channel_gap(1, 4) == 4


async def test_seed_unit(hass: HomeAssistant) -> None:
    """Seeding marks the unit configured with a random increment 10-39."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    store.seed_unit(3)
    assert store.is_configured(3)
    assert 10 <= store.current_increment(3) < 40


async def test_import_sqlite(hass: HomeAssistant, tmp_path) -> None:
    """Import counters from a legacy centronic-stick.db."""
    db_path = tmp_path / "centronic-stick.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE unit (code NVARCHAR(5), increment INTEGER(4), "
        "configured BIT, executed INTEGER, UNIQUE(code))"
    )
    rows = [
        ("1737b", 825, 1, 0),
        ("1737c", 0, 0, 0),
        ("1737d", 42, 1, 0),
        ("1737e", 0, 0, 0),
        ("1737f", 0, 0, 0),
    ]
    conn.executemany("INSERT INTO unit VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    store = UnitStore(hass, "test_entry")
    await store.async_load()
    assert await store.async_import_sqlite(hass, str(db_path))

    assert store.is_configured(1)
    assert store.current_increment(1) == 825
    assert not store.is_configured(2)
    assert store.is_configured(3)
    assert store.current_increment(3) == 42


async def test_import_sqlite_missing_file(hass: HomeAssistant, tmp_path) -> None:
    """Import of a missing/broken file fails gracefully."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    assert not await store.async_import_sqlite(hass, str(tmp_path / "missing.db"))


async def test_save_and_reload(hass: HomeAssistant) -> None:
    """Counters survive a save/load cycle."""
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    store.seed_unit(1)
    store.take_increment(1, 3)
    increment = store.current_increment(1)
    await store.async_save_now()

    store2 = UnitStore(hass, "test_entry")
    await store2.async_load()
    assert store2.is_configured(1)
    assert store2.current_increment(1) == increment
    assert store2.channel_gap(1, 3) == 1


async def test_load_data_without_channels(hass: HomeAssistant, hass_storage) -> None:
    """Data saved before per-channel tracking loads with an unknown gap."""
    hass_storage["becker_centronic.test_entry"] = {
        "version": 1,
        "data": {"units": {"1": {"code": "1737B", "increment": 7, "configured": True}}},
    }
    store = UnitStore(hass, "test_entry")
    await store.async_load()
    assert store.current_increment(1) == 7
    assert store.channel_gap(1, 1) is None
