"""Constants for the Becker Centronic integration."""

from __future__ import annotations

DOMAIN = "becker_centronic"

MANUFACTURER = "Becker Antriebe GmbH"
MODEL_STICK = "Centronic USB Stick"

# Config entry data
CONF_CONNECTION = "connection"
CONNECTION_SERIAL = "serial"
CONNECTION_SOCKET = "socket"
CONF_DEVICE_PATH = "device_path"
CONF_LEGACY_DB_PATH = "legacy_db_path"

# Cover subentry data
SUBENTRY_TYPE_COVER = "cover"
CONF_UNIT = "unit"
CONF_CHANNEL = "channel"
CONF_DEVICE_CLASS = "device_class"
CONF_TRAVELLING_TIME_UP = "travelling_time_up"
CONF_TRAVELLING_TIME_DOWN = "travelling_time_down"
CONF_TILT_MODE = "tilt_mode"
TILT_MODE_NONE = "none"
TILT_MODE_INTERMEDIATE = "intermediate"
TILT_MODE_BLIND = "blind"
CONF_TILT_TIME_BLIND = "tilt_time_blind"
CONF_INTERMEDIATE_POSITION_UP = "intermediate_position_up"
CONF_INTERMEDIATE_POSITION_DOWN = "intermediate_position_down"
CONF_TRACK_INTERMEDIATE = "track_intermediate"
CONF_REMOTE_IDS = "remote_ids"
CONF_VALUE_TEMPLATE = "value_template"

# Defaults
DEFAULT_PORT = 5000
DEFAULT_INTERMEDIATE_POSITION_UP = 25
DEFAULT_INTERMEDIATE_POSITION_DOWN = 75
DEFAULT_TILT_TIME_BLIND = 0.3

OPEN_POSITION = 100
CLOSED_POSITION = 0

TILT_RECEIVE_TIMEOUT = 1.0

# Dispatcher signals (formatted with entry_id)
SIGNAL_PACKET = f"{DOMAIN}_packet_{{}}"
SIGNAL_CONNECTION = f"{DOMAIN}_connection_{{}}"

# Event fired on the HA bus for every received remote packet
EVENT_REMOTE_PACKET = f"{DOMAIN}_remote_packet_received"

ATTR_UNIT = "unit"
ATTR_CHANNEL = "channel"
ATTR_COMMAND = "command"

# Services
SERVICE_RESYNC = "resync"
SERVICE_UNPAIR = "unpair"
SERVICE_PAIR_MASTER = "pair_master"
SERVICE_CLEAR_POSITION = "clear_position"
