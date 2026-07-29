"""Constants for the PairLink integration."""

from __future__ import annotations

DOMAIN = "pairlink"

CONF_REMOTE_ID = "remote_id"
CONF_HOME_ID = "home_id"
CONF_PASSWORD = "password"
CONF_REMOTE_CHANNEL = "remote_channel"
CONF_LIGHT_ID = "light_id"

PAIRLINK_MARKER = b"\xc0\xff"
PAIRLINK_IDLE_ADVERTISEMENT = 0x05
PAIRLINK_REGISTRATION_ADVERTISEMENT = 0x0D

FFD0_UUID = "0000ffd0-0000-1000-8000-00805f9b34fb"
FFD1_UUID = "0000ffd1-0000-1000-8000-00805f9b34fb"
FFD2_UUID = "0000ffd2-0000-1000-8000-00805f9b34fb"

EVENT_ON = "on"
EVENT_OFF = "off"
EVENT_TYPES = [EVENT_ON, EVENT_OFF]
COMMAND_TO_EVENT_TYPE = {
    0x01: EVENT_ON,
    0x02: EVENT_OFF,
}

PLATFORMS = ["event", "sensor"]

REGISTRATION_TIMEOUT = 120
LOGIN_RESPONSE_TIMEOUT = 10.0
POST_WELCOME_DELAY = 0.5
# Real-hardware validation showed that rapid packets represent intentional
# repeated button presses. Preserve every operation by default.
DEDUPLICATION_WINDOW = 0.0
NOTIFICATION_QUEUE_SIZE = 256
MAX_LOGIN_TIMEOUTS_BEFORE_REAUTH = 3
RECONNECT_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
