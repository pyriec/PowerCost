"""Coordinator for Electricity Cost integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .calculations import (
    calculate_energy_delta,
    calculate_power_consumption,
)
from .const import (
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_POWER,
)
from .models import DeviceConfig, DeviceStatistics, PricingConfig
from .pricing import PricingManager, parse_float_state
from .storage import ElectricityCostStorage

_LOGGER = logging.getLogger(__name__)


class ElectricityCostCoordinator:
    """Coordinates state changes, cost computation, and persistence."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        pricing_config: PricingConfig,
        devices: list[DeviceConfig],
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry_id = entry_id
        self.pricing_config = pricing_config
        self.devices: dict[str, DeviceConfig] = {d.device_id: d for d in devices}
        self.statistics: dict[str, DeviceStatistics] = {}

        self.storage = ElectricityCostStorage(hass, entry_id)
        self.pricing_manager = PricingManager(hass, pricing_config)

        self._listeners: list[Callable[[], None]] = []
        self._unsub_trackers: list[Callable[[], None]] = []

    async def async_setup(self) -> None:
        """Load stored statistics and start state tracking."""
        stored_stats = await self.storage.async_load()
        now_local = dt_util.now()

        for dev_id, dev_cfg in self.devices.items():
            if dev_id in stored_stats:
                stats = stored_stats[dev_id]
                stats.rollover_if_needed(now_local)
                self.statistics[dev_id] = stats
            else:
                stats = DeviceStatistics(device_id=dev_id)
                stats.rollover_if_needed(now_local)
                self.statistics[dev_id] = stats

            # Initialize baseline from current state if available and needed
            if stats.last_source_value is None and dev_cfg.source_entity:
                curr_state = self.hass.states.get(dev_cfg.source_entity)
                if curr_state:
                    val = parse_float_state(curr_state)
                    if val is not None:
                        stats.last_source_value = val
                        stats.last_source_timestamp = curr_state.last_updated.timestamp()
                        stats.last_source_unit = dev_cfg.source_unit or curr_state.attributes.get("unit_of_measurement")

        self._start_tracking()

        # Regular periodic check for day/month/year rollovers (every 10 minutes)
        self._unsub_trackers.append(
            async_track_time_interval(
                self.hass,
                self._async_handle_periodic_rollover,
                timedelta(minutes=10),
            )
        )

    @callback
    def async_register_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register entity update listener."""
        self._listeners.append(listener)

        @callback
        def unsub() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsub

    @callback
    def async_update_listeners(self) -> None:
        """Notify all registered sensor entities to write updated state."""
        for listener in list(self._listeners):
            try:
                listener()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Error updating sensor listener: %s", err)

    def _start_tracking(self) -> None:
        """Attach state change listeners for source entities and pricing entities."""
        # Unsubscribe existing listeners if re-configuring
        for unsub in self._unsub_trackers:
            unsub()
        self._unsub_trackers.clear()

        # Track pricing changes
        pricing_entities: list[str] = []
        if self.pricing_config.mode == PRICING_MODE_VARIABLE and self.pricing_config.variable_price_entity:
            pricing_entities.append(self.pricing_config.variable_price_entity)
        elif self.pricing_config.mode == PRICING_MODE_PEAK_OFFPEAK:
            for ent in (
                self.pricing_config.offpeak_price_entity,
                self.pricing_config.peak_price_entity,
                self.pricing_config.tariff_mode_entity,
            ):
                if ent:
                    pricing_entities.append(ent)

        if pricing_entities:
            self._unsub_trackers.append(
                async_track_state_change_event(
                    self.hass,
                    pricing_entities,
                    self._async_handle_pricing_change,
                )
            )

        # Track device sources
        source_entities = [d.source_entity for d in self.devices.values() if d.source_entity]
        if source_entities:
            self._unsub_trackers.append(
                async_track_state_change_event(
                    self.hass,
                    source_entities,
                    self._async_handle_device_source_change,
                )
            )
            try:
                from homeassistant.helpers.event import async_track_state_report_event
                self._unsub_trackers.append(
                    async_track_state_report_event(
                        self.hass,
                        source_entities,
                        self._async_handle_device_source_change,
                    )
                )
            except (ImportError, AttributeError):
                pass

    @callback
    def _async_handle_pricing_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle tariff or price change event."""
        _LOGGER.debug(
            "Pricing entity state changed: %s -> %s",
            event.data.get("entity_id"),
            event.data.get("new_state"),
        )
        # Notify entities so attributes reflecting current price or tariff update
        self.async_update_listeners()

    async def _async_handle_device_source_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle state change on a monitored device entity."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if not entity_id or not new_state:
            return

        # Find matching device
        matching_devices = [d for d in self.devices.values() if d.source_entity == entity_id]
        if not matching_devices:
            return

        for dev_cfg in matching_devices:
            await self._async_process_device_reading(dev_cfg, new_state, old_state)

    async def _async_process_device_reading(
        self,
        dev_cfg: DeviceConfig,
        new_state: Any,
        old_state: Any | None,
    ) -> None:
        """Process a single device reading, compute consumption & cost, and persist."""
        stats = self.statistics.setdefault(dev_cfg.device_id, DeviceStatistics(device_id=dev_cfg.device_id))
        now_local = dt_util.as_local(new_state.last_updated)
        stats.rollover_if_needed(now_local)

        curr_val = parse_float_state(new_state)
        if curr_val is None:
            # Device became unavailable or unknown: reset pointer so next reading is baseline
            stats.last_source_value = None
            stats.last_source_timestamp = None
            return

        curr_ts = new_state.last_updated.timestamp()
        unit = dev_cfg.source_unit or new_state.attributes.get("unit_of_measurement")

        last_val = stats.last_source_value
        last_ts = stats.last_source_timestamp

        # Initial baseline recording
        if last_val is None or last_ts is None:
            stats.last_source_value = curr_val
            stats.last_source_timestamp = curr_ts
            stats.last_source_unit = unit
            await self.async_save_data()
            self.async_update_listeners()
            return

        # Compute energy delta
        energy_kwh = 0.0
        if dev_cfg.source_type == SOURCE_TYPE_POWER:
            energy_kwh = calculate_power_consumption(
                last_val, curr_val, last_ts, curr_ts, unit
            )
        else:
            energy_kwh = calculate_energy_delta(
                dev_cfg.source_type, last_val, curr_val, unit
            )

        # Update last pointers
        stats.last_source_value = curr_val
        stats.last_source_timestamp = curr_ts
        stats.last_source_unit = unit

        if energy_kwh > 0.0:
            start_utc = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            end_utc = datetime.fromtimestamp(curr_ts, tz=timezone.utc)

            current_price = self.pricing_manager.get_current_price() or 0.0
            cost = self.pricing_manager.calculate_cost_for_time_range(
                start_time=start_utc,
                end_time=end_utc,
                energy_kwh=energy_kwh,
                fallback_price=current_price,
            )

            stats.add_consumption(energy_kwh, cost, now_local)
            await self.async_save_data()

        self.async_update_listeners()

    async def _async_handle_periodic_rollover(self, now: datetime) -> None:
        """Periodic check for calendar rollovers."""
        now_local = dt_util.as_local(now)
        has_changed = False
        for stats in self.statistics.values():
            if stats.rollover_if_needed(now_local):
                has_changed = True

        if has_changed:
            await self.async_save_data()
            self.async_update_listeners()

    async def async_save_data(self) -> None:
        """Save statistics to storage."""
        await self.storage.async_save(self.statistics)

    async def async_unload(self) -> None:
        """Clean up listeners and state tracking."""
        for unsub in self._unsub_trackers:
            unsub()
        self._unsub_trackers.clear()
        self._listeners.clear()
        await self.async_save_data()
