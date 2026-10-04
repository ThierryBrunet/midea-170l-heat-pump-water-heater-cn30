"""Constants for the CN30 water-heater integration."""

from __future__ import annotations

DOMAIN = "midea_cn30_hws"
VERSION = "0.1.0"
PLATFORMS = ("water_heater", "sensor", "binary_sensor")

CONF_HOST = "host"
CONF_PORT = "port"
CONF_NAME = "name"
CONF_STALE_SECONDS = "stale_seconds"

DEFAULT_HOST = "192.168.31.219"
DEFAULT_PORT = 502
DEFAULT_NAME = "Chromagen HP170"
DEFAULT_STALE_SECONDS = 20

# Heater status period is about 4 s. Twenty seconds is several missed frames.
MIN_STALE_SECONDS = 8
MAX_STALE_SECONDS = 120

MANUFACTURER = "Chromagen"
MODEL = "HP170 (RSJ-15/190RDN3-C)"
HW_VERSION = "R-ZT15/190(C)-A AC128"
