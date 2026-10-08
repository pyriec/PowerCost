"""Storage management for PowerCost integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_PREFIX, STORAGE_VERSION
from .models import DeviceStatistics

_LOGGER = logging.getLogger(__name__)


class ElectricityCostStorage:
    """Manages persistent JSON storage using Home Assistant Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the storage helper."""
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry_id}",
        )

    async def async_load(self) -> dict[str, DeviceStatistics]:
        """Load stored statistics for all devices."""
        data = await self._store.async_load()
        if not data or "devices" not in data:
            _LOGGER.debug("No existing persistent data found for %s", self.entry_id)
            return {}

        result: dict[str, DeviceStatistics] = {}
        for dev_id, dev_data in data.get("devices", {}).items():
            try:
                result[dev_id] = DeviceStatistics.from_dict(dev_data)
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Failed to restore statistics for device %s: %s", dev_id, err)
        return result

    async def async_save(self, statistics: dict[str, DeviceStatistics]) -> None:
        """Save devices statistics to storage."""
        data_to_store = {
            "version": STORAGE_VERSION,
            "devices": {dev_id: stats.to_dict() for dev_id, stats in statistics.items()},
        }
        await self._store.async_save(data_to_store)
