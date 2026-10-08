"""Unit tests for calculation logic in electricity_cost."""

import pytest

from custom_components.electricity_cost.calculations import (
    calculate_energy_delta,
    calculate_power_consumption,
    normalize_energy_to_kwh,
    normalize_power_to_kw,
)
from custom_components.electricity_cost.const import (
    SOURCE_TYPE_ENERGY_DAILY,
    SOURCE_TYPE_ENERGY_MONTHLY,
    SOURCE_TYPE_ENERGY_TOTAL,
    SOURCE_TYPE_ENERGY_YEARLY,
    SOURCE_TYPE_POWER,
    UNIT_KW,
    UNIT_KWH,
    UNIT_MWH,
    UNIT_W,
    UNIT_WH,
)


def test_normalize_power_to_kw():
    """Test power unit conversions."""
    assert normalize_power_to_kw(1000.0, UNIT_W) == 1.0
    assert normalize_power_to_kw(2.5, UNIT_KW) == 2.5
    assert normalize_power_to_kw(500.0, None) == 0.5


def test_normalize_energy_to_kwh():
    """Test energy unit conversions."""
    assert normalize_energy_to_kwh(1500.0, UNIT_WH) == 1.5
    assert normalize_energy_to_kwh(3.2, UNIT_KWH) == 3.2
    assert normalize_energy_to_kwh(0.002, UNIT_MWH) == 2.0
    assert normalize_energy_to_kwh(5.0, None) == 5.0


def test_power_consumption_constant_power():
    """Test power integration with constant 1000W over 1 hour (3600s)."""
    kwh = calculate_power_consumption(
        last_power_val=1000.0,
        current_power_val=1000.0,
        last_ts=1000000.0,
        current_ts=1003600.0,
        unit=UNIT_W,
    )
    assert kwh == 1.0


def test_power_consumption_variable_power():
    """Test power integration with changing power (trapezoidal: 1000W to 2000W over 30 min)."""
    # Avg power = 1.5 kW * 0.5h = 0.75 kWh
    kwh = calculate_power_consumption(
        last_power_val=1000.0,
        current_power_val=2000.0,
        last_ts=1000000.0,
        current_ts=1001800.0,
        unit=UNIT_W,
    )
    assert kwh == 0.75


def test_power_consumption_device_off():
    """Test device turned off (0W)."""
    kwh = calculate_power_consumption(
        last_power_val=0.0,
        current_power_val=0.0,
        last_ts=1000000.0,
        current_ts=1003600.0,
        unit=UNIT_W,
    )
    assert kwh == 0.0


def test_power_consumption_gap_limit():
    """Test power gap > 2 hours is safely ignored."""
    kwh = calculate_power_consumption(
        last_power_val=2000.0,
        current_power_val=2000.0,
        last_ts=1000000.0,
        current_ts=1010000.0,  # 10,000s > 7200s
        unit=UNIT_W,
    )
    assert kwh == 0.0


def test_energy_daily_meter():
    """Test daily energy meter standard accumulation and midnight reset."""
    # Monotonic increment: 2.8 kWh -> 3.1 kWh = 0.3 kWh
    delta1 = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_DAILY,
        last_val=2.8,
        current_val=3.1,
        unit=UNIT_KWH,
    )
    assert delta1 == 0.3

    # Midnight reset: 5.2 kWh -> 0.1 kWh = 0.1 kWh (new cycle start)
    delta_reset = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_DAILY,
        last_val=5.2,
        current_val=0.1,
        unit=UNIT_KWH,
    )
    assert delta_reset == 0.1


def test_energy_monthly_meter():
    """Test monthly energy meter month-end reset."""
    # 250 kWh on Oct 31 -> 0.4 kWh on Nov 1
    delta = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_MONTHLY,
        last_val=250.0,
        current_val=0.4,
        unit=UNIT_KWH,
    )
    assert delta == 0.4


def test_energy_yearly_meter():
    """Test yearly energy meter year-end reset."""
    # 4200 kWh on Dec 31 -> 0.8 kWh on Jan 1
    delta = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_YEARLY,
        last_val=4200.0,
        current_val=0.8,
        unit=UNIT_KWH,
    )
    assert delta == 0.8


def test_energy_total_meter():
    """Test total cumulative meter and unexpected drops/resets."""
    # Normal increment: 1000.0 -> 1001.5 kWh = 1.5 kWh
    delta = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_TOTAL,
        last_val=1000.0,
        current_val=1001.5,
        unit=UNIT_KWH,
    )
    assert delta == 1.5

    # Unexpected drop / meter swap: 1001.5 -> 0.2 kWh
    delta_swap = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_TOTAL,
        last_val=1001.5,
        current_val=0.2,
        unit=UNIT_KWH,
    )
    # Reset close to 0 treated as zero reset = 0.2 kWh
    assert delta_swap == 0.2

    # Unexpected drop to non-zero (recalibration): 1001.5 -> 950.0
    delta_drop = calculate_energy_delta(
        SOURCE_TYPE_ENERGY_TOTAL,
        last_val=1001.5,
        current_val=950.0,
        unit=UNIT_KWH,
    )
    assert delta_drop == 0.0
