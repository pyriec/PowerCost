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


@pytest.mark.asyncio
async def test_history_rebuild_includes_recorder_states_in_result(hass):
    """Test rebuild result includes recorder_states_imported counter."""
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
        entry_id="test_rebuild_rec_res",
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
    assert "recorder_states_imported" in res
    assert isinstance(res["recorder_states_imported"], int)
    assert "statistics_imported" in res

    await coordinator.async_unload()


def test_insert_recorder_states_sync_direct():
    """Test _insert_recorder_states_sync directly with an in-memory SQLite database."""
    from contextlib import contextmanager
    from unittest.mock import patch
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from homeassistant.components.recorder.db_schema import Base, States, StatesMeta, StateAttributes

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    @contextmanager
    def fake_session_scope(*args, **kwargs):
        yield session
        session.commit()

    hass = MagicMock()
    coordinator = MagicMock()
    rebuilder = HistoryRebuilder(hass, coordinator)

    mock_rec = MagicMock()
    mock_rec.states_meta_manager.active = False
    mock_rec.state_attributes_manager.get.return_value = None

    entity_id = "sensor.test_oven_cost_day"
    states_data = {
        entity_id: [(100.0, 0.5), (200.0, 1.25)],
    }
    metadata = {
        entity_id: {
            "unit_of_measurement": "€",
            "device_class": "monetary",
            "state_class": "total",
            "friendly_name": "Four Coût aujourd'hui",
        }
    }

    with patch("homeassistant.components.recorder.get_instance", return_value=mock_rec), \
         patch("homeassistant.components.recorder.util.session_scope", side_effect=fake_session_scope):
        saved_count = rebuilder._insert_recorder_states_sync(states_data, metadata, 50.0, 250.0)

    assert saved_count == 2

    # Verify rows in database
    meta_rows = session.execute(select(StatesMeta).where(StatesMeta.entity_id == entity_id)).scalars().all()
    assert len(meta_rows) == 1
    meta_id = meta_rows[0].metadata_id

    attr_rows = session.execute(select(StateAttributes)).scalars().all()
    assert len(attr_rows) >= 1

    state_rows = session.execute(
        select(States).where(States.metadata_id == meta_id).order_by(States.last_updated_ts)
    ).scalars().all()
    assert len(state_rows) == 2
    assert state_rows[0].state == "0.50"
    assert state_rows[0].last_updated_ts == 100.0
    assert state_rows[1].state == "1.25"
    assert state_rows[1].last_updated_ts == 200.0

    # Test that re-running cleans up within the time window
    states_data_new = {
        entity_id: [(150.0, 2.0)],
    }
    with patch("homeassistant.components.recorder.get_instance", return_value=mock_rec), \
         patch("homeassistant.components.recorder.util.session_scope", side_effect=fake_session_scope):
        new_count = rebuilder._insert_recorder_states_sync(states_data_new, metadata, 50.0, 250.0)

    assert new_count == 1
    re_rows = session.execute(
        select(States).where(States.metadata_id == meta_id).order_by(States.last_updated_ts)
    ).scalars().all()
    assert len(re_rows) == 1
    assert re_rows[0].state == "2.00"
    assert re_rows[0].last_updated_ts == 150.0


def test_raw_states_tracker_functionality():
    """Test _RawStatesTracker initial states, rollover resets, and changes."""
    from custom_components.electricity_cost.history import _RawStatesTracker
    from custom_components.electricity_cost.models import DeviceStatistics
    from custom_components.electricity_cost.const import (
        SENSOR_COST_DAY,
        SENSOR_COST_TOTAL,
    )

    sensors = [SENSOR_COST_DAY, SENSOR_COST_TOTAL]
    tracker = _RawStatesTracker(sensors)
    stats = DeviceStatistics(device_id="test")

    # 1. Initial point
    tracker.record_initial_state(stats, 1000.0)
    assert tracker.raw_states[SENSOR_COST_DAY] == [(1000.0, 0.0)]
    assert tracker.raw_states[SENSOR_COST_TOTAL] == [(1000.0, 0.0)]

    # 2. Consumption change
    stats.cost_today = 0.50
    stats.cost_total = 0.50
    tracker.record_changes(stats, 1060.0)
    assert tracker.raw_states[SENSOR_COST_DAY][-1] == (1060.0, 0.50)
    assert tracker.raw_states[SENSOR_COST_TOTAL][-1] == (1060.0, 0.50)

    # 3. Rollover at midnight
    tracker.record_rollover("day", 1100.0)
    assert tracker.raw_states[SENSOR_COST_DAY][-1] == (1100.0, 0.0)
    # SENSOR_COST_TOTAL should NOT be reset by day rollover
    assert tracker.raw_states[SENSOR_COST_TOTAL][-1] == (1060.0, 0.50)

    # 4. Strict monotonicity
    tracker.record_changes(stats, 1100.0)  # Same timestamp
    assert tracker.raw_states[SENSOR_COST_DAY][-1][0] > 1100.0

