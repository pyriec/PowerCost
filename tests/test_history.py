"""Unit tests for historical reconstruction."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from homeassistant.core import State

from custom_components.electricity_cost.const import (
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_POWER,
)
from custom_components.electricity_cost.coordinator import ElectricityCostCoordinator
from custom_components.electricity_cost.history import HistoryRebuilder
from custom_components.electricity_cost.models import DeviceConfig, PricingConfig


@pytest.mark.asyncio
async def test_history_rebuild_simple(hass):
    """Test rebuilding device history from simulated recorded states."""
    pricing_config = PricingConfig(
        mode=PRICING_MODE_VARIABLE,
        variable_price_entity="sensor.elec_price",
    )
    device = DeviceConfig(
        device_id="oven",
        name="Four",
        source_entity="sensor.oven_power",
        source_type=SOURCE_TYPE_POWER,
        source_unit="W",
    )

    coordinator = ElectricityCostCoordinator(
        hass=hass,
        entry_id="test_rebuild_entry",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    # Simulated recorded states: 2000W between 10:00 and 11:00 (2.0 kWh)
    # Price was 0.30 €/kWh
    power_states = [
        State("sensor.oven_power", "2000", last_updated=t0),
        State("sensor.oven_power", "2000", last_updated=t1),
    ]
    price_states = [
        State("sensor.elec_price", "0.30", last_updated=t0),
    ]

    mock_history_data = {
        "sensor.oven_power": power_states,
        "sensor.elec_price": price_states,
    }

    # Mock _fetch_history
    rebuilder._fetch_history = MagicMock(return_value=mock_history_data)

    res = await rebuilder.async_rebuild_device("oven", t0, t1)
    assert res["success"] is True
    assert res["records_processed"] == 2

    stats = coordinator.statistics["oven"]
    # 2000W * 1h = 2.0 kWh * 0.30 €/kWh = 0.60 €
    assert pytest.approx(stats.energy_total, 0.01) == 2.0
    assert pytest.approx(stats.cost_total, 0.01) == 0.60
    assert stats.last_rebuild_timestamp is not None
