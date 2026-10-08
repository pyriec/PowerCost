"""Unit tests for storage persistence in electricity_cost."""

import pytest

from custom_components.electricity_cost.models import DeviceStatistics
from custom_components.electricity_cost.storage import ElectricityCostStorage


@pytest.mark.asyncio
async def test_storage_save_and_load(hass):
    """Test saving and restoring device statistics."""
    storage = ElectricityCostStorage(hass, "test_entry_123")

    # Initial empty load
    initial_data = await storage.async_load()
    assert initial_data == {}

    # Save stats for device
    dev_stats = DeviceStatistics(
        device_id="dryer",
        cost_today=1.25,
        cost_total=45.60,
        energy_today=5.0,
        energy_total=180.0,
        max_cost_day=3.50,
        daily_history={"2026-03-01": 2.10},
    )

    await storage.async_save({"dryer": dev_stats})

    # Reload from store
    reloaded = await storage.async_load()
    assert "dryer" in reloaded
    restored = reloaded["dryer"]

    assert restored.cost_today == 1.25
    assert restored.cost_total == 45.60
    assert restored.energy_today == 5.0
    assert restored.energy_total == 180.0
    assert restored.max_cost_day == 3.50
    assert restored.daily_history == {"2026-03-01": 2.10}
