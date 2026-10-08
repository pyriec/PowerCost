"""The PowerCost integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DEVICE_ID,
    ATTR_END_DATE,
    ATTR_REBUILD_ALL,
    ATTR_START_DATE,
    CONF_DEVICES,
    DOMAIN,
    SERVICE_REBUILD_HISTORY,
    SERVICE_RESET_STATISTICS,
)
from .coordinator import ElectricityCostCoordinator
from .history import HistoryRebuilder
from .models import DeviceConfig, DeviceStatistics, PricingConfig

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

REBUILD_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_START_DATE): cv.datetime,
        vol.Optional(ATTR_END_DATE): cv.datetime,
        vol.Optional(ATTR_REBUILD_ALL, default=False): cv.boolean,
    }
)

RESET_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_REBUILD_ALL, default=False): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PowerCost from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    pricing_config = PricingConfig.from_dict(entry.data)
    devices_data = entry.data.get(CONF_DEVICES, [])
    devices = [DeviceConfig.from_dict(d) for d in devices_data]

    coordinator = ElectricityCostCoordinator(
        hass=hass,
        entry_id=entry.entry_id,
        pricing_config=pricing_config,
        devices=devices,
    )

    await coordinator.async_setup()
    rebuilder = HistoryRebuilder(hass, coordinator)

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "rebuilder": rebuilder,
    }

    # Register services once
    if not hass.services.has_service(DOMAIN, SERVICE_REBUILD_HISTORY):

        async def handle_rebuild_history(call: ServiceCall) -> None:
            """Service to rebuild device history from Home Assistant recorder."""
            start_date: datetime = call.data[ATTR_START_DATE]
            end_date: datetime = call.data.get(ATTR_END_DATE) or dt_util.utcnow()
            target_device_id = call.data.get(ATTR_DEVICE_ID)
            rebuild_all = call.data.get(ATTR_REBUILD_ALL, False)

            # Ensure UTC datetime
            if start_date.tzinfo is None:
                start_date = dt_util.as_utc(start_date)
            if end_date.tzinfo is None:
                end_date = dt_util.as_utc(end_date)

            for entry_data in hass.data[DOMAIN].values():
                c: ElectricityCostCoordinator = entry_data["coordinator"]
                r: HistoryRebuilder = entry_data["rebuilder"]

                devices_to_process = list(c.devices.keys()) if rebuild_all or not target_device_id else [target_device_id]
                for dev_id in devices_to_process:
                    if dev_id in c.devices:
                        try:
                            await r.async_rebuild_device(dev_id, start_date, end_date)
                        except Exception as err:
                            _LOGGER.error("Failed to rebuild history for %s: %s", dev_id, err)
                            raise HomeAssistantError(f"History rebuild failed for {dev_id}: {err}") from err

        async def handle_reset_statistics(call: ServiceCall) -> None:
            """Service to reset statistics for a device or all devices."""
            target_device_id = call.data.get(ATTR_DEVICE_ID)
            reset_all = call.data.get(ATTR_REBUILD_ALL, False)

            for entry_data in hass.data[DOMAIN].values():
                c: ElectricityCostCoordinator = entry_data["coordinator"]
                devices_to_reset = list(c.devices.keys()) if reset_all or not target_device_id else [target_device_id]
                for dev_id in devices_to_reset:
                    if dev_id in c.devices:
                        c.statistics[dev_id] = DeviceStatistics(device_id=dev_id)
                await c.async_save_data()
                c.async_update_listeners()

        hass.services.async_register(
            DOMAIN,
            SERVICE_REBUILD_HISTORY,
            handle_rebuild_history,
            schema=REBUILD_SERVICE_SCHEMA,
        )

        hass.services.async_register(
            DOMAIN,
            SERVICE_RESET_STATISTICS,
            handle_reset_statistics,
            schema=RESET_SERVICE_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data:
            coordinator: ElectricityCostCoordinator = entry_data["coordinator"]
            await coordinator.async_unload()

        # Remove services if no more entries exist
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_REBUILD_HISTORY)
            hass.services.async_remove(DOMAIN, SERVICE_RESET_STATISTICS)

    return unload_ok
