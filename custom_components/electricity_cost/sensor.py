"""Sensor platform for Electricity Cost integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SENSOR_COST_AVG_DAY,
    SENSOR_COST_AVG_MONTH,
    SENSOR_COST_AVG_YEAR,
    SENSOR_COST_DAY,
    SENSOR_COST_MAX_DAY,
    SENSOR_COST_MAX_MONTH,
    SENSOR_COST_MAX_YEAR,
    SENSOR_COST_MONTH,
    SENSOR_COST_TOTAL,
    SENSOR_COST_YEAR,
)
from .coordinator import ElectricityCostCoordinator
from .models import DeviceConfig, DeviceStatistics

SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    SENSOR_COST_DAY: SensorEntityDescription(
        key=SENSOR_COST_DAY,
        translation_key=SENSOR_COST_DAY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_MONTH: SensorEntityDescription(
        key=SENSOR_COST_MONTH,
        translation_key=SENSOR_COST_MONTH,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_YEAR: SensorEntityDescription(
        key=SENSOR_COST_YEAR,
        translation_key=SENSOR_COST_YEAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_TOTAL: SensorEntityDescription(
        key=SENSOR_COST_TOTAL,
        translation_key=SENSOR_COST_TOTAL,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_MAX_DAY: SensorEntityDescription(
        key=SENSOR_COST_MAX_DAY,
        translation_key=SENSOR_COST_MAX_DAY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_MAX_MONTH: SensorEntityDescription(
        key=SENSOR_COST_MAX_MONTH,
        translation_key=SENSOR_COST_MAX_MONTH,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_MAX_YEAR: SensorEntityDescription(
        key=SENSOR_COST_MAX_YEAR,
        translation_key=SENSOR_COST_MAX_YEAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_AVG_DAY: SensorEntityDescription(
        key=SENSOR_COST_AVG_DAY,
        translation_key=SENSOR_COST_AVG_DAY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_AVG_MONTH: SensorEntityDescription(
        key=SENSOR_COST_AVG_MONTH,
        translation_key=SENSOR_COST_AVG_MONTH,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
    SENSOR_COST_AVG_YEAR: SensorEntityDescription(
        key=SENSOR_COST_AVG_YEAR,
        translation_key=SENSOR_COST_AVG_YEAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Electricity Cost sensor entities based on a config entry."""
    coordinator: ElectricityCostCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[ElectricityCostSensor] = []
    for device_cfg in coordinator.devices.values():
        for sensor_type, description in SENSOR_DESCRIPTIONS.items():
            entities.append(
                ElectricityCostSensor(
                    coordinator=coordinator,
                    device_cfg=device_cfg,
                    description=description,
                    sensor_type=sensor_type,
                )
            )

    async_add_entities(entities)


class ElectricityCostSensor(SensorEntity):
    """Representation of an Electricity Cost Sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ElectricityCostCoordinator,
        device_cfg: DeviceConfig,
        description: SensorEntityDescription,
        sensor_type: str,
    ) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.device_cfg = device_cfg
        self.entity_description = description
        self.sensor_type = sensor_type

        # Unique ID pattern
        self._attr_unique_id = f"{coordinator.entry_id}_{device_cfg.device_id}_{sensor_type}"

        # Group under device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry_id}_{device_cfg.device_id}")},
            name=device_cfg.name,
            manufacturer="Home Assistant Electricity Cost",
            model=f"Source: {device_cfg.source_type}",
        )

    @property
    def _stats(self) -> DeviceStatistics:
        """Helper to get current statistics for the device."""
        return self.coordinator.statistics.setdefault(
            self.device_cfg.device_id,
            DeviceStatistics(device_id=self.device_cfg.device_id),
        )

    @property
    def native_value(self) -> float | None:
        """Return the current sensor value."""
        stats = self._stats
        mapping: dict[str, float] = {
            SENSOR_COST_DAY: stats.cost_today,
            SENSOR_COST_MONTH: stats.cost_this_month,
            SENSOR_COST_YEAR: stats.cost_this_year,
            SENSOR_COST_TOTAL: stats.cost_total,
            SENSOR_COST_MAX_DAY: stats.max_cost_day,
            SENSOR_COST_MAX_MONTH: stats.max_cost_month,
            SENSOR_COST_MAX_YEAR: stats.max_cost_year,
            SENSOR_COST_AVG_DAY: stats.avg_cost_day,
            SENSOR_COST_AVG_MONTH: stats.avg_cost_month,
            SENSOR_COST_AVG_YEAR: stats.avg_cost_year,
        }
        val = mapping.get(self.sensor_type)
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        stats = self._stats
        base_attrs: dict[str, Any] = {
            "device_id": self.device_cfg.device_id,
            "source_entity": self.device_cfg.source_entity,
            "source_type": self.device_cfg.source_type,
            "last_rebuild_timestamp": stats.last_rebuild_timestamp,
        }

        if self.sensor_type == SENSOR_COST_DAY:
            base_attrs["energy_today_kwh"] = stats.energy_today
            base_attrs["current_price"] = self.coordinator.pricing_manager.get_current_price()
        elif self.sensor_type == SENSOR_COST_MONTH:
            base_attrs["energy_month_kwh"] = stats.energy_this_month
        elif self.sensor_type == SENSOR_COST_YEAR:
            base_attrs["energy_year_kwh"] = stats.energy_this_year
        elif self.sensor_type == SENSOR_COST_TOTAL:
            base_attrs["energy_total_kwh"] = stats.energy_total

        return base_attrs

    async def async_added_to_hass(self) -> None:
        """Register coordinator update callback."""
        self.async_on_remove(
            self.coordinator.async_register_listener(self.async_write_ha_state)
        )
