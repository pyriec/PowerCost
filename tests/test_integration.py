"""End-to-end integration and coordinator tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_cost.const import (
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_PRICING_MODE,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_VARIABLE_PRICE_ENTITY,
    DOMAIN,
    PRICING_MODE_VARIABLE,
    SERVICE_RESET_STATISTICS,
    SOURCE_TYPE_POWER,
)


@pytest.mark.asyncio
async def test_full_setup_and_sensor_updates(hass: HomeAssistant):
    """Test full integration lifecycle and sensor updates."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PowerCost",
        data={
            CONF_PRICING_MODE: PRICING_MODE_VARIABLE,
            CONF_VARIABLE_PRICE_ENTITY: "sensor.test_price",
            CONF_DEVICES: [
                {
                    CONF_DEVICE_ID: "dev_wash",
                    CONF_DEVICE_NAME: "Lave Linge",
                    CONF_SOURCE_ENTITY: "sensor.wash_power",
                    CONF_SOURCE_TYPE: SOURCE_TYPE_POWER,
                }
            ],
        },
    )
    entry.add_to_hass(hass)

    # Set initial price
    hass.states.async_set("sensor.test_price", "0.25")

    # Set initial power
    t0_ts = 1772445600.0  # fixed timestamp
    hass.states.async_set(
        "sensor.wash_power",
        "1000",
        {"unit_of_measurement": "W"},
        timestamp=t0_ts,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Verify sensors created
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert "dev_wash" in coordinator.devices

    # Advance time by 1 hour (3600s) and update power to 2000 W
    # Avg power = (1000 + 2000) / 2 = 1.5 kW. 1.5 kW * 1h = 1.5 kWh.
    # Cost = 1.5 kWh * 0.25 €/kWh = 0.375 €
    t1_ts = t0_ts + 3600.0
    hass.states.async_set(
        "sensor.wash_power",
        "2000",
        {"unit_of_measurement": "W"},
        timestamp=t1_ts,
    )
    await hass.async_block_till_done()

    stats = coordinator.statistics["dev_wash"]
    assert pytest.approx(stats.energy_today, 0.001) == 1.5
    assert pytest.approx(stats.cost_today, 0.001) == 0.375
    assert pytest.approx(stats.cost_total, 0.001) == 0.375

    # Test reset_statistics service
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESET_STATISTICS,
        {"device_id": "dev_wash"},
        blocking=True,
    )
    stats_after_reset = coordinator.statistics["dev_wash"]
    assert stats_after_reset.cost_today == 0.0
    assert stats_after_reset.cost_total == 0.0

    # Test unload
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]
