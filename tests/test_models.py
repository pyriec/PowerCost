"""Unit tests for models and rollover tracking."""

from datetime import datetime, timezone
import pytest

from custom_components.electricity_cost.models import DeviceStatistics, PricingConfig


def test_device_statistics_add_consumption():
    """Test standard accumulation of consumption and cost."""
    stats = DeviceStatistics(device_id="wash_machine")
    t1 = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)

    stats.add_consumption(energy_kwh=1.5, cost=0.45, timestamp=t1)

    assert stats.cost_today == 0.45
    assert stats.cost_this_month == 0.45
    assert stats.cost_this_year == 0.45
    assert stats.cost_total == 0.45
    assert stats.energy_today == 1.5
    assert stats.energy_total == 1.5
    assert stats.max_cost_day == 0.45


def test_device_statistics_rollovers_and_averages():
    """Test daily, monthly, and yearly rollovers with max and average tracking."""
    stats = DeviceStatistics(device_id="wash_machine")

    # Day 1: 2026-03-01 -> 2.0 €
    stats.add_consumption(10.0, 2.0, datetime(2026, 3, 1, 14, 0))
    # Day 2: 2026-03-02 -> 4.0 €
    stats.add_consumption(20.0, 4.0, datetime(2026, 3, 2, 14, 0))
    # Day 3: 2026-03-03 -> 3.0 €
    stats.add_consumption(15.0, 3.0, datetime(2026, 3, 3, 14, 0))

    # Max daily cost should be 4.0 €
    assert stats.max_cost_day == 4.0
    # Day 1 (2.0) and Day 2 (4.0) are in daily_history, Day 3 (3.0) is cost_today
    # Avg day = (2.0 + 4.0 + 3.0) / 3 = 3.0 €
    assert pytest.approx(stats.avg_cost_day, 0.01) == 3.0

    # Month rollover: 2026-04-01 -> 5.0 €
    stats.add_consumption(25.0, 5.0, datetime(2026, 4, 1, 10, 0))
    assert stats.cost_today == 5.0
    assert stats.cost_this_month == 5.0
    # March total was 2 + 4 + 3 = 9.0 €
    assert stats.monthly_history.get("2026-03") == 9.0
    assert stats.max_cost_month == 9.0

    # Year rollover: 2027-01-01 -> 1.0 €
    stats.add_consumption(5.0, 1.0, datetime(2027, 1, 1, 10, 0))
    assert stats.cost_this_year == 1.0
    # 2026 total was 9.0 + 5.0 = 14.0 €
    assert stats.yearly_history.get("2026") == 14.0
    assert stats.max_cost_year == 14.0


def test_device_statistics_peak_offpeak_consumption():
    """Test peak and off-peak accumulation and rollovers."""
    stats = DeviceStatistics(device_id="water_heater")
    t1 = datetime(2026, 3, 1, 4, 0, tzinfo=timezone.utc)

    # Off-peak consumption
    stats.add_consumption(
        energy_kwh=2.0,
        cost=0.30,
        timestamp=t1,
        tariff="offpeak",
    )
    assert stats.cost_today == 0.30
    assert stats.cost_today_offpeak == 0.30
    assert stats.cost_today_peak == 0.0
    assert stats.energy_today_offpeak == 2.0
    assert stats.energy_today_peak == 0.0

    # Peak consumption later that day
    t2 = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
    stats.add_consumption(
        energy_kwh=3.0,
        cost=0.75,
        timestamp=t2,
        tariff="peak",
    )
    assert stats.cost_today == 1.05
    assert stats.cost_today_offpeak == 0.30
    assert stats.cost_today_peak == 0.75
    assert stats.cost_total_offpeak == 0.30
    assert stats.cost_total_peak == 0.75
    assert stats.energy_total_offpeak == 2.0
    assert stats.energy_total_peak == 3.0

    # Rollover to next day
    t3 = datetime(2026, 3, 2, 2, 0, tzinfo=timezone.utc)
    stats.add_consumption(
        energy_kwh=1.0,
        cost=0.15,
        timestamp=t3,
        tariff="offpeak",
    )
    assert stats.cost_today == 0.15
    assert stats.cost_today_offpeak == 0.15
    assert stats.cost_today_peak == 0.0
    # Totals accumulate
    assert stats.cost_total == 1.20
    assert stats.cost_total_offpeak == 0.45
    assert stats.cost_total_peak == 0.75


def test_device_statistics_serialization():
    """Test serialization to and from dictionary."""
    stats = DeviceStatistics(
        device_id="car_charger",
        cost_today_offpeak=1.5,
        cost_today_peak=2.5,
        cost_total_offpeak=10.0,
        cost_total_peak=20.0,
        energy_today_offpeak=10.0,
        energy_today_peak=12.0,
        energy_total_offpeak=100.0,
        energy_total_peak=150.0,
    )
    data = stats.to_dict()
    restored = DeviceStatistics.from_dict(data)

    assert restored.device_id == "car_charger"
    assert restored.cost_today_offpeak == 1.5
    assert restored.cost_today_peak == 2.5
    assert restored.cost_total_offpeak == 10.0
    assert restored.cost_total_peak == 20.0
    assert restored.energy_today_offpeak == 10.0
    assert restored.energy_total_peak == 150.0


def test_pricing_config_from_dict():
    """Test PricingConfig deserialization with pricing_mode and mode."""
    # From config entry dictionary with CONF_PRICING_MODE
    cfg = PricingConfig.from_dict({
        "pricing_mode": "peak_offpeak",
        "offpeak_price_entity": "sensor.hc",
        "peak_price_entity": "sensor.hp",
        "tariff_mode_entity": "sensor.mode",
        "offpeak_state": "HEURE CREUSE",
        "peak_state": "HEURE PLEINE",
    })
    assert cfg.mode == "peak_offpeak"
    assert cfg.offpeak_price_entity == "sensor.hc"
    assert cfg.peak_price_entity == "sensor.hp"
    assert cfg.tariff_mode_entity == "sensor.mode"
    assert cfg.offpeak_state == "HEURE CREUSE"
    assert cfg.peak_state == "HEURE PLEINE"

    # Serialization roundtrip
    d = cfg.to_dict()
    assert d["pricing_mode"] == "peak_offpeak"
    assert d["mode"] == "peak_offpeak"
    restored = PricingConfig.from_dict(d)
    assert restored.mode == "peak_offpeak"

    # Legacy dictionary with "mode" key
    cfg_legacy = PricingConfig.from_dict({
        "mode": "variable",
        "variable_price_entity": "sensor.spot_price",
    })
    assert cfg_legacy.mode == "variable"
    assert cfg_legacy.variable_price_entity == "sensor.spot_price"


