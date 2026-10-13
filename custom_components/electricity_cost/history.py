"""History reconstruction service and recorder interface for PowerCost."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .calculations import (
    calculate_energy_delta,
    calculate_power_consumption,
    normalize_energy_to_kwh,
    normalize_power_to_kw,
)
from .const import (
    ATTR_DATA_SOURCE,
    DATA_SOURCE_MIXED,
    DATA_SOURCE_RECORDER,
    DATA_SOURCE_STATISTICS,
    DOMAIN,
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
    SENSOR_COST_DAY,
    SENSOR_COST_DAY_OFFPEAK,
    SENSOR_COST_DAY_PEAK,
    SENSOR_COST_MONTH,
    SENSOR_COST_MONTH_OFFPEAK,
    SENSOR_COST_MONTH_PEAK,
    SENSOR_COST_TOTAL,
    SENSOR_COST_TOTAL_OFFPEAK,
    SENSOR_COST_TOTAL_PEAK,
    SENSOR_COST_YEAR,
    SENSOR_COST_YEAR_OFFPEAK,
    SENSOR_COST_YEAR_PEAK,
    SENSOR_COST_MAX_DAY,
    SENSOR_COST_MAX_MONTH,
    SENSOR_COST_MAX_YEAR,
    SENSOR_COST_AVG_DAY,
    SENSOR_COST_AVG_MONTH,
    SENSOR_COST_AVG_YEAR,
    SENSOR_ENERGY_DAY_OFFPEAK,
    SENSOR_ENERGY_DAY_PEAK,
    SENSOR_ENERGY_MONTH_OFFPEAK,
    SENSOR_ENERGY_MONTH_PEAK,
    SENSOR_ENERGY_YEAR_OFFPEAK,
    SENSOR_ENERGY_YEAR_PEAK,
    SENSOR_ENERGY_TOTAL_OFFPEAK,
    SENSOR_ENERGY_TOTAL_PEAK,
    SENSOR_TYPES,
    SOURCE_TYPE_POWER,
)
from .models import DeviceConfig, DeviceStatistics, PricingConfig
from .pricing import PriceInterval, extract_timeline_intervals, parse_float_state

if TYPE_CHECKING:
    from .coordinator import ElectricityCostCoordinator

_LOGGER = logging.getLogger(__name__)

# Duration of each processing chunk (15 days)
CHUNK_DURATION_DAYS = 15

SENSOR_METADATA_DEFAULTS: dict[str, dict[str, Any]] = {
    SENSOR_COST_DAY: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_MONTH: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_YEAR: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_TOTAL: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_DAY_OFFPEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_DAY_PEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_MONTH_OFFPEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_MONTH_PEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_YEAR_OFFPEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_YEAR_PEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_TOTAL_OFFPEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_COST_TOTAL_PEAK: {"unit_of_measurement": "€", "device_class": "monetary", "state_class": "total"},
    SENSOR_ENERGY_DAY_OFFPEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_DAY_PEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_MONTH_OFFPEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_MONTH_PEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_YEAR_OFFPEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_YEAR_PEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total"},
    SENSOR_ENERGY_TOTAL_OFFPEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    SENSOR_ENERGY_TOTAL_PEAK: {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    SENSOR_COST_MAX_DAY: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
    SENSOR_COST_MAX_MONTH: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
    SENSOR_COST_MAX_YEAR: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
    SENSOR_COST_AVG_DAY: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
    SENSOR_COST_AVG_MONTH: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
    SENSOR_COST_AVG_YEAR: {"unit_of_measurement": "€", "device_class": None, "state_class": "measurement"},
}


def _get_sensor_stat_value(stats: DeviceStatistics, sensor_type: str) -> float | None:
    """Get the current rounded value for a given sensor type from device statistics."""
    mapping: dict[str, float] = {
        SENSOR_COST_DAY: stats.cost_today,
        SENSOR_COST_MONTH: stats.cost_this_month,
        SENSOR_COST_YEAR: stats.cost_this_year,
        SENSOR_COST_TOTAL: stats.cost_total,
        SENSOR_COST_DAY_OFFPEAK: stats.cost_today_offpeak,
        SENSOR_COST_DAY_PEAK: stats.cost_today_peak,
        SENSOR_COST_MONTH_OFFPEAK: stats.cost_this_month_offpeak,
        SENSOR_COST_MONTH_PEAK: stats.cost_this_month_peak,
        SENSOR_COST_YEAR_OFFPEAK: stats.cost_this_year_offpeak,
        SENSOR_COST_YEAR_PEAK: stats.cost_this_year_peak,
        SENSOR_COST_TOTAL_OFFPEAK: stats.cost_total_offpeak,
        SENSOR_COST_TOTAL_PEAK: stats.cost_total_peak,
        SENSOR_ENERGY_DAY_OFFPEAK: stats.energy_today_offpeak,
        SENSOR_ENERGY_DAY_PEAK: stats.energy_today_peak,
        SENSOR_ENERGY_MONTH_OFFPEAK: stats.energy_this_month_offpeak,
        SENSOR_ENERGY_MONTH_PEAK: stats.energy_this_month_peak,
        SENSOR_ENERGY_YEAR_OFFPEAK: stats.energy_this_year_offpeak,
        SENSOR_ENERGY_YEAR_PEAK: stats.energy_this_year_peak,
        SENSOR_ENERGY_TOTAL_OFFPEAK: stats.energy_total_offpeak,
        SENSOR_ENERGY_TOTAL_PEAK: stats.energy_total_peak,
        SENSOR_COST_MAX_DAY: stats.max_cost_day,
        SENSOR_COST_MAX_MONTH: stats.max_cost_month,
        SENSOR_COST_MAX_YEAR: stats.max_cost_year,
        SENSOR_COST_AVG_DAY: stats.avg_cost_day,
        SENSOR_COST_AVG_MONTH: stats.avg_cost_month,
        SENSOR_COST_AVG_YEAR: stats.avg_cost_year,
    }
    val = mapping.get(sensor_type)
    return round(val, 2) if val is not None else None


class _RawStatesTracker:
    """Tracks state changes for PowerCost sensors during high-precision replay."""

    def __init__(self, active_sensors: list[str]) -> None:
        self.active_sensors = active_sensors
        self.raw_states: dict[str, list[tuple[float, float]]] = {s: [] for s in active_sensors}
        self.last_values: dict[str, float] = {}
        self.last_timestamps: dict[str, float] = {}

    def record_initial_state(self, stats: DeviceStatistics, ts: float) -> None:
        """Record initial starting point for each active sensor."""
        for s_key in self.active_sensors:
            val = _get_sensor_stat_value(stats, s_key) or 0.0
            self.raw_states[s_key].append((ts, val))
            self.last_values[s_key] = val
            self.last_timestamps[s_key] = ts

    def record_rollover(self, period_type: str, ts: float) -> None:
        """Record reset to 0.0 at rollover transition."""
        if period_type == "day":
            keys = [
                SENSOR_COST_DAY,
                SENSOR_COST_DAY_OFFPEAK,
                SENSOR_COST_DAY_PEAK,
                SENSOR_ENERGY_DAY_OFFPEAK,
                SENSOR_ENERGY_DAY_PEAK,
            ]
        elif period_type == "month":
            keys = [
                SENSOR_COST_MONTH,
                SENSOR_COST_MONTH_OFFPEAK,
                SENSOR_COST_MONTH_PEAK,
                SENSOR_ENERGY_MONTH_OFFPEAK,
                SENSOR_ENERGY_MONTH_PEAK,
            ]
        elif period_type == "year":
            keys = [
                SENSOR_COST_YEAR,
                SENSOR_COST_YEAR_OFFPEAK,
                SENSOR_COST_YEAR_PEAK,
                SENSOR_ENERGY_YEAR_OFFPEAK,
                SENSOR_ENERGY_YEAR_PEAK,
            ]
        else:
            return

        for s_key in keys:
            if s_key in self.active_sensors:
                last_ts = self.last_timestamps.get(s_key, 0.0)
                record_ts = ts if ts > last_ts else last_ts + 0.001
                self.raw_states[s_key].append((record_ts, 0.0))
                self.last_values[s_key] = 0.0
                self.last_timestamps[s_key] = record_ts

    def record_changes(self, stats: DeviceStatistics, ts: float) -> None:
        """Record sensor values if they changed."""
        for s_key in self.active_sensors:
            val = _get_sensor_stat_value(stats, s_key)
            if val is None:
                continue
            if s_key not in self.last_values or val != self.last_values[s_key]:
                last_ts = self.last_timestamps.get(s_key, 0.0)
                record_ts = ts if ts > last_ts else last_ts + 0.001
                self.raw_states[s_key].append((record_ts, val))
                self.last_values[s_key] = val
                self.last_timestamps[s_key] = record_ts



def normalize_data_source(val: str | None) -> str:
    """Normalize user input data source to standard constant."""
    if not val:
        return DATA_SOURCE_MIXED
    val = str(val).lower().strip()
    if val in ("recorder", "recorder_only", "raw", "precise"):
        return DATA_SOURCE_RECORDER
    if val in ("statistics", "lts", "lts_only", "long_term"):
        return DATA_SOURCE_STATISTICS
    return DATA_SOURCE_MIXED


def _parse_stat_timestamp(val: Any) -> datetime:
    """Parse timestamp or datetime from statistic entry."""
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    if isinstance(val, datetime):
        return dt_util.as_utc(val)
    return dt_util.utcnow()


def _send_progress_notification(
    hass: HomeAssistant,
    device_id: str,
    title: str,
    message: str,
) -> None:
    """Send or update a persistent notification in Home Assistant UI."""
    try:
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            hass,
            message,
            title=title,
            notification_id=f"powercost_rebuild_{device_id}",
        )
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.debug("Could not create persistent notification: %s", err)


async def async_clear_recorder_statistics(hass: HomeAssistant, statistic_ids: list[str]) -> bool:
    """Clear long-term statistics from Home Assistant recorder."""
    if not statistic_ids:
        return True
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import ClearStatisticsTask

        instance = get_instance(hass)
        if not instance or not hasattr(instance, "queue_task"):
            return False

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def on_done(*args: Any, **kwargs: Any) -> None:
            res = args[0] if args else True
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, res)

        instance.queue_task(ClearStatisticsTask(on_done=on_done, statistic_ids=statistic_ids))
        return await asyncio.wait_for(future, timeout=60.0)
    except asyncio.TimeoutError:
        _LOGGER.warning("Timeout while clearing recorder statistics for %s", statistic_ids)
        return False
    except Exception as err:
        _LOGGER.warning("Failed to clear recorder statistics for %s: %s", statistic_ids, err)
        return False


async def async_purge_recorder_entity_states(hass: HomeAssistant, entity_ids: list[str]) -> None:
    """Purge state history for entity IDs."""
    if not entity_ids:
        return
    try:
        if (
            hasattr(hass, "services")
            and hasattr(hass.services, "has_service")
            and hass.services.has_service("recorder", "purge_entities")
        ):
            await hass.services.async_call(
                "recorder",
                "purge_entities",
                {"entity_id": entity_ids, "keep_days": 0},
                blocking=True,
            )
    except Exception as err:
        _LOGGER.warning("Failed to purge recorder states for %s: %s", entity_ids, err)


def get_device_entity_ids(hass: HomeAssistant, coordinator_entry_id: str, device_id: str) -> list[str]:
    """Get all entity IDs belonging to a device."""
    found: list[str] = []
    try:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        prefix = f"{coordinator_entry_id}_{device_id}_"
        if hasattr(ent_reg, "entities"):
            for ent in ent_reg.entities.values():
                if getattr(ent, "platform", None) == DOMAIN and getattr(ent, "unique_id", "").startswith(prefix):
                    found.append(ent.entity_id)

        # Supplementary check using known SENSOR_TYPES
        for s_type in SENSOR_TYPES:
            ent_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{prefix}{s_type}")
            if ent_id and ent_id not in found:
                found.append(ent_id)
    except Exception as err:
        _LOGGER.debug("Could not lookup entity IDs for %s: %s", device_id, err)
    return found


async def async_purge_device_data_and_history(
    hass: HomeAssistant,
    coordinator: ElectricityCostCoordinator,
    device_id: str,
    notify_listeners: bool = False,
) -> None:
    """Completely wipe storage data and recorder statistics for a device."""
    entity_ids = get_device_entity_ids(hass, coordinator.entry_id, device_id)
    _LOGGER.info("Purging all data and history for device %s (entities: %s)", device_id, entity_ids)

    # 1. Clear Long-Term Statistics and entity state history in recorder
    if entity_ids:
        await async_clear_recorder_statistics(hass, entity_ids)
        await async_purge_recorder_entity_states(hass, entity_ids)

    # 2. Reset in-memory and stored statistics
    coordinator.statistics[device_id] = DeviceStatistics(device_id=device_id)
    await coordinator.async_save_data()
    if notify_listeners:
        coordinator.async_update_listeners()


class HistoryRebuilder:
    """Handles querying the Recorder and rebuilding historical consumption and costs."""

    def __init__(self, hass: HomeAssistant, coordinator: ElectricityCostCoordinator) -> None:
        """Initialize history rebuilder."""
        self.hass = hass
        self.coordinator = coordinator

    async def _async_run_recorder_job(self, target: Any, *args: Any) -> Any:
        """Run a database query using the recorder's dedicated executor."""
        try:
            from homeassistant.components.recorder import get_instance
            rec = get_instance(self.hass)
            if rec:
                return await rec.async_add_executor_job(target, *args)
        except Exception:
            pass
        return await self.hass.async_add_executor_job(target, *args)

    @staticmethod
    def _slice_step_by_hours(
        start_dt: datetime, end_dt: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Slice an interval [start_dt, end_dt] into hourly segments aligned with UTC hour boundaries."""
        if end_dt <= start_dt:
            return []
        slices: list[tuple[datetime, datetime]] = []
        curr = start_dt
        while curr < end_dt:
            next_hour = curr.astimezone(timezone.utc).replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
            slice_end = min(next_hour, end_dt)
            slices.append((curr, slice_end))
            curr = slice_end
        return slices

    def _compute_profile_weights(
        self,
        lts_stats: list[dict[str, Any]],
    ) -> dict[int, float]:
        """Compute relative hourly consumption weights (0-23) based on LTS history."""
        hourly_samples: dict[int, list[float]] = {h: [] for h in range(24)}
        for item in lts_stats:
            start_val = item.get("start")
            if not start_val:
                continue
            dt_item = _parse_stat_timestamp(start_val)
            h = dt_util.as_local(dt_item).hour
            val = item.get("change")
            if val is None or val < 0:
                val = item.get("mean")
            if val is not None and val > 0:
                hourly_samples[h].append(float(val))

        weights: dict[int, float] = {}
        all_vals: list[float] = [v for samples in hourly_samples.values() for v in samples]
        overall_avg = sum(all_vals) / len(all_vals) if all_vals else 1.0

        for h in range(24):
            if hourly_samples[h]:
                weights[h] = sum(hourly_samples[h]) / len(hourly_samples[h])
            else:
                weights[h] = overall_avg

        return weights

    def _find_earliest_state_timestamp(
        self,
        start_date: datetime,
        end_date: datetime,
        entity_id: str,
    ) -> datetime | None:
        """Find the earliest actual recorded state for an entity in the period."""
        try:
            chunk_delta = timedelta(days=CHUNK_DURATION_DAYS)
            curr_start = start_date
            while curr_start < end_date:
                curr_end = min(curr_start + chunk_delta, end_date)
                chunk_hist = self._fetch_history(curr_start, curr_end, [entity_id])
                states = chunk_hist.get(entity_id, [])
                for s in states:
                    if s.state not in ("unavailable", "unknown", ""):
                        s_dt = dt_util.as_utc(s.last_updated)
                        if s_dt <= start_date + timedelta(hours=2):
                            return start_date
                        return s_dt
                if states:
                    s_dt = dt_util.as_utc(states[0].last_updated)
                    if s_dt <= start_date + timedelta(hours=2):
                        return start_date
                    return s_dt
                curr_start = curr_end
        except Exception as err:
            _LOGGER.debug("Could not find earliest state for %s: %s", entity_id, err)
        return None

    def _process_step_consumption(
        self,
        step_start_utc: datetime,
        step_end_utc: datetime,
        energy_kwh: float,
        price_intervals: list[PriceInterval],
        fallback_price: float | None,
        profile_weights: dict[int, float],
        stats: DeviceStatistics,
        hourly_cost_deltas: dict[datetime, float],
        hourly_cost_deltas_offpeak: dict[datetime, float],
        hourly_cost_deltas_peak: dict[datetime, float],
        hourly_energy_deltas_offpeak: dict[datetime, float],
        hourly_energy_deltas_peak: dict[datetime, float],
        raw_states_tracker: _RawStatesTracker | None = None,
    ) -> None:
        """Distribute energy and cost across hour slices, applying accurate tariffs per slice."""
        slices = self._slice_step_by_hours(step_start_utc, step_end_utc)
        if not slices:
            return

        total_duration = max(0.001, (step_end_utc - step_start_utc).total_seconds())

        # If step is multi-hour, weight by profile of each slice's local hour
        if len(slices) > 1 and total_duration > 3600.0:
            slice_weights: list[float] = []
            for s_start, s_end in slices:
                loc_h = dt_util.as_local(s_start).hour
                dur = (s_end - s_start).total_seconds()
                w = max(0.0001, profile_weights.get(loc_h, 1.0)) * (dur / 3600.0)
                slice_weights.append(w)
            sum_w = sum(slice_weights) or 1.0
            slice_fractions = [w / sum_w for w in slice_weights]
        else:
            slice_fractions = [(s_end - s_start).total_seconds() / total_duration for s_start, s_end in slices]

        for (s_start, s_end), frac in zip(slices, slice_fractions):
            slice_energy = energy_kwh * frac
            if slice_energy <= 0.0:
                continue

            detailed = self.coordinator.pricing_manager.calculate_detailed_cost_for_time_range(
                start_time=s_start,
                end_time=s_end,
                energy_kwh=slice_energy,
                price_intervals=price_intervals,
                fallback_price=fallback_price,
            )

            dt_local = dt_util.as_local(s_end)

            if raw_states_tracker is not None:
                day_str = dt_local.strftime("%Y-%m-%d")
                if stats.current_day and day_str != stats.current_day:
                    midnight_ts = dt_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).timestamp()
                    raw_states_tracker.record_rollover("day", midnight_ts)
                month_str = dt_local.strftime("%Y-%m")
                if stats.current_month and month_str != stats.current_month:
                    month_start_ts = dt_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).timestamp()
                    raw_states_tracker.record_rollover("month", month_start_ts)
                year_str = dt_local.strftime("%Y")
                if stats.current_year and year_str != stats.current_year:
                    year_start_ts = dt_local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).timestamp()
                    raw_states_tracker.record_rollover("year", year_start_ts)

            stats.add_consumption(
                energy_kwh=slice_energy,
                cost=detailed.total_cost,
                timestamp=dt_local,
                cost_offpeak=detailed.cost_offpeak,
                cost_peak=detailed.cost_peak,
                energy_offpeak=detailed.energy_offpeak,
                energy_peak=detailed.energy_peak,
            )

            if raw_states_tracker is not None:
                raw_states_tracker.record_changes(stats, s_end.timestamp())

            # Record in hourly bucket at the START of the hour
            h_bucket = s_start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            hourly_cost_deltas[h_bucket] = hourly_cost_deltas.get(h_bucket, 0.0) + detailed.total_cost
            if detailed.cost_offpeak > 0 or detailed.energy_offpeak > 0:
                hourly_cost_deltas_offpeak[h_bucket] = (
                    hourly_cost_deltas_offpeak.get(h_bucket, 0.0) + detailed.cost_offpeak
                )
                hourly_energy_deltas_offpeak[h_bucket] = (
                    hourly_energy_deltas_offpeak.get(h_bucket, 0.0) + detailed.energy_offpeak
                )
            if detailed.cost_peak > 0 or detailed.energy_peak > 0:
                hourly_cost_deltas_peak[h_bucket] = (
                    hourly_cost_deltas_peak.get(h_bucket, 0.0) + detailed.cost_peak
                )
                hourly_energy_deltas_peak[h_bucket] = (
                    hourly_energy_deltas_peak.get(h_bucket, 0.0) + detailed.energy_peak
                )

    async def async_rebuild_device(
        self,
        device_id: str,
        start_date: datetime,
        end_date: datetime,
        clear_existing: bool = True,
        data_source: str = DATA_SOURCE_MIXED,
    ) -> dict[str, Any]:
        """Rebuild history for a specific device between start_date and end_date."""
        device_cfg = self.coordinator.devices.get(device_id)
        if not device_cfg:
            raise ValueError(f"Appareil '{device_id}' non trouvé dans la configuration PowerCost")

        self.coordinator.mark_device_rebuilding(device_id, True)
        try:
            return await self._async_rebuild_device_impl(
                device_id=device_id,
                device_cfg=device_cfg,
                start_date=start_date,
                end_date=end_date,
                clear_existing=clear_existing,
                data_source=data_source,
            )
        finally:
            self.coordinator.mark_device_rebuilding(device_id, False)

    async def _async_rebuild_device_impl(
        self,
        device_id: str,
        device_cfg: DeviceConfig,
        start_date: datetime,
        end_date: datetime,
        clear_existing: bool = True,
        data_source: str = DATA_SOURCE_MIXED,
    ) -> dict[str, Any]:
        """Internal implementation of rebuilding device history."""
        pricing_cfg = self.coordinator.pricing_config
        norm_source = normalize_data_source(data_source)

        fmt = "%d/%m/%Y %H:%M"
        start_fmt = dt_util.as_local(start_date).strftime(fmt)
        end_fmt = dt_util.as_local(end_date).strftime(fmt)

        # 1. Fetch long-term statistics if needed (for statistics mode, mixed mode, or profile weights)
        all_lts_stats: list[dict[str, Any]] = []
        if norm_source in (DATA_SOURCE_STATISTICS, DATA_SOURCE_MIXED) or device_cfg.source_type == SOURCE_TYPE_POWER:
            all_lts_stats = await self._async_run_recorder_job(
                self._fetch_statistics,
                start_date,
                end_date,
                device_cfg.source_entity,
            )
        profile_weights = self._compute_profile_weights(all_lts_stats)

        # 2. Validate data availability and determine phase boundaries based on selected data_source
        lts_start_boundary: datetime | None = None
        lts_end_boundary: datetime | None = None
        raw_start_boundary: datetime | None = None
        raw_end_boundary: datetime | None = None

        if norm_source == DATA_SOURCE_STATISTICS:
            if not all_lts_stats:
                _LOGGER.warning(
                    "No long-term statistics found for entity %s between %s and %s",
                    device_cfg.source_entity,
                    start_date.isoformat(),
                    end_date.isoformat(),
                )
                _send_progress_notification(
                    self.hass,
                    device_id=device_id,
                    title=f"PowerCost - Aucune statistique trouvée ({device_cfg.name})",
                    message=(
                        f"⚠️ **Aucune statistique à long terme trouvée pour {device_cfg.name}**\n\n"
                        f"- **Période demandée** : du {start_fmt} au {end_fmt}\n"
                        f"- Aucune mesure n'a été trouvée dans les statistiques à long terme pour `{device_cfg.source_entity}`."
                    ),
                )
                return {
                    "success": False,
                    "message": (
                        f"Aucune statistique à long terme trouvée pour '{device_cfg.source_entity}' "
                        f"entre {start_fmt} et {end_fmt}."
                    ),
                    "records_processed": 0,
                }
            lts_start_boundary = start_date
            lts_end_boundary = end_date
            raw_start_boundary = None
            raw_end_boundary = None

        elif norm_source == DATA_SOURCE_RECORDER:
            earliest_raw_dt = await self._async_run_recorder_job(
                self._find_earliest_state_timestamp,
                start_date,
                end_date,
                device_cfg.source_entity,
            )
            if earliest_raw_dt is None:
                _LOGGER.error(
                    "No recorder history found for entity %s between %s and %s",
                    device_cfg.source_entity,
                    start_date.isoformat(),
                    end_date.isoformat(),
                )
                _send_progress_notification(
                    self.hass,
                    device_id=device_id,
                    title=f"PowerCost - Données Recorder introuvables ({device_cfg.name})",
                    message=(
                        f"❌ **Aucune donnée Recorder trouvée pour {device_cfg.name}**\n\n"
                        f"- **Période demandée** : du {start_fmt} au {end_fmt}\n"
                        f"- Aucune donnée brute n'a été trouvée dans le Recorder pour `{device_cfg.source_entity}`. Les données ont probablement été purgées.\n\n"
                        f"👉 *Utilisez le mode 'Mixte' ou 'Statistiques à long terme' pour exploiter l'historique.*"
                    ),
                )
                return {
                    "success": False,
                    "message": (
                        f"Aucune donnée brute du Recorder trouvée pour '{device_cfg.name}' "
                        f"entre {start_fmt} et {end_fmt}. Les données ont probablement été purgées du Recorder."
                    ),
                    "records_processed": 0,
                }

            if earliest_raw_dt > start_date + timedelta(hours=2):
                earliest_fmt = dt_util.as_local(earliest_raw_dt).strftime(fmt)
                _LOGGER.error(
                    "Recorder data missing for %s on period %s to %s (earliest available: %s)",
                    device_cfg.name,
                    start_fmt,
                    earliest_fmt,
                    earliest_fmt,
                )
                _send_progress_notification(
                    self.hass,
                    device_id=device_id,
                    title=f"PowerCost - Données Recorder incomplètes ({device_cfg.name})",
                    message=(
                        f"❌ **Données Recorder incomplètes pour {device_cfg.name}**\n\n"
                        f"- **Période demandée** : du {start_fmt} au {end_fmt}\n"
                        f"- **Période manquante** : du {start_fmt} au {earliest_fmt}\n\n"
                        f"Impossible de reconstruire l'historique avec l'option **Recorder uniquement** : les données précises ne sont pas disponibles sur la période du {start_fmt} au {earliest_fmt}. "
                        f"La reconstruction avec le Recorder n'est possible qu'à partir du **{earliest_fmt}**.\n\n"
                        f"👉 *Vous pouvez lancer la reconstruction à partir du **{earliest_fmt}**, ou choisir le mode **Mixte** pour utiliser les statistiques à long terme sur la période manquante.*"
                    ),
                )
                return {
                    "success": False,
                    "message": (
                        f"Données Recorder indisponibles pour '{device_cfg.name}' sur la période du {start_fmt} au {earliest_fmt}. "
                        f"Impossible d'utiliser le Recorder sur cette période : les données précises ne sont disponibles qu'à partir du {earliest_fmt}. "
                        f"Vous pouvez refaire la demande à partir du {earliest_fmt} ou choisir le mode 'mixed' pour compléter avec les statistiques à long terme."
                    ),
                    "records_processed": 0,
                }

            lts_start_boundary = None
            lts_end_boundary = None
            raw_start_boundary = start_date
            raw_end_boundary = end_date

        else:
            # DATA_SOURCE_MIXED
            earliest_raw_dt = await self._async_run_recorder_job(
                self._find_earliest_state_timestamp,
                start_date,
                end_date,
                device_cfg.source_entity,
            )
            if earliest_raw_dt is None:
                lts_start_boundary = start_date
                lts_end_boundary = end_date
                raw_start_boundary = None
                raw_end_boundary = None
            elif earliest_raw_dt <= start_date + timedelta(hours=2):
                lts_start_boundary = None
                lts_end_boundary = None
                raw_start_boundary = start_date
                raw_end_boundary = end_date
            else:
                lts_start_boundary = start_date
                lts_end_boundary = earliest_raw_dt
                raw_start_boundary = earliest_raw_dt
                raw_end_boundary = end_date

        lts_period_used: tuple[datetime, datetime] | None = None
        if lts_end_boundary is not None:
            lts_period_used = (lts_start_boundary or start_date, lts_end_boundary)

        raw_period_used: tuple[datetime, datetime] | None = None
        if raw_start_boundary is not None:
            raw_period_used = (raw_start_boundary, raw_end_boundary or end_date)

        _LOGGER.info(
            "Rebuild execution plan for '%s': source=%s, lts=%s to %s, raw=%s to %s",
            device_cfg.name,
            norm_source,
            lts_period_used[0].isoformat() if lts_period_used else "None",
            lts_period_used[1].isoformat() if lts_period_used else "None",
            raw_period_used[0].isoformat() if raw_period_used else "None",
            raw_period_used[1].isoformat() if raw_period_used else "None",
        )

        # 3. Purge old recorder statistics and state history if requested (done without wiping in-memory state during rebuild)
        if clear_existing:
            _LOGGER.info("Purging old statistics before rebuild for %s", device_cfg.name)
            entity_ids = get_device_entity_ids(self.hass, self.coordinator.entry_id, device_id)
            if entity_ids:
                await async_clear_recorder_statistics(self.hass, entity_ids)
                await async_purge_recorder_entity_states(self.hass, entity_ids)

        # 4. Build list of entities to query
        entity_ids_to_query = [device_cfg.source_entity]
        if pricing_cfg.mode == PRICING_MODE_VARIABLE and pricing_cfg.variable_price_entity:
            entity_ids_to_query.append(pricing_cfg.variable_price_entity)
        elif pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            for ent in (
                pricing_cfg.offpeak_price_entity,
                pricing_cfg.peak_price_entity,
                pricing_cfg.tariff_mode_entity,
            ):
                if ent and ent not in entity_ids_to_query:
                    entity_ids_to_query.append(ent)

        # Pre-fetch pricing intervals across whole period
        fallback_price = self.coordinator.pricing_manager.get_current_price() or 0.25
        fallback_offpeak_price: float | None = None
        fallback_peak_price: float | None = None
        if pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            if pricing_cfg.offpeak_price_entity:
                fallback_offpeak_price = parse_float_state(
                    self.hass.states.get(pricing_cfg.offpeak_price_entity)
                )
            if pricing_cfg.peak_price_entity:
                fallback_peak_price = parse_float_state(
                    self.hass.states.get(pricing_cfg.peak_price_entity)
                )

        pricing_entities = [e for e in entity_ids_to_query if e != device_cfg.source_entity]
        price_states_all: dict[str, list[State]] = {}
        if pricing_entities:
            price_states_all = await self._async_run_recorder_job(
                self._fetch_history,
                start_date,
                end_date,
                pricing_entities,
            )

        price_intervals = extract_timeline_intervals(
            mode=pricing_cfg.mode,
            price_states=price_states_all.get(pricing_cfg.variable_price_entity or "", []),
            offpeak_states=price_states_all.get(pricing_cfg.offpeak_price_entity or "", []),
            peak_states=price_states_all.get(pricing_cfg.peak_price_entity or "", []),
            mode_states=price_states_all.get(pricing_cfg.tariff_mode_entity or "", []),
            offpeak_target=pricing_cfg.offpeak_state,
            peak_target=pricing_cfg.peak_state,
            start_bound=start_date,
            end_bound=end_date,
            fallback_price=fallback_price,
            fallback_offpeak_price=fallback_offpeak_price,
            fallback_peak_price=fallback_peak_price,
        )

        # Runtime structures
        stats = DeviceStatistics(device_id=device_id)
        unit = device_cfg.source_unit
        total_records_processed = 0

        # Hourly delta accumulators for retroactive statistics injection
        hourly_cost_deltas: dict[datetime, float] = {}
        hourly_cost_deltas_offpeak: dict[datetime, float] = {}
        hourly_cost_deltas_peak: dict[datetime, float] = {}
        hourly_energy_deltas_offpeak: dict[datetime, float] = {}
        hourly_energy_deltas_peak: dict[datetime, float] = {}

        last_val: float | None = None
        last_ts: float | None = None

        # Phase 1: Replay LTS for historical portion before raw states begin
        if lts_end_boundary is not None and all_lts_stats:
            prev_sum: float | None = None
            prev_state_val: float | None = None
            for stat_item in all_lts_stats:
                t_start = _parse_stat_timestamp(stat_item.get("start"))
                t_end = _parse_stat_timestamp(stat_item.get("end"))
                if lts_start_boundary is not None and t_end <= lts_start_boundary:
                    continue
                if t_start >= lts_end_boundary:
                    break

                energy_delta = 0.0
                if device_cfg.source_type == SOURCE_TYPE_POWER:
                    mean_val = stat_item.get("mean")
                    if mean_val is not None:
                        power_kw = normalize_power_to_kw(float(mean_val), unit)
                        duration_hours = max(0.0, (t_end - t_start).total_seconds() / 3600.0)
                        energy_delta = max(0.0, power_kw * duration_hours)
                else:
                    change_val = stat_item.get("change")
                    if change_val is not None and change_val >= 0:
                        energy_delta = normalize_energy_to_kwh(float(change_val), unit)
                    elif stat_item.get("sum") is not None:
                        curr_sum = normalize_energy_to_kwh(float(stat_item["sum"]), unit)
                        if prev_sum is not None and curr_sum >= prev_sum:
                            energy_delta = round(curr_sum - prev_sum, 6)
                        prev_sum = curr_sum
                    elif stat_item.get("state") is not None:
                        curr_st = float(stat_item["state"])
                        if prev_state_val is not None:
                            energy_delta = calculate_energy_delta(
                                device_cfg.source_type, prev_state_val, curr_st, unit
                            )
                        prev_state_val = curr_st

                if energy_delta > 0.0:
                    self._process_step_consumption(
                        step_start_utc=t_start,
                        step_end_utc=t_end,
                        energy_kwh=energy_delta,
                        price_intervals=price_intervals,
                        fallback_price=fallback_price,
                        profile_weights=profile_weights,
                        stats=stats,
                        hourly_cost_deltas=hourly_cost_deltas,
                        hourly_cost_deltas_offpeak=hourly_cost_deltas_offpeak,
                        hourly_cost_deltas_peak=hourly_cost_deltas_peak,
                        hourly_energy_deltas_offpeak=hourly_energy_deltas_offpeak,
                        hourly_energy_deltas_peak=hourly_energy_deltas_peak,
                    )
                    total_records_processed += 1

        # Phase 2: Replay high-precision Raw States
        num_chunks = 0
        active_sensor_keys: list[str] = [
            SENSOR_COST_DAY,
            SENSOR_COST_MONTH,
            SENSOR_COST_YEAR,
            SENSOR_COST_TOTAL,
            SENSOR_COST_MAX_DAY,
            SENSOR_COST_MAX_MONTH,
            SENSOR_COST_MAX_YEAR,
            SENSOR_COST_AVG_DAY,
            SENSOR_COST_AVG_MONTH,
            SENSOR_COST_AVG_YEAR,
        ]
        if pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            active_sensor_keys.extend([
                SENSOR_COST_DAY_OFFPEAK,
                SENSOR_COST_DAY_PEAK,
                SENSOR_COST_MONTH_OFFPEAK,
                SENSOR_COST_MONTH_PEAK,
                SENSOR_COST_YEAR_OFFPEAK,
                SENSOR_COST_YEAR_PEAK,
                SENSOR_COST_TOTAL_OFFPEAK,
                SENSOR_COST_TOTAL_PEAK,
                SENSOR_ENERGY_DAY_OFFPEAK,
                SENSOR_ENERGY_DAY_PEAK,
                SENSOR_ENERGY_MONTH_OFFPEAK,
                SENSOR_ENERGY_MONTH_PEAK,
                SENSOR_ENERGY_YEAR_OFFPEAK,
                SENSOR_ENERGY_YEAR_PEAK,
                SENSOR_ENERGY_TOTAL_OFFPEAK,
                SENSOR_ENERGY_TOTAL_PEAK,
            ])
        raw_states_tracker = _RawStatesTracker(active_sensor_keys)
        first_raw_state_recorded = False

        if raw_start_boundary is not None:
            raw_end = raw_end_boundary or end_date
            chunk_delta = timedelta(days=CHUNK_DURATION_DAYS)
            chunks: list[tuple[datetime, datetime]] = []
            c_start = raw_start_boundary
            while c_start < raw_end:
                c_end = min(c_start + chunk_delta, raw_end)
                chunks.append((c_start, c_end))
                c_start = c_end

            num_chunks = max(1, len(chunks))

            source_title_desc = {
                DATA_SOURCE_RECORDER: "Recorder uniquement (haute précision)",
                DATA_SOURCE_STATISTICS: "Statistiques à long terme (LTS)",
                DATA_SOURCE_MIXED: "Mixte (Recorder haute précision + LTS)",
            }.get(norm_source, norm_source)

            sources_plan_lines = [f"- **Mode demandé** : {source_title_desc}"]
            if lts_period_used and raw_period_used:
                sources_plan_lines.append(
                    f"- **Plan d'exécution** :\n"
                    f"  - 📊 LTS : du {dt_util.as_local(lts_period_used[0]).strftime(fmt)} au {dt_util.as_local(lts_period_used[1]).strftime(fmt)}\n"
                    f"  - ⚡ Recorder : du {dt_util.as_local(raw_period_used[0]).strftime(fmt)} au {dt_util.as_local(raw_period_used[1]).strftime(fmt)}"
                )
            elif raw_period_used:
                sources_plan_lines.append(
                    f"- **Période brute (Recorder)** : du {dt_util.as_local(raw_period_used[0]).strftime(fmt)} au {dt_util.as_local(raw_period_used[1]).strftime(fmt)}"
                )
            elif lts_period_used:
                sources_plan_lines.append(
                    f"- **Période LTS** : du {dt_util.as_local(lts_period_used[0]).strftime(fmt)} au {dt_util.as_local(lts_period_used[1]).strftime(fmt)}"
                )

            sources_plan_lines.append(f"- **Découpage** : {num_chunks} tranche{'s' if num_chunks > 1 else ''} de {CHUNK_DURATION_DAYS} jours")

            _send_progress_notification(
                self.hass,
                device_id=device_id,
                title=f"PowerCost - Démarrage du calcul ({device_cfg.name})",
                message=(
                    f"⏳ **Reconstruction démarrée pour {device_cfg.name}**\n\n"
                    + "\n".join(sources_plan_lines)
                    + "\n- Traitement haute précision en cours..."
                ),
            )

            for idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
                chunk_history = await self._async_run_recorder_job(
                    self._fetch_history,
                    chunk_start,
                    chunk_end,
                    entity_ids_to_query,
                )
                source_states = chunk_history.get(device_cfg.source_entity, [])
                if source_states:
                    sorted_source_states = sorted(source_states, key=lambda s: s.last_updated)
                    for state in sorted_source_states:
                        val = parse_float_state(state)
                        if val is None:
                            continue

                        current_ts = state.last_updated.timestamp()
                        if not first_raw_state_recorded:
                            raw_states_tracker.record_initial_state(stats, current_ts)
                            first_raw_state_recorded = True

                        if unit is None:
                            unit = state.attributes.get("unit_of_measurement")

                        if last_val is not None and last_ts is not None:
                            step_start_utc = datetime.fromtimestamp(last_ts, tz=timezone.utc)
                            step_end_utc = datetime.fromtimestamp(current_ts, tz=timezone.utc)

                            energy_delta = 0.0
                            if device_cfg.source_type == SOURCE_TYPE_POWER:
                                elapsed = current_ts - last_ts
                                if elapsed > 3600.0:
                                    # Power sensor gap > 1 hour: estimate using profile
                                    slices = self._slice_step_by_hours(step_start_utc, step_end_utc)
                                    energy_delta = 0.0
                                    for s_a, s_b in slices:
                                        dur_h = (s_b - s_a).total_seconds() / 3600.0
                                        h_loc = dt_util.as_local(s_a).hour
                                        p_kw = normalize_power_to_kw(
                                            profile_weights.get(h_loc, (last_val + val) / 2.0), unit
                                        )
                                        energy_delta += p_kw * dur_h
                                else:
                                    energy_delta = calculate_power_consumption(
                                        last_val, val, last_ts, current_ts, unit
                                    )
                            else:
                                energy_delta = calculate_energy_delta(
                                    device_cfg.source_type, last_val, val, unit
                                )

                            if energy_delta > 0.0:
                                self._process_step_consumption(
                                    step_start_utc=step_start_utc,
                                    step_end_utc=step_end_utc,
                                    energy_kwh=energy_delta,
                                    price_intervals=price_intervals,
                                    fallback_price=fallback_price,
                                    profile_weights=profile_weights,
                                    stats=stats,
                                    hourly_cost_deltas=hourly_cost_deltas,
                                    hourly_cost_deltas_offpeak=hourly_cost_deltas_offpeak,
                                    hourly_cost_deltas_peak=hourly_cost_deltas_peak,
                                    hourly_energy_deltas_offpeak=hourly_energy_deltas_offpeak,
                                    hourly_energy_deltas_peak=hourly_energy_deltas_peak,
                                    raw_states_tracker=raw_states_tracker,
                                )

                        last_val = val
                        last_ts = current_ts
                        total_records_processed += 1

                # Send progress notification after this chunk
                pct = int((idx / num_chunks) * 100)
                _send_progress_notification(
                    self.hass,
                    device_id=device_id,
                    title=f"PowerCost - Progression ({idx}/{num_chunks})",
                    message=(
                        f"⏳ **Reconstruction en cours pour {device_cfg.name}**\n\n"
                        f"- **Progression** : Tranche {idx}/{num_chunks} ({pct}%)\n"
                        f"- **Tranche traitée** : du {dt_util.as_local(chunk_start).strftime('%d/%m/%Y')} au {dt_util.as_local(chunk_end).strftime('%d/%m/%Y')}\n"
                        f"- **Enregistrements cumulés** : {total_records_processed}\n"
                        f"- **Consommation calculée** : {stats.energy_total:.2f} kWh\n"
                        f"- **Coût cumulé actuel** : {stats.cost_total:.2f} €"
                    ),
                )

        # 5. Check if any data was processed
        if total_records_processed == 0:
            source_label = "le Recorder" if norm_source == DATA_SOURCE_RECORDER else ("les statistiques LTS" if norm_source == DATA_SOURCE_STATISTICS else "le Recorder ou les statistiques LTS")
            _LOGGER.warning(
                "No history data found for entity %s between %s and %s (source: %s)",
                device_cfg.source_entity,
                start_date.isoformat(),
                end_date.isoformat(),
                norm_source,
            )
            _send_progress_notification(
                self.hass,
                device_id=device_id,
                title="PowerCost - Aucune donnée trouvée",
                message=(
                    f"⚠️ **Aucune donnée trouvée pour {device_cfg.name}**\n\n"
                    f"- **Période** : du {start_fmt} au {end_fmt}\n"
                    f"- Aucune mesure n'a été trouvée dans {source_label} pour `{device_cfg.source_entity}`."
                ),
            )
            return {
                "success": False,
                "message": (
                    f"Aucune donnée d'historique trouvée dans {source_label} pour "
                    f"'{device_cfg.source_entity}' entre {start_fmt} et {end_fmt}."
                ),
                "records_processed": 0,
            }

        # 6. Finalize statistics and sync baseline with current live state
        curr_live_state = self.hass.states.get(device_cfg.source_entity)
        if curr_live_state:
            live_val = parse_float_state(curr_live_state)
            if live_val is not None:
                last_val = live_val
                last_ts = curr_live_state.last_updated.timestamp()
                unit = device_cfg.source_unit or curr_live_state.attributes.get("unit_of_measurement")

        stats.last_source_value = last_val
        stats.last_source_timestamp = last_ts
        stats.last_source_unit = unit
        stats.last_rebuild_timestamp = dt_util.utcnow().timestamp()

        # 6.5 Inject high-precision states into Recorder database for the recorder period
        imported_recorder_states_count = 0
        if raw_start_boundary is not None and first_raw_state_recorded:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(self.hass)
            unique_prefix = f"{self.coordinator.entry_id}_{device_cfg.device_id}_"
            states_by_entity_id: dict[str, list[tuple[float, float]]] = {}
            sensor_metadata: dict[str, dict[str, Any]] = {}

            for s_key in active_sensor_keys:
                ent_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{unique_prefix}{s_key}")
                if not ent_id:
                    candidate = f"sensor.{device_cfg.device_id}_{s_key}"
                    if candidate in self.hass.states.async_entity_ids("sensor"):
                        ent_id = candidate

                pts = raw_states_tracker.raw_states.get(s_key, [])
                if ent_id and pts:
                    states_by_entity_id[ent_id] = pts
                    meta_def = SENSOR_METADATA_DEFAULTS.get(s_key, {})
                    ent_entry = ent_reg.async_get(ent_id) if hasattr(ent_reg, "async_get") else None
                    friendly_name = getattr(ent_entry, "name", None) or getattr(ent_entry, "original_name", None)
                    sensor_metadata[ent_id] = {
                        "unit_of_measurement": meta_def.get("unit_of_measurement"),
                        "device_class": meta_def.get("device_class"),
                        "state_class": meta_def.get("state_class"),
                        "friendly_name": friendly_name,
                    }

            if states_by_entity_id:
                raw_start_ts = raw_start_boundary.timestamp()
                raw_end_ts = (raw_end_boundary or end_date).timestamp()
                imported_recorder_states_count = await self._async_run_recorder_job(
                    self._insert_recorder_states_sync,
                    states_by_entity_id,
                    sensor_metadata,
                    raw_start_ts,
                    raw_end_ts,
                )

        # 7. Inject retroactive statistics curves into Home Assistant Long-Term Statistics
        imported_stats_count = await self._async_inject_historical_statistics(
            device_cfg=device_cfg,
            start_date=start_date,
            end_date=end_date,
            hourly_cost_deltas=hourly_cost_deltas,
            hourly_cost_deltas_offpeak=hourly_cost_deltas_offpeak,
            hourly_cost_deltas_peak=hourly_cost_deltas_peak,
            hourly_energy_deltas_offpeak=hourly_energy_deltas_offpeak,
            hourly_energy_deltas_peak=hourly_energy_deltas_peak,
        )

        # 8. Update coordinator and notify UI listeners once everything is ready
        self.coordinator.statistics[device_id] = stats
        await self.coordinator.async_save_data()
        self.coordinator.async_update_listeners()

        _LOGGER.info(
            "Rebuild complete for %s. Total cost: %.2f EUR, Total energy: %.2f kWh across %d records. "
            "Imported %d recorder states, %d LTS points.",
            device_cfg.name,
            stats.cost_total,
            stats.energy_total,
            total_records_processed,
            imported_recorder_states_count,
            imported_stats_count,
        )

        # 9. Send final completion notification
        recorder_states_info = (
            f"\n- ⚡ **États haute précision enregistrés dans le Recorder** : {imported_recorder_states_count} états"
            if imported_recorder_states_count > 0
            else ""
        )
        lts_info = (
            f"\n- 📊 **Points de courbes injectés (LTS)** : {imported_stats_count} points horaires (graphiques jour, mois, total et HC/HP à jour !)"
            if imported_stats_count > 0
            else ""
        )
        tariff_breakdown = ""
        if pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            tariff_breakdown = (
                f"\n- **Coût Heures Creuses** : {stats.cost_total_offpeak:.2f} € ({stats.energy_total_offpeak:.2f} kWh)"
                f"\n- **Coût Heures Pleines** : {stats.cost_total_peak:.2f} € ({stats.energy_total_peak:.2f} kWh)"
            )

        sources_used_lines = []
        if lts_period_used and raw_period_used:
            sources_used_lines.append(
                f"\n- **Sources utilisées par période** :"
                f"\n  - 📊 **Statistiques à long terme (LTS)** : du {dt_util.as_local(lts_period_used[0]).strftime(fmt)} au {dt_util.as_local(lts_period_used[1]).strftime(fmt)}"
                f"\n  - ⚡ **Recorder (données précises)** : du {dt_util.as_local(raw_period_used[0]).strftime(fmt)} au {dt_util.as_local(raw_period_used[1]).strftime(fmt)}"
            )
        elif raw_period_used:
            sources_used_lines.append(
                f"\n- **Source de données utilisée** : ⚡ **Recorder uniquement (données précises)** du {dt_util.as_local(raw_period_used[0]).strftime(fmt)} au {dt_util.as_local(raw_period_used[1]).strftime(fmt)}"
            )
        elif lts_period_used:
            sources_used_lines.append(
                f"\n- **Source de données utilisée** : 📊 **Statistiques à long terme uniquement (LTS)** du {dt_util.as_local(lts_period_used[0]).strftime(fmt)} au {dt_util.as_local(lts_period_used[1]).strftime(fmt)}"
            )
        sources_breakdown = "".join(sources_used_lines)

        chunk_summary = f"- **Tranches traitées** : {num_chunks}/{num_chunks} (100%)\n" if raw_start_boundary is not None else ""

        _send_progress_notification(
            self.hass,
            device_id=device_id,
            title="PowerCost - Historique reconstruit avec succès",
            message=(
                f"✅ **Reconstruction terminée pour {device_cfg.name}**\n\n"
                f"- **Période totale** : du {start_fmt} au {end_fmt}"
                f"{sources_breakdown}\n"
                f"{chunk_summary}"
                f"- **Total enregistrements traités** : {total_records_processed}\n"
                f"- **Consommation totale** : {stats.energy_total:.2f} kWh\n"
                f"- **Coût total calculé** : {stats.cost_total:.2f} €"
                f"{tariff_breakdown}"
                f"{recorder_states_info}"
                f"{lts_info}"
            ),
        )

        return {
            "success": True,
            "device_id": device_id,
            "cost_total": stats.cost_total,
            "energy_total": stats.energy_total,
            "records_processed": total_records_processed,
            "recorder_states_imported": imported_recorder_states_count,
            "statistics_imported": imported_stats_count,
            "data_source_requested": norm_source,
            "periods_used": {
                "lts": (
                    {
                        "start": lts_period_used[0].isoformat(),
                        "end": lts_period_used[1].isoformat(),
                    }
                    if lts_period_used
                    else None
                ),
                "recorder": (
                    {
                        "start": raw_period_used[0].isoformat(),
                        "end": raw_period_used[1].isoformat(),
                    }
                    if raw_period_used
                    else None
                ),
            },
        }

    def _build_cumulative_curve_points(
        self,
        start_hour: datetime,
        end_hour: datetime,
        hourly_deltas: dict[datetime, float],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Build day, month, year, and total points from an hourly delta map."""
        points_day: list[dict[str, Any]] = []
        points_month: list[dict[str, Any]] = []
        points_year: list[dict[str, Any]] = []
        points_total: list[dict[str, Any]] = []

        running_today = 0.0
        running_month = 0.0
        running_year = 0.0
        running_total = 0.0

        current_day_str = ""
        current_month_str = ""
        current_year_str = ""

        curr_h = start_hour
        while curr_h <= end_hour:
            local_dt = dt_util.as_local(curr_h)
            day_str = local_dt.strftime("%Y-%m-%d")
            month_str = local_dt.strftime("%Y-%m")
            year_str = local_dt.strftime("%Y")

            if not current_day_str:
                current_day_str = day_str
                current_month_str = month_str
                current_year_str = year_str
            else:
                # Midnight rollover: cost_today resets to 0.0
                if day_str != current_day_str:
                    running_today = 0.0
                    current_day_str = day_str
                # Month rollover: cost_month resets to 0.0
                if month_str != current_month_str:
                    running_month = 0.0
                    current_month_str = month_str
                # Year rollover: cost_year resets to 0.0
                if year_str != current_year_str:
                    running_year = 0.0
                    current_year_str = year_str

            cost_delta = hourly_deltas.get(curr_h, 0.0)
            running_today += cost_delta
            running_month += cost_delta
            running_year += cost_delta
            running_total += cost_delta

            pt_common = {"start": curr_h, "sum": round(running_total, 4)}
            points_day.append({**pt_common, "state": round(running_today, 4)})
            points_month.append({**pt_common, "state": round(running_month, 4)})
            points_year.append({**pt_common, "state": round(running_year, 4)})
            points_total.append({**pt_common, "state": round(running_total, 4)})

            curr_h += timedelta(hours=1)

        return points_day, points_month, points_year, points_total

    async def _async_inject_historical_statistics(
        self,
        device_cfg: DeviceConfig,
        start_date: datetime,
        end_date: datetime,
        hourly_cost_deltas: dict[datetime, float],
        hourly_cost_deltas_offpeak: dict[datetime, float] | None = None,
        hourly_cost_deltas_peak: dict[datetime, float] | None = None,
        hourly_energy_deltas_offpeak: dict[datetime, float] | None = None,
        hourly_energy_deltas_peak: dict[datetime, float] | None = None,
    ) -> int:
        """Build and import historical statistics curves for Day, Month, Year, and Total sensors."""
        start_hour = start_date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end_hour = end_date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

        # Do not import statistics for current ongoing hour or future hours to avoid collisions
        # with Home Assistant recorder's live periodic compilation.
        current_hour_utc = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        if end_hour >= current_hour_utc:
            end_hour = current_hour_utc - timedelta(hours=1)

        if start_hour > end_hour:
            _LOGGER.debug(
                "Skipping statistics injection for %s: start_hour %s is after end_hour %s",
                device_cfg.name,
                start_hour.isoformat(),
                end_hour.isoformat(),
            )
            return 0

        points_day, points_month, points_year, points_total = self._build_cumulative_curve_points(
            start_hour, end_hour, hourly_cost_deltas
        )

        total_imported_points = 0

        try:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(self.hass)
            unique_prefix = f"{self.coordinator.entry_id}_{device_cfg.device_id}_"

            mappings: list[tuple[str, str, list[dict[str, Any]], str, str | None]] = [
                (SENSOR_COST_DAY, f"{device_cfg.name} Coût aujourd'hui", points_day, "€", None),
                (SENSOR_COST_MONTH, f"{device_cfg.name} Coût ce mois", points_month, "€", None),
                (SENSOR_COST_YEAR, f"{device_cfg.name} Coût cette année", points_year, "€", None),
                (SENSOR_COST_TOTAL, f"{device_cfg.name} Coût total", points_total, "€", None),
            ]

            if (
                self.coordinator.pricing_config.mode == PRICING_MODE_PEAK_OFFPEAK
                and hourly_cost_deltas_offpeak is not None
                and hourly_cost_deltas_peak is not None
            ):
                hc_d, hc_m, hc_y, hc_t = self._build_cumulative_curve_points(
                    start_hour, end_hour, hourly_cost_deltas_offpeak
                )
                hp_d, hp_m, hp_y, hp_t = self._build_cumulative_curve_points(
                    start_hour, end_hour, hourly_cost_deltas_peak
                )
                mappings.extend(
                    [
                        (SENSOR_COST_DAY_OFFPEAK, f"{device_cfg.name} Coût aujourd'hui (Heures Creuses)", hc_d, "€", None),
                        (SENSOR_COST_MONTH_OFFPEAK, f"{device_cfg.name} Coût ce mois (Heures Creuses)", hc_m, "€", None),
                        (SENSOR_COST_YEAR_OFFPEAK, f"{device_cfg.name} Coût cette année (Heures Creuses)", hc_y, "€", None),
                        (SENSOR_COST_TOTAL_OFFPEAK, f"{device_cfg.name} Coût total (Heures Creuses)", hc_t, "€", None),
                        (SENSOR_COST_DAY_PEAK, f"{device_cfg.name} Coût aujourd'hui (Heures Pleines)", hp_d, "€", None),
                        (SENSOR_COST_MONTH_PEAK, f"{device_cfg.name} Coût ce mois (Heures Pleines)", hp_m, "€", None),
                        (SENSOR_COST_YEAR_PEAK, f"{device_cfg.name} Coût cette année (Heures Pleines)", hp_y, "€", None),
                        (SENSOR_COST_TOTAL_PEAK, f"{device_cfg.name} Coût total (Heures Pleines)", hp_t, "€", None),
                    ]
                )

            if (
                self.coordinator.pricing_config.mode == PRICING_MODE_PEAK_OFFPEAK
                and hourly_energy_deltas_offpeak is not None
                and hourly_energy_deltas_peak is not None
            ):
                hc_e_d, hc_e_m, hc_e_y, hc_e_t = self._build_cumulative_curve_points(
                    start_hour, end_hour, hourly_energy_deltas_offpeak
                )
                hp_e_d, hp_e_m, hp_e_y, hp_e_t = self._build_cumulative_curve_points(
                    start_hour, end_hour, hourly_energy_deltas_peak
                )
                mappings.extend(
                    [
                        (SENSOR_ENERGY_DAY_OFFPEAK, f"{device_cfg.name} Énergie aujourd'hui (Heures Creuses)", hc_e_d, "kWh", "energy"),
                        (SENSOR_ENERGY_MONTH_OFFPEAK, f"{device_cfg.name} Énergie ce mois (Heures Creuses)", hc_e_m, "kWh", "energy"),
                        (SENSOR_ENERGY_YEAR_OFFPEAK, f"{device_cfg.name} Énergie cette année (Heures Creuses)", hc_e_y, "kWh", "energy"),
                        (SENSOR_ENERGY_TOTAL_OFFPEAK, f"{device_cfg.name} Énergie totale (Heures Creuses)", hc_e_t, "kWh", "energy"),
                        (SENSOR_ENERGY_DAY_PEAK, f"{device_cfg.name} Énergie aujourd'hui (Heures Pleines)", hp_e_d, "kWh", "energy"),
                        (SENSOR_ENERGY_MONTH_PEAK, f"{device_cfg.name} Énergie ce mois (Heures Pleines)", hp_e_m, "kWh", "energy"),
                        (SENSOR_ENERGY_YEAR_PEAK, f"{device_cfg.name} Énergie cette année (Heures Pleines)", hp_e_y, "kWh", "energy"),
                        (SENSOR_ENERGY_TOTAL_PEAK, f"{device_cfg.name} Énergie totale (Heures Pleines)", hp_e_t, "kWh", "energy"),
                    ]
                )

            def _resolve_entity_id(sensor_key: str) -> str | None:
                full_uid = f"{unique_prefix}{sensor_key}"
                eid = ent_reg.async_get_entity_id("sensor", DOMAIN, full_uid)
                if eid:
                    return eid
                if hasattr(ent_reg, "entities"):
                    for ent in ent_reg.entities.values():
                        if getattr(ent, "platform", None) == DOMAIN:
                            uid = getattr(ent, "unique_id", "")
                            if uid == full_uid or (uid.endswith(f"_{sensor_key}") and device_cfg.device_id in uid):
                                return ent.entity_id
                for candidate in (
                    f"sensor.{device_cfg.device_id}_{sensor_key}",
                    f"sensor.{device_cfg.name.lower().replace(' ', '_')}_{sensor_key}",
                ):
                    if candidate in self.hass.states.async_entity_ids("sensor"):
                        return candidate
                return None

            target_items: list[tuple[str, str, list[dict[str, Any]], str, str | None]] = []
            for sensor_key, friendly_name, pts, unit, unit_class in mappings:
                entity_id = _resolve_entity_id(sensor_key)
                if entity_id and pts:
                    target_items.append((entity_id, friendly_name, pts, unit, unit_class))
                else:
                    _LOGGER.debug(
                        "Could not resolve entity_id for %s (sensor_key=%s, points=%d)",
                        friendly_name,
                        sensor_key,
                        len(pts) if pts else 0,
                    )

            if not target_items:
                return 0

            # 1. Directly inject hourly points into Statistics table and seed StatisticsShortTerm
            # to guarantee continuous curves and avoid negative variations in monthly bar charts.
            injected_stats = await self._async_run_recorder_job(
                self._inject_statistics_to_recorder_sync,
                target_items,
            )
            total_imported_points += injected_stats

            # 2. Also register via Home Assistant's standard API with valid metadata
            for entity_id, friendly_name, pts, unit, unit_class in target_items:
                await self._async_import_sensor_stats(entity_id, friendly_name, pts, unit=unit, unit_class=unit_class)

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Could not inject statistics for %s: %s", device_cfg.name, err)

        return total_imported_points

    async def _async_import_sensor_stats(
        self,
        entity_id: str,
        name: str,
        points: list[dict[str, Any]],
        unit: str = "€",
        unit_class: str | None = None,
    ) -> None:
        """Import statistics data points into Home Assistant recorder."""
        if not points or not entity_id:
            return

        try:
            from homeassistant.components.recorder.statistics import async_import_statistics

            try:
                from homeassistant.components.recorder.models import StatisticMeanType
                mean_type = StatisticMeanType.NONE
            except Exception:
                mean_type = 0

            metadata: dict[str, Any] = {
                "has_mean": False,
                "has_sum": True,
                "mean_type": mean_type,
                "name": name,
                "source": "recorder",
                "statistic_id": entity_id,
                "unit_of_measurement": unit,
                "unit_class": unit_class,
            }

            res = async_import_statistics(self.hass, metadata, points)
            if asyncio.iscoroutine(res):
                await res

            _LOGGER.info(
                "Successfully imported %d statistic points into Home Assistant for %s",
                len(points),
                entity_id,
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Could not import statistics for %s: %s", entity_id, err)

    def _fetch_history(
        self,
        start_date: datetime,
        end_date: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[State]]:
        """Run blocking recorder query inside executor for a given time slice."""
        result: dict[str, list[State]] = {}
        for ent_id in entity_ids:
            if not ent_id:
                continue
            try:
                changes = history.state_changes_during_period(
                    self.hass,
                    start_date,
                    end_date,
                    entity_id=ent_id,
                    include_start_time_state=True,
                )
                states = changes.get(ent_id, [])
                result[ent_id] = sorted(states, key=lambda s: s.last_updated)
            except Exception as err:
                _LOGGER.warning(
                    "Error querying recorder history for %s (%s to %s): %s",
                    ent_id,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    err,
                )
                result[ent_id] = []
        return result

    def _fetch_statistics(
        self,
        start_date: datetime,
        end_date: datetime,
        statistic_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch long-term statistics (hourly) from recorder."""
        try:
            from homeassistant.components.recorder.statistics import statistics_during_period

            stats = statistics_during_period(
                self.hass,
                start_time=start_date,
                end_time=end_date,
                statistic_ids={statistic_id},
                period="hour",
                types={"state", "sum", "mean", "change"},
            )
            return stats.get(statistic_id, [])
        except Exception as err:
            _LOGGER.debug(
                "Could not fetch long-term statistics for %s: %s",
                statistic_id,
                err,
            )
            return []

    def _insert_recorder_states_sync(
        self,
        states_by_entity_id: dict[str, list[tuple[float, float]]],
        sensor_metadata: dict[str, dict[str, Any]],
        start_ts: float,
        end_ts: float,
    ) -> int:
        """Insert high-precision historical states directly into the recorder database."""
        if not states_by_entity_id:
            return 0

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import (
                StateAttributes,
                States,
                StatesMeta,
            )
            from homeassistant.components.recorder.util import session_scope
            from homeassistant.helpers.json import json_bytes
            from sqlalchemy import delete, select

            try:
                rec = get_instance(self.hass)
            except Exception:
                rec = None

            if not rec:
                _LOGGER.debug("Recorder instance not available; skipping raw state insertion")
                return 0

            total_saved = 0
            with session_scope(hass=self.hass) as session:
                all_db_states: list[States] = []

                for entity_id, points in states_by_entity_id.items():
                    if not points:
                        continue

                    # 1. Resolve or create metadata_id
                    meta_id: int | None = None
                    if hasattr(rec, "states_meta_manager") and getattr(rec.states_meta_manager, "active", False):
                        res_m = rec.states_meta_manager.get(entity_id, session, True)
                        if isinstance(res_m, int):
                            meta_id = res_m
                    if meta_id is None:
                        meta_row = session.execute(
                            select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                        ).scalar_one_or_none()
                        if not meta_row:
                            meta_row = StatesMeta(entity_id=entity_id)
                            session.add(meta_row)
                            session.flush()
                        meta_id = meta_row.metadata_id
                        if hasattr(rec, "states_meta_manager") and hasattr(rec.states_meta_manager, "_id_map"):
                            rec.states_meta_manager._id_map[entity_id] = meta_id

                    # 2. Resolve or create attributes_id
                    meta_info = sensor_metadata.get(entity_id, {})
                    attrs_dict: dict[str, Any] = {}
                    if meta_info.get("unit_of_measurement"):
                        attrs_dict["unit_of_measurement"] = meta_info["unit_of_measurement"]
                    if meta_info.get("device_class"):
                        attrs_dict["device_class"] = meta_info["device_class"]
                    if meta_info.get("state_class"):
                        attrs_dict["state_class"] = meta_info["state_class"]
                    if meta_info.get("friendly_name"):
                        attrs_dict["friendly_name"] = meta_info["friendly_name"]

                    attrs_bytes = json_bytes(attrs_dict)
                    shared_attrs_str = attrs_bytes.decode("utf-8")
                    h = StateAttributes.hash_shared_attrs_bytes(attrs_bytes)

                    attr_id: int | None = None
                    if hasattr(rec, "state_attributes_manager"):
                        res_a = rec.state_attributes_manager.get(shared_attrs_str, h, session)
                        if isinstance(res_a, int):
                            attr_id = res_a
                    if attr_id is None:
                        attr_row = session.execute(
                            select(StateAttributes).where(
                                StateAttributes.hash == h,
                                StateAttributes.shared_attrs == shared_attrs_str,
                            )
                        ).scalar_one_or_none()
                        if not attr_row:
                            attr_row = StateAttributes(hash=h, shared_attrs=shared_attrs_str)
                            session.add(attr_row)
                            session.flush()
                        attr_id = attr_row.attributes_id
                        if hasattr(rec, "state_attributes_manager") and hasattr(rec.state_attributes_manager, "_id_map"):
                            rec.state_attributes_manager._id_map[shared_attrs_str] = attr_id

                    # 3. Clean up existing states in the target time window
                    session.execute(
                        delete(States).where(
                            States.metadata_id == meta_id,
                            States.last_updated_ts >= start_ts,
                            States.last_updated_ts <= end_ts,
                        )
                    )

                    # 4. Build States objects
                    for ts, val in points:
                        dt_obj = datetime.fromtimestamp(ts, tz=timezone.utc)
                        st_str = f"{val:.2f}"
                        all_db_states.append(
                            States(
                                metadata_id=meta_id,
                                state=st_str,
                                attributes_id=attr_id,
                                last_updated=dt_obj,
                                last_changed=dt_obj,
                                last_updated_ts=ts,
                                last_changed_ts=ts,
                                last_reported_ts=ts,
                            )
                        )

                # 5. Bulk insert in chunks
                chunk_size = 5000
                for i in range(0, len(all_db_states), chunk_size):
                    session.bulk_save_objects(all_db_states[i : i + chunk_size])
                    session.flush()

                total_saved = len(all_db_states)

            _LOGGER.info(
                "Successfully injected %d high-precision states into recorder database",
                total_saved,
            )
            return total_saved

        except Exception as err:
            _LOGGER.warning("Could not inject raw recorder states: %s", err)
            return 0

    def _inject_statistics_to_recorder_sync(
        self,
        target_items: list[tuple[str, str, list[dict[str, Any]], str, str | None]],
    ) -> int:
        """Directly insert long-term hourly statistics and seed short-term statistics into the recorder database."""
        if not target_items:
            return 0

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import (
                Statistics,
                StatisticsMeta,
                StatisticsShortTerm,
            )
            from homeassistant.components.recorder.util import session_scope
            from sqlalchemy import delete, select

            try:
                rec = get_instance(self.hass)
            except Exception:
                rec = None

            if not rec:
                _LOGGER.debug("Recorder instance not available; skipping direct statistics injection")
                return 0

            stat_col_names = {col.name for col in Statistics.__table__.columns}
            short_col_names = {col.name for col in StatisticsShortTerm.__table__.columns}
            meta_col_names = {col.name for col in StatisticsMeta.__table__.columns}
            total_points_saved = 0
            now_dt = dt_util.utcnow()
            now_ts = now_dt.timestamp()

            with session_scope(hass=self.hass) as session:
                for entity_id, friendly_name, pts, unit, unit_class in target_items:
                    if not pts or not entity_id:
                        continue

                    # 1. Resolve or create metadata_id in StatisticsMeta
                    meta_id: int | None = None
                    if hasattr(rec, "statistics_meta_manager"):
                        mgr = rec.statistics_meta_manager
                        if hasattr(mgr, "get"):
                            try:
                                res_m = mgr.get(session, entity_id)
                                if isinstance(res_m, int):
                                    meta_id = res_m
                                elif isinstance(res_m, (tuple, list)) and len(res_m) > 0 and isinstance(res_m[0], int):
                                    meta_id = res_m[0]
                            except Exception:
                                meta_id = None
                        if meta_id is None and hasattr(mgr, "get_metadata_id"):
                            try:
                                res_m = mgr.get_metadata_id(entity_id)
                                if isinstance(res_m, int):
                                    meta_id = res_m
                            except Exception:
                                meta_id = None

                    if meta_id is None:
                        meta_row = session.execute(
                            select(StatisticsMeta).where(StatisticsMeta.statistic_id == entity_id)
                        ).scalar_one_or_none()
                        if not meta_row:
                            meta_kwargs: dict[str, Any] = {
                                "statistic_id": entity_id,
                                "source": "recorder",
                                "unit_of_measurement": unit,
                                "has_mean": False,
                                "has_sum": True,
                                "name": friendly_name,
                            }
                            if "unit_class" in meta_col_names:
                                meta_kwargs["unit_class"] = unit_class
                            if "mean_type" in meta_col_names:
                                meta_kwargs["mean_type"] = 0
                            meta_row = StatisticsMeta(**meta_kwargs)
                            session.add(meta_row)
                            session.flush()
                        else:
                            changed = False
                            if "unit_class" in meta_col_names and getattr(meta_row, "unit_class", None) != unit_class:
                                setattr(meta_row, "unit_class", unit_class)
                                changed = True
                            if "mean_type" in meta_col_names and getattr(meta_row, "mean_type", None) is None:
                                setattr(meta_row, "mean_type", 0)
                                changed = True
                            if "has_sum" in meta_col_names and not getattr(meta_row, "has_sum", False):
                                setattr(meta_row, "has_sum", True)
                                changed = True
                            if changed:
                                session.flush()
                        meta_id = getattr(meta_row, "id", None) or getattr(meta_row, "metadata_id", None)

                    if meta_id is None:
                        continue

                    # Update recorder manager in-memory cache if available
                    if hasattr(rec, "statistics_meta_manager"):
                        mgr = rec.statistics_meta_manager
                        if hasattr(mgr, "_id_map") and isinstance(mgr._id_map, dict):
                            mgr._id_map[entity_id] = meta_id
                        if hasattr(mgr, "_metadata_ids") and isinstance(mgr._metadata_ids, dict):
                            mgr._metadata_ids[entity_id] = meta_id

                    # 2. Determine time range of reconstructed points
                    first_pt_start = pts[0].get("start")
                    last_pt_start = pts[-1].get("start")
                    p_start_dt = first_pt_start if isinstance(first_pt_start, datetime) else _parse_stat_timestamp(first_pt_start)
                    p_end_dt = last_pt_start if isinstance(last_pt_start, datetime) else _parse_stat_timestamp(last_pt_start)
                    p_start_ts = p_start_dt.timestamp()
                    p_end_ts = p_end_dt.timestamp()

                    # 3. Clean up existing rows in Statistics (hourly) for this metadata_id in the reconstructed window
                    if "start_ts" in stat_col_names:
                        session.execute(
                            delete(Statistics).where(
                                Statistics.metadata_id == meta_id,
                                Statistics.start_ts >= p_start_ts,
                                Statistics.start_ts <= p_end_ts,
                            )
                        )
                    elif "start" in stat_col_names:
                        session.execute(
                            delete(Statistics).where(
                                Statistics.metadata_id == meta_id,
                                Statistics.start >= p_start_dt,
                                Statistics.start <= p_end_dt,
                            )
                        )

                    # 4. Bulk insert hourly points into Statistics
                    all_db_stats: list[Statistics] = []
                    for pt in pts:
                        pt_start = pt.get("start")
                        if not pt_start:
                            continue
                        s_dt = pt_start if isinstance(pt_start, datetime) else _parse_stat_timestamp(pt_start)
                        s_ts = s_dt.timestamp()
                        st_val = float(pt.get("state", 0.0))
                        sum_val = float(pt.get("sum", 0.0))

                        s_kwargs: dict[str, Any] = {
                            "metadata_id": meta_id,
                            "state": st_val,
                            "sum": sum_val,
                        }
                        if "start_ts" in stat_col_names:
                            s_kwargs["start_ts"] = s_ts
                        if "start" in stat_col_names:
                            s_kwargs["start"] = s_dt
                        if "created_ts" in stat_col_names:
                            s_kwargs["created_ts"] = now_ts
                        if "created" in stat_col_names:
                            s_kwargs["created"] = now_dt

                        all_db_stats.append(Statistics(**s_kwargs))

                    chunk_size = 5000
                    for i in range(0, len(all_db_stats), chunk_size):
                        session.bulk_save_objects(all_db_stats[i : i + chunk_size])
                        session.flush()

                    total_points_saved += len(all_db_stats)

                    # 5. Clean up existing records in StatisticsShortTerm and insert the latest point as seed
                    session.execute(
                        delete(StatisticsShortTerm).where(StatisticsShortTerm.metadata_id == meta_id)
                    )

                    last_pt = pts[-1]
                    last_st_val = float(last_pt.get("state", 0.0))
                    last_sum_val = float(last_pt.get("sum", 0.0))

                    short_kwargs: dict[str, Any] = {
                        "metadata_id": meta_id,
                        "state": last_st_val,
                        "sum": last_sum_val,
                    }
                    if "start_ts" in short_col_names:
                        short_kwargs["start_ts"] = p_end_ts
                    if "start" in short_col_names:
                        short_kwargs["start"] = p_end_dt
                    if "created_ts" in short_col_names:
                        short_kwargs["created_ts"] = now_ts
                    if "created" in short_col_names:
                        short_kwargs["created"] = now_dt

                    session.add(StatisticsShortTerm(**short_kwargs))

                    # 6. Update short term run cache if available
                    try:
                        from homeassistant.components.recorder.statistics import (
                            get_short_term_statistics_run_cache,
                            cache_latest_short_term_statistic_id_for_metadata_id,
                        )
                        run_cache = get_short_term_statistics_run_cache(self.hass)
                        cache_latest_short_term_statistic_id_for_metadata_id(run_cache, session, meta_id)
                    except Exception:
                        pass

                session.flush()

            _LOGGER.info(
                "Successfully injected %d hourly statistic points and short-term seeds into recorder database",
                total_points_saved,
            )
            return total_points_saved

        except Exception as err:
            _LOGGER.warning("Could not inject statistics directly into recorder database: %s", err)
            return 0


