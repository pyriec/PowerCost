"""Unit tests for models and rollover tracking."""

from datetime import datetime, timezone
import pytest

from custom_components.electricity_cost.models import DeviceStatistics


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
