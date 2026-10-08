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
        vol.Optional(ATTR_DEVICE_ID): vol.Any(cv.string, [cv.string]),
        vol.Optional("entity_id"): vol.Any(cv.string, [cv.string]),
        vol.Optional("area_id"): vol.Any(cv.string, [cv.string]),
        vol.Required(ATTR_START_DATE): cv.datetime,
        vol.Optional(ATTR_END_DATE): cv.datetime,
        vol.Optional(ATTR_REBUILD_ALL, default=False): cv.boolean,
    }
)

RESET_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.Any(cv.string, [cv.string]),
        vol.Optional("entity_id"): vol.Any(cv.string, [cv.string]),
        vol.Optional("area_id"): vol.Any(cv.string, [cv.string]),
        vol.Optional(ATTR_REBUILD_ALL, default=False): cv.boolean,
    }
)


def _extract_target_strings(val: Any) -> list[str]:
    """Extract list of strings from service call parameter."""
    if not val:
        return []
    if isinstance(val, (list, tuple, set)):
        items: list[str] = []
        for v in val:
            items.extend(_extract_target_strings(v))
        return items
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def _resolve_target_device_ids(
    hass: HomeAssistant,
    coordinator: ElectricityCostCoordinator,
    targets: list[str],
) -> set[str]:
    """Resolve user targets (HA device registry ID, internal device_id, entity_id, name) to internal device IDs."""
    resolved: set[str] = set()
    prefix = f"{coordinator.entry_id}_"

    for target in targets:
        # 1. Direct match with coordinator internal device_id
        if target in coordinator.devices:
            resolved.add(target)
            continue

        # 2. Match with entry_id prefix: f"{coordinator.entry_id}_{dev_id}"
        if target.startswith(prefix):
            sub_id = target[len(prefix):]
            if sub_id in coordinator.devices:
                resolved.add(sub_id)
                continue

        # 3. Look up in Home Assistant Device Registry
        try:
            from homeassistant.helpers import device_registry as dr

            dev_reg = dr.async_get(hass)
            device_entry = dev_reg.async_get(target)
            if device_entry:
                found_in_reg = False
                for dom, ident in device_entry.identifiers:
                    if dom == DOMAIN:
                        if ident in coordinator.devices:
                            resolved.add(ident)
                            found_in_reg = True
                            break
                        if ident.startswith(prefix):
                            sub_id = ident[len(prefix):]
                            if sub_id in coordinator.devices:
                                resolved.add(sub_id)
                                found_in_reg = True
                                break
                if found_in_reg:
                    continue
        except Exception as err:
            _LOGGER.debug("Device registry lookup error for %s: %s", target, err)

        # 4. Look up in Home Assistant Entity Registry
        try:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(hass)
            entity_entry = ent_reg.async_get(target)
            if entity_entry and entity_entry.device_id:
                sub_resolved = _resolve_target_device_ids(
                    hass, coordinator, [entity_entry.device_id]
                )
                if sub_resolved:
                    resolved.update(sub_resolved)
                    continue
        except Exception as err:
            _LOGGER.debug("Entity registry lookup error for %s: %s", target, err)

        # 5. Fallback: match by device friendly name or source entity ID
        for dev_id, dev_cfg in coordinator.devices.items():
            if target in (dev_cfg.name, dev_cfg.source_entity):
                resolved.add(dev_id)

    return resolved


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
            rebuild_all = call.data.get(ATTR_REBUILD_ALL, False)

            # Ensure UTC datetime
            if start_date.tzinfo is None:
                start_date = dt_util.as_utc(start_date)
            if end_date.tzinfo is None:
                end_date = dt_util.as_utc(end_date)

            if start_date >= end_date:
                raise HomeAssistantError(
                    f"La date de début ({start_date.strftime('%Y-%m-%d %H:%M')}) doit être antérieure à la date de fin ({end_date.strftime('%Y-%m-%d %H:%M')})."
                )

            targets: list[str] = []
            for key in (ATTR_DEVICE_ID, "entity_id", "area_id"):
                targets.extend(_extract_target_strings(call.data.get(key)))

            total_rebuilt = 0
            error_messages: list[str] = []

            for entry_data in hass.data[DOMAIN].values():
                c: ElectricityCostCoordinator = entry_data["coordinator"]
                r: HistoryRebuilder = entry_data["rebuilder"]

                if rebuild_all or not targets:
                    devices_to_process = list(c.devices.keys())
                else:
                    devices_to_process = list(_resolve_target_device_ids(hass, c, targets))

                if targets and not devices_to_process:
                    raise HomeAssistantError(
                        f"Aucun appareil PowerCost correspondant trouvé pour la sélection : {', '.join(targets)}. "
                        "Veuillez choisir un appareil configuré dans la liste."
                    )

                for dev_id in devices_to_process:
                    if dev_id in c.devices:
                        try:
                            res = await r.async_rebuild_device(dev_id, start_date, end_date)
                            if res.get("success"):
                                total_rebuilt += 1
                            else:
                                error_messages.append(res.get("message", "Erreur inconnue"))
                        except Exception as err:
                            _LOGGER.error("Failed to rebuild history for %s: %s", dev_id, err)
                            error_messages.append(f"{c.devices[dev_id].name}: {err}")

            if error_messages:
                raise HomeAssistantError(
                    f"Erreur lors de la reconstruction : {'; '.join(error_messages)}"
                )

            if total_rebuilt == 0:
                raise HomeAssistantError(
                    "Aucun appareil n'a été reconstruit. Vérifiez vos appareils ou les dates demandées."
                )

        async def handle_reset_statistics(call: ServiceCall) -> None:
            """Service to reset statistics for a device or all devices."""
            reset_all = call.data.get(ATTR_REBUILD_ALL, False)

            targets: list[str] = []
            for key in (ATTR_DEVICE_ID, "entity_id", "area_id"):
                targets.extend(_extract_target_strings(call.data.get(key)))

            for entry_data in hass.data[DOMAIN].values():
                c: ElectricityCostCoordinator = entry_data["coordinator"]
                if reset_all or not targets:
                    devices_to_reset = list(c.devices.keys())
                else:
                    devices_to_reset = list(_resolve_target_device_ids(hass, c, targets))

                if targets and not devices_to_reset:
                    raise HomeAssistantError(
                        f"Aucun appareil PowerCost correspondant trouvé pour la sélection : {', '.join(targets)}."
                    )

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
