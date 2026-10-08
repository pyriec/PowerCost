"""History reconstruction service and recorder interface for Electricity Cost."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .calculations import (
    calculate_energy_delta,
    calculate_power_consumption,
    normalize_power_to_kw,
)
from .const import (
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_POWER,
)
from .models import DeviceConfig, DeviceStatistics, PricingConfig
from .pricing import extract_timeline_intervals, parse_float_state

if TYPE_CHECKING:
    from .coordinator import ElectricityCostCoordinator

_LOGGER = logging.getLogger(__name__)


class HistoryRebuilder:
    """Handles querying the Recorder and rebuilding historical consumption and costs."""

    def __init__(self, hass: HomeAssistant, coordinator: ElectricityCostCoordinator) -> None:
        """Initialize history rebuilder."""
        self.hass = hass
        self.coordinator = coordinator

    async def async_rebuild_device(
        self,
        device_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Rebuild history for a specific device between start_date and end_date."""
        device_cfg = self.coordinator.devices.get(device_id)
        if not device_cfg:
            raise ValueError(f"Device {device_id} not found in configuration")

        pricing_cfg = self.coordinator.pricing_config

        # Execute recorder DB queries in executor thread to prevent event loop lag
        entity_ids_to_query = [device_cfg.source_entity]
        if pricing_cfg.mode == PRICING_MODE_VARIABLE and pricing_cfg.variable_price_entity:
            entity_ids_to_query.append(pricing_cfg.variable_price_entity)
        elif pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            for ent in (
                pricing_cfg.offpeak_price_entity,
                pricing_cfg.peak_price_entity,
                pricing_cfg.tariff_mode_entity,
            ):
                if ent:
                    entity_ids_to_query.append(ent)

        _LOGGER.info(
            "Starting history rebuild for device '%s' (%s) from %s to %s",
            device_cfg.name,
            device_cfg.source_entity,
            start_date.isoformat(),
            end_date.isoformat(),
        )

        history_data = await self.hass.async_add_executor_job(
            self._fetch_history,
            start_date,
            end_date,
            entity_ids_to_query,
        )

        source_states = history_data.get(device_cfg.source_entity, [])
        if not source_states:
            _LOGGER.warning(
                "No recorder history found for source entity %s during the requested period",
                device_cfg.source_entity,
            )
            return {
                "success": False,
                "message": f"No data found in recorder for {device_cfg.source_entity}",
                "records_processed": 0,
            }

        # Build price timeline intervals
        price_states = history_data.get(pricing_cfg.variable_price_entity or "", [])
        offpeak_states = history_data.get(pricing_cfg.offpeak_price_entity or "", [])
        peak_states = history_data.get(pricing_cfg.peak_price_entity or "", [])
        mode_states = history_data.get(pricing_cfg.tariff_mode_entity or "", [])

        price_intervals = extract_timeline_intervals(
            mode=pricing_cfg.mode,
            price_states=price_states,
            offpeak_states=offpeak_states,
            peak_states=peak_states,
            mode_states=mode_states,
            offpeak_target=pricing_cfg.offpeak_state,
            peak_target=pricing_cfg.peak_state,
            start_bound=start_date,
            end_bound=end_date,
        )

        # Fallback price from coordinator if needed
        fallback_price = self.coordinator.pricing_manager.get_current_price() or 0.25

        # Reconstruct new statistics
        stats = DeviceStatistics(device_id=device_id)

        # Chronological replay
        sorted_source_states = sorted(source_states, key=lambda s: s.last_updated)

        last_val: float | None = None
        last_ts: float | None = None
        unit = device_cfg.source_unit

        for state in sorted_source_states:
            val = parse_float_state(state)
            if val is None:
                continue

            current_ts = state.last_updated.timestamp()
            dt_local = dt_util.as_local(state.last_updated)

            if unit is None:
                unit = state.attributes.get("unit_of_measurement")

            if last_val is not None and last_ts is not None:
                step_start_utc = datetime.fromtimestamp(last_ts, tz=timezone.utc)
                step_end_utc = datetime.fromtimestamp(current_ts, tz=timezone.utc)

                energy_delta = 0.0
                if device_cfg.source_type == SOURCE_TYPE_POWER:
                    energy_delta = calculate_power_consumption(
                        last_val, val, last_ts, current_ts, unit
                    )
                else:
                    energy_delta = calculate_energy_delta(
                        device_cfg.source_type, last_val, val, unit
                    )

                if energy_delta > 0.0:
                    step_cost = self.coordinator.pricing_manager.calculate_cost_for_time_range(
                        start_time=step_start_utc,
                        end_time=step_end_utc,
                        energy_kwh=energy_delta,
                        price_intervals=price_intervals,
                        fallback_price=fallback_price,
                    )
                    stats.add_consumption(energy_delta, step_cost, dt_local)

            last_val = val
            last_ts = current_ts

        # Preserve latest current values & mark rebuild
        stats.last_source_value = last_val
        stats.last_source_timestamp = last_ts
        stats.last_source_unit = unit
        stats.last_rebuild_timestamp = dt_util.utcnow().timestamp()

        # Update in-memory coordinator & persist
        self.coordinator.statistics[device_id] = stats
        await self.coordinator.async_save_data()
        self.coordinator.async_update_listeners()

        _LOGGER.info(
            "Rebuild complete for %s. Total cost: %.2f EUR, Total energy: %.2f kWh across %d states",
            device_cfg.name,
            stats.cost_total,
            stats.energy_total,
            len(sorted_source_states),
        )

        return {
            "success": True,
            "device_id": device_id,
            "cost_total": stats.cost_total,
            "energy_total": stats.energy_total,
            "records_processed": len(sorted_source_states),
        }

    def _fetch_history(
        self,
        start_date: datetime,
        end_date: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[State]]:
        """Run blocking recorder query inside executor."""
        result: dict[str, list[State]] = {}
        for ent_id in entity_ids:
            changes = history.state_changes_during_period(
                self.hass,
                start_date,
                end_date,
                entity_id=ent_id,
                include_start_time_state=True,
            )
            result.update(changes)
        return result
