"""End-to-end integration and coordinator tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
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

    # Test reset_statistics service with internal id
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESET_STATISTICS,
        {"device_id": "dev_wash"},
        blocking=True,
    )
    stats_after_reset = coordinator.statistics["dev_wash"]
    assert stats_after_reset.cost_today == 0.0
    assert stats_after_reset.cost_total == 0.0

    # Test reset_statistics service with Home Assistant Device Registry ID (UI selector)
    from homeassistant.helpers import device_registry as dr
    dev_reg = dr.async_get(hass)
    device_entry = dev_reg.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_dev_wash")})
    assert device_entry is not None

    coordinator.statistics["dev_wash"].cost_today = 5.0
    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESET_STATISTICS,
        {"device_id": device_entry.id},
        blocking=True,
    )
    assert coordinator.statistics["dev_wash"].cost_today == 0.0

    # Test unload
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_rebuild_history_service_with_data_source(hass: HomeAssistant):
    """Test calling rebuild_history service with data_source parameter."""
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.electricity_cost.const import SERVICE_REBUILD_HISTORY

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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    rebuilder = hass.data[DOMAIN][entry.entry_id]["rebuilder"]

    # Test error when recorder only is requested but recorder has no data
    rebuilder._fetch_history = patch.object(rebuilder, "_fetch_history", return_value={"sensor.wash_power": []})
    with patch.object(rebuilder, "_find_earliest_state_timestamp", return_value=datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)):
        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REBUILD_HISTORY,
                {
                    "device_id": "dev_wash",
                    "start_date": "2026-01-01T00:00:00+00:00",
                    "end_date": "2026-03-01T00:00:00+00:00",
                    "data_source": "recorder",
                },
                blocking=True,
            )
        assert "Données Recorder indisponibles" in str(exc_info.value)
        assert "qu'à partir du" in str(exc_info.value)

    # Test success when recorder only has complete data
    t_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    with patch.object(rebuilder, "_find_earliest_state_timestamp", return_value=t_start):
        with patch.object(
            rebuilder,
            "_fetch_history",
            return_value={
                "sensor.wash_power": [
                    State("sensor.wash_power", "1000", last_updated=t_start),
                    State("sensor.wash_power", "1000", last_updated=t_start + timedelta(hours=1)),
                ],
                "sensor.test_price": [
                    State("sensor.test_price", "0.20", last_updated=t_start),
                ],
            },
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_REBUILD_HISTORY,
                {
                    "device_id": "dev_wash",
                    "start_date": "2026-01-01T00:00:00+00:00",
                    "end_date": "2026-01-01T01:00:00+00:00",
                    "data_source": "recorder",
                },
                blocking=True,
            )

    stats = hass.data[DOMAIN][entry.entry_id]["coordinator"].statistics["dev_wash"]
    assert pytest.approx(stats.energy_total, 0.01) == 1.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_rebuild_does_not_drop_to_zero_during_execution(hass: HomeAssistant):
    """Test that existing stats are preserved and live readings are ignored during rebuild."""
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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    stats = coordinator.statistics["dev_wash"]
    stats.cost_today = 10.0
    stats.energy_today = 40.0
    stats.cost_this_month = 25.0
    stats.energy_this_month = 100.0

    # Simulate rebuild running
    coordinator.mark_device_rebuilding("dev_wash", True)
    assert coordinator.is_device_rebuilding("dev_wash") is True

    # Incoming live reading while rebuild is in progress:
    hass.states.async_set(
        "sensor.wash_power",
        "500",
        {"unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()

    # Prior stats must NOT be reset or wiped to 0
    assert stats.cost_this_month == 25.0
    assert stats.energy_this_month == 100.0

    coordinator.mark_device_rebuilding("dev_wash", False)
    assert coordinator.is_device_rebuilding("dev_wash") is False

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
