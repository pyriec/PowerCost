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
    assert stats.last_rebuild_timestamp is not None

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_recorder_only_success(hass):
    """Test rebuilding with recorder only when full data is available."""
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
        entry_id="test_rebuild_rec_entry",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    power_states = [
        State("sensor.oven_power", "2000", last_updated=t0),
        State("sensor.oven_power", "2000", last_updated=t1),
    ]
    price_states = [
        State("sensor.elec_price", "0.20", last_updated=t0),
    ]

    mock_history_data = {
        "sensor.oven_power": power_states,
        "sensor.elec_price": price_states,
    }

    rebuilder._fetch_history = MagicMock(return_value=mock_history_data)

    res = await rebuilder.async_rebuild_device("oven", t0, t1, data_source="recorder")
    assert res["success"] is True
    assert res["data_source_requested"] == "recorder"
    assert res["periods_used"]["recorder"] is not None
    assert res["periods_used"]["lts"] is None
    assert res["records_processed"] == 2

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_recorder_only_partial_data_error(hass):
    """Test that requesting recorder only when data is missing on part of the period returns an explicit error."""
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
        entry_id="test_rebuild_rec_err",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)
    t_avail = datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc)

    # When querying starting at t_start, earliest state found is t_avail
    def fake_fetch_history(s_date, e_date, entity_ids):
        if e_date <= t_avail:
            return {"sensor.oven_power": []}
        return {
            "sensor.oven_power": [
                State("sensor.oven_power", "2000", last_updated=t_avail),
            ],
            "sensor.elec_price": [
                State("sensor.elec_price", "0.25", last_updated=t_avail),
            ],
        }

    rebuilder._fetch_history = MagicMock(side_effect=fake_fetch_history)

    res = await rebuilder.async_rebuild_device("oven", t_start, t_end, data_source="recorder")
    assert res["success"] is False
    assert "Données Recorder indisponibles pour 'Four'" in res["message"]
    assert "sur la période" in res["message"]
    assert "qu'à partir du" in res["message"]
    assert "15/02/2026" in res["message"]

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_recorder_only_no_data_error(hass):
    """Test that requesting recorder only when no data exists returns error."""
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
        entry_id="test_rebuild_rec_no_data",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t_start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    rebuilder._fetch_history = MagicMock(return_value={"sensor.oven_power": []})

    res = await rebuilder.async_rebuild_device("oven", t_start, t_end, data_source="recorder")
    assert res["success"] is False
    assert "Aucune donnée brute du Recorder trouvée" in res["message"]

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_statistics_only(hass):
    """Test rebuilding using long-term statistics only."""
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
        entry_id="test_rebuild_lts_entry",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    # LTS hourly stats: mean power 2000W between 10:00 and 11:00 (2.0 kWh)
    lts_stats = [
        {"start": t0, "end": t1, "mean": 2000.0},
    ]

    rebuilder._fetch_statistics = MagicMock(return_value=lts_stats)
    rebuilder._fetch_history = MagicMock(return_value={})

    res = await rebuilder.async_rebuild_device("oven", t0, t1, data_source="statistics")
    assert res["success"] is True
    assert res["data_source_requested"] == "statistics"
    assert res["periods_used"]["lts"] is not None
    assert res["periods_used"]["recorder"] is None
    assert res["records_processed"] == 1

    stats = coordinator.statistics["oven"]
    assert pytest.approx(stats.energy_total, 0.01) == 2.0

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_statistics_no_data(hass):
    """Test statistics only mode returns error when LTS is empty."""
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
        entry_id="test_rebuild_lts_empty",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)

    rebuilder._fetch_statistics = MagicMock(return_value=[])

    res = await rebuilder.async_rebuild_device("oven", t0, t1, data_source="statistics")
    assert res["success"] is False
    assert "Aucune statistique à long terme trouvée" in res["message"]

    await coordinator.async_unload()


@pytest.mark.asyncio
async def test_history_rebuild_mixed_mode(hass):
    """Test mixed mode uses LTS for older period and recorder for recent period."""
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
        entry_id="test_rebuild_mixed_entry",
        pricing_config=pricing_config,
        devices=[device],
    )
    await coordinator.async_setup()

    rebuilder = HistoryRebuilder(hass, coordinator)

    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, 1, 0, tzinfo=timezone.utc)

    # LTS from t0 to t1: 1000W for 1 hour
    lts_stats = [
        {"start": t0, "end": t0 + timedelta(hours=1), "mean": 1000.0},
    ]

    # Recorder states from t1 to t2: 2000W
    power_states = [
        State("sensor.oven_power", "2000", last_updated=t1),
        State("sensor.oven_power", "2000", last_updated=t2),
    ]
    price_states = [
        State("sensor.elec_price", "0.20", last_updated=t0),
    ]

    def fake_fetch_history(s_date, e_date, entity_ids):
        if e_date <= t1:
            return {"sensor.oven_power": []}
        return {
            "sensor.oven_power": power_states,
            "sensor.elec_price": price_states,
        }

    rebuilder._fetch_statistics = MagicMock(return_value=lts_stats)
    rebuilder._fetch_history = MagicMock(side_effect=fake_fetch_history)

    res = await rebuilder.async_rebuild_device("oven", t0, t2, data_source="mixed")
    assert res["success"] is True
    assert res["periods_used"]["lts"] is not None
    assert res["periods_used"]["recorder"] is not None
    assert res["periods_used"]["lts"]["start"] == t0.isoformat()
    assert res["periods_used"]["lts"]["end"] == t1.isoformat()
    assert res["periods_used"]["recorder"]["start"] == t1.isoformat()
    assert res["periods_used"]["recorder"]["end"] == t2.isoformat()

    await coordinator.async_unload()
