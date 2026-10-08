"""Unit tests for pricing logic and timeline slicing in electricity_cost."""

from datetime import datetime, timezone
import pytest

from homeassistant.core import State

from custom_components.electricity_cost.const import (
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
)
from custom_components.electricity_cost.models import PricingConfig
from custom_components.electricity_cost.pricing import (
    PriceInterval,
    PricingManager,
    extract_timeline_intervals,
    parse_float_state,
)


def test_parse_float_state():
    """Test state string parsing to float."""
    assert parse_float_state(State("sensor.test", "0.251")) == 0.251
    assert parse_float_state(State("sensor.test", "0,251")) == 0.251
    assert parse_float_state(State("sensor.test", "unavailable")) is None
    assert parse_float_state(State("sensor.test", "unknown")) is None
    assert parse_float_state(None) is None


def test_variable_pricing_manager(hass):
    """Test PricingManager in variable mode."""
    config = PricingConfig(
        mode=PRICING_MODE_VARIABLE,
        variable_price_entity="sensor.elec_price",
    )
    pm = PricingManager(hass, config)

    # Missing state
    assert pm.get_current_price() is None

    # State available
    hass.states.async_set("sensor.elec_price", "0.25")
    assert pm.get_current_price() == 0.25


def test_peak_offpeak_pricing_manager(hass):
    """Test PricingManager in peak/off-peak mode with dynamic state matching."""
    config = PricingConfig(
        mode=PRICING_MODE_PEAK_OFFPEAK,
        offpeak_price_entity="sensor.hc_price",
        peak_price_entity="sensor.hp_price",
        tariff_mode_entity="sensor.current_mode",
        offpeak_state="heures_creuses",
        peak_state="heures_pleines",
    )
    pm = PricingManager(hass, config)

    hass.states.async_set("sensor.hc_price", "0.18")
    hass.states.async_set("sensor.hp_price", "0.27")

    # In HC
    hass.states.async_set("sensor.current_mode", "heures_creuses")
    assert pm.get_current_price() == 0.18

    # In HP
    hass.states.async_set("sensor.current_mode", "heures_pleines")
    assert pm.get_current_price() == 0.27

    # Unknown mode
    hass.states.async_set("sensor.current_mode", "autre")
    assert pm.get_current_price() is None


def test_calculate_cost_with_time_slicing(hass):
    """Test the exact requirement #10 from prompt:

    10:30 to 11:30: 1.0 kWh
    10:30 -> 11:00: price = 0.20 €/kWh
    11:00 -> 11:30: price = 0.30 €/kWh
    Expected cost = 0.5 * 0.20 + 0.5 * 0.30 = 0.25 €
    """
    config = PricingConfig(mode=PRICING_MODE_VARIABLE)
    pm = PricingManager(hass, config)

    t10_30 = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)
    t11_00 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
    t11_30 = datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc)

    intervals = [
        PriceInterval(start=t10_30, end=t11_00, price=0.20),
        PriceInterval(start=t11_00, end=t11_30, price=0.30),
    ]

    cost = pm.calculate_cost_for_time_range(
        start_time=t10_30,
        end_time=t11_30,
        energy_kwh=1.0,
        price_intervals=intervals,
        fallback_price=0.20,
    )

    assert cost == 0.25


def test_extract_timeline_intervals():
    """Test building intervals from state history."""
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    s1 = State("sensor.price", "0.20", last_updated=t0)
    s2 = State("sensor.price", "0.30", last_updated=t1)

    intervals = extract_timeline_intervals(
        mode=PRICING_MODE_VARIABLE,
        price_states=[s1, s2],
        start_bound=t0,
        end_bound=t2,
    )

    assert len(intervals) == 2
    assert intervals[0].start == t0
    assert intervals[0].end == t1
    assert intervals[0].price == 0.20

    assert intervals[1].start == t1
    assert intervals[1].end == t2
    assert intervals[1].price == 0.30
