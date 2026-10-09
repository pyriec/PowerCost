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
    SENSOR_TYPES,
    SOURCE_TYPE_POWER,
)
from .models import DeviceConfig, DeviceStatistics, PricingConfig
from .pricing import extract_timeline_intervals, parse_float_state

if TYPE_CHECKING:
    from .coordinator import ElectricityCostCoordinator

_LOGGER = logging.getLogger(__name__)

# Duration of each processing chunk (15 days)
CHUNK_DURATION_DAYS = 15


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

        def on_done(success: bool) -> None:
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, success)

        instance.queue_task(ClearStatisticsTask(on_done=on_done, statistic_ids=statistic_ids))
        return await asyncio.wait_for(future, timeout=30.0)
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
) -> None:
    """Completely wipe storage data, recorder states, and recorder statistics for a device."""
    entity_ids = get_device_entity_ids(hass, coordinator.entry_id, device_id)
    _LOGGER.info("Purging all data and history for device %s (entities: %s)", device_id, entity_ids)

    # 1. Clear Long-Term Statistics
    if entity_ids:
        await async_clear_recorder_statistics(hass, entity_ids)

    # 2. Purge states history
    if entity_ids:
        await async_purge_recorder_entity_states(hass, entity_ids)

    # 3. Reset in-memory and stored statistics
    coordinator.statistics[device_id] = DeviceStatistics(device_id=device_id)
    await coordinator.async_save_data()
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

    async def async_rebuild_device(
        self,
        device_id: str,
        start_date: datetime,
        end_date: datetime,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        """Rebuild history for a specific device between start_date and end_date."""
        device_cfg = self.coordinator.devices.get(device_id)
        if not device_cfg:
            raise ValueError(f"Appareil '{device_id}' non trouvé dans la configuration PowerCost")

        pricing_cfg = self.coordinator.pricing_config

        # 0. Purge existing history and statistics before rebuilding if requested
        if clear_existing:
            _LOGGER.info("Purging old statistics and history before rebuild for %s", device_cfg.name)
            await async_purge_device_data_and_history(self.hass, self.coordinator, device_id)

        # 1. Build list of entities to query
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

        # 2. Divide requested range into manageable chunks
        chunk_delta = timedelta(days=CHUNK_DURATION_DAYS)
        chunks: list[tuple[datetime, datetime]] = []
        c_start = start_date
        while c_start < end_date:
            c_end = min(c_start + chunk_delta, end_date)
            chunks.append((c_start, c_end))
            c_start = c_end

        num_chunks = len(chunks)

        _LOGGER.info(
            "Starting history rebuild for device '%s' (%s) from %s to %s in %d chunk(s)",
            device_cfg.name,
            device_cfg.source_entity,
            start_date.isoformat(),
            end_date.isoformat(),
            num_chunks,
        )

        # 3. Send initial start notification
        _send_progress_notification(
            self.hass,
            device_id=device_id,
            title=f"PowerCost - Démarrage du calcul ({device_cfg.name})",
            message=(
                f"⏳ **Reconstruction démarrée pour {device_cfg.name}**\n\n"
                f"- **Période demandée** : du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}\n"
                f"- **Découpage** : {num_chunks} tranche{'s' if num_chunks > 1 else ''} de {CHUNK_DURATION_DAYS} jours\n"
                f"- Traitement de la première tranche en cours..."
            ),
        )

        # Pre-fetch pricing intervals across whole period
        fallback_price = self.coordinator.pricing_manager.get_current_price() or 0.25
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
        )

        # Runtime structures
        stats = DeviceStatistics(device_id=device_id)
        unit = device_cfg.source_unit
        total_records_processed = 0

        # Hourly cost delta accumulators for retroactive statistics injection
        hourly_cost_deltas: dict[datetime, float] = {}
        hourly_cost_deltas_offpeak: dict[datetime, float] = {}
        hourly_cost_deltas_peak: dict[datetime, float] = {}

        last_val: float | None = None
        last_ts: float | None = None
        prev_sum: float | None = None
        prev_state_val: float | None = None

        # 4. Process chunk by chunk
        for idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
            _LOGGER.info(
                "Processing chunk %d/%d for '%s': %s -> %s",
                idx,
                num_chunks,
                device_cfg.name,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
            )

            # A. Fetch raw states for this chunk
            chunk_history = await self._async_run_recorder_job(
                self._fetch_history,
                chunk_start,
                chunk_end,
                entity_ids_to_query,
            )
            source_states = chunk_history.get(device_cfg.source_entity, [])

            # B. If raw states missing in this chunk, try Long-Term Statistics (LTS)
            lts_stats: list[dict[str, Any]] = []
            if not source_states:
                lts_stats = await self._async_run_recorder_job(
                    self._fetch_statistics,
                    chunk_start,
                    chunk_end,
                    device_cfg.source_entity,
                )

            # Replay LTS if used
            if lts_stats:
                for stat_item in lts_stats:
                    t_start = _parse_stat_timestamp(stat_item.get("start"))
                    t_end = _parse_stat_timestamp(stat_item.get("end"))
                    dt_local = dt_util.as_local(t_end)

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
                        detailed = self.coordinator.pricing_manager.calculate_detailed_cost_for_time_range(
                            start_time=t_start,
                            end_time=t_end,
                            energy_kwh=energy_delta,
                            price_intervals=price_intervals,
                            fallback_price=fallback_price,
                        )
                        stats.add_consumption(
                            energy_kwh=energy_delta,
                            cost=detailed.total_cost,
                            timestamp=dt_local,
                            cost_offpeak=detailed.cost_offpeak,
                            cost_peak=detailed.cost_peak,
                            energy_offpeak=detailed.energy_offpeak,
                            energy_peak=detailed.energy_peak,
                        )

                        # Record for hourly bucket
                        h_bucket = t_end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                        hourly_cost_deltas[h_bucket] = hourly_cost_deltas.get(h_bucket, 0.0) + detailed.total_cost
                        if detailed.cost_offpeak > 0:
                            hourly_cost_deltas_offpeak[h_bucket] = (
                                hourly_cost_deltas_offpeak.get(h_bucket, 0.0) + detailed.cost_offpeak
                            )
                        if detailed.cost_peak > 0:
                            hourly_cost_deltas_peak[h_bucket] = (
                                hourly_cost_deltas_peak.get(h_bucket, 0.0) + detailed.cost_peak
                            )

                    total_records_processed += 1

            # Replay raw states
            elif source_states:
                sorted_source_states = sorted(source_states, key=lambda s: s.last_updated)
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
                            detailed = self.coordinator.pricing_manager.calculate_detailed_cost_for_time_range(
                                start_time=step_start_utc,
                                end_time=step_end_utc,
                                energy_kwh=energy_delta,
                                price_intervals=price_intervals,
                                fallback_price=fallback_price,
                            )
                            stats.add_consumption(
                                energy_kwh=energy_delta,
                                cost=detailed.total_cost,
                                timestamp=dt_local,
                                cost_offpeak=detailed.cost_offpeak,
                                cost_peak=detailed.cost_peak,
                                energy_offpeak=detailed.energy_offpeak,
                                energy_peak=detailed.energy_peak,
                            )

                            # Record for hourly bucket
                            h_bucket = step_end_utc.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                            hourly_cost_deltas[h_bucket] = hourly_cost_deltas.get(h_bucket, 0.0) + detailed.total_cost
                            if detailed.cost_offpeak > 0:
                                hourly_cost_deltas_offpeak[h_bucket] = (
                                    hourly_cost_deltas_offpeak.get(h_bucket, 0.0) + detailed.cost_offpeak
                                )
                            if detailed.cost_peak > 0:
                                hourly_cost_deltas_peak[h_bucket] = (
                                    hourly_cost_deltas_peak.get(h_bucket, 0.0) + detailed.cost_peak
                                )

                    last_val = val
                    last_ts = current_ts
                    total_records_processed += 1

            # Update coordinator in real-time so UI sensors update as chunks complete
            self.coordinator.statistics[device_id] = stats
            await self.coordinator.async_save_data()
            self.coordinator.async_update_listeners()

            # Send progress notification after this chunk
            pct = int((idx / num_chunks) * 100)
            _send_progress_notification(
                self.hass,
                device_id=device_id,
                title=f"PowerCost - Progression ({idx}/{num_chunks})",
                message=(
                    f"⏳ **Reconstruction en cours pour {device_cfg.name}**\n\n"
                    f"- **Progression** : Tranche {idx}/{num_chunks} ({pct}%)\n"
                    f"- **Tranche traitée** : du {chunk_start.strftime('%d/%m/%Y')} au {chunk_end.strftime('%d/%m/%Y')}\n"
                    f"- **Enregistrements cumulés** : {total_records_processed}\n"
                    f"- **Consommation calculée** : {stats.energy_total:.2f} kWh\n"
                    f"- **Coût cumulé actuel** : {stats.cost_total:.2f} €"
                ),
            )

        # 5. Check if any data was processed
        if total_records_processed == 0:
            _LOGGER.warning(
                "No recorder history found for entity %s between %s and %s",
                device_cfg.source_entity,
                start_date.isoformat(),
                end_date.isoformat(),
            )
            _send_progress_notification(
                self.hass,
                device_id=device_id,
                title="PowerCost - Aucune donnée trouvée",
                message=(
                    f"⚠️ **Aucune donnée trouvée pour {device_cfg.name}**\n\n"
                    f"- **Période** : du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}\n"
                    f"- Aucune mesure n'a été trouvée dans le Recorder pour `{device_cfg.source_entity}`."
                ),
            )
            return {
                "success": False,
                "message": (
                    f"Aucune donnée d'historique trouvée dans le Recorder pour "
                    f"'{device_cfg.source_entity}' entre {start_date.strftime('%d/%m/%Y')} et {end_date.strftime('%d/%m/%Y')}."
                ),
                "records_processed": 0,
            }

        # 6. Finalize statistics
        stats.last_source_value = last_val
        stats.last_source_timestamp = last_ts
        stats.last_source_unit = unit
        stats.last_rebuild_timestamp = dt_util.utcnow().timestamp()

        self.coordinator.statistics[device_id] = stats
        await self.coordinator.async_save_data()
        self.coordinator.async_update_listeners()

        # 7. Inject retroactive statistics curves into Home Assistant Long-Term Statistics
        imported_stats_count = await self._async_inject_historical_statistics(
            device_cfg=device_cfg,
            start_date=start_date,
            end_date=end_date,
            hourly_cost_deltas=hourly_cost_deltas,
            hourly_cost_deltas_offpeak=hourly_cost_deltas_offpeak,
            hourly_cost_deltas_peak=hourly_cost_deltas_peak,
        )

        _LOGGER.info(
            "Rebuild complete for %s. Total cost: %.2f EUR, Total energy: %.2f kWh across %d records. Imported %d LTS points.",
            device_cfg.name,
            stats.cost_total,
            stats.energy_total,
            total_records_processed,
            imported_stats_count,
        )

        # 8. Send final completion notification
        lts_info = (
            f"\n- **Points de courbes injectés** : {imported_stats_count} points horaires (graphiques jour, mois, total et HC/HP à jour !)"
            if imported_stats_count > 0
            else ""
        )
        tariff_breakdown = ""
        if pricing_cfg.mode == PRICING_MODE_PEAK_OFFPEAK:
            tariff_breakdown = (
                f"\n- **Coût Heures Creuses** : {stats.cost_total_offpeak:.2f} € ({stats.energy_total_offpeak:.2f} kWh)"
                f"\n- **Coût Heures Pleines** : {stats.cost_total_peak:.2f} € ({stats.energy_total_peak:.2f} kWh)"
            )

        _send_progress_notification(
            self.hass,
            device_id=device_id,
            title="PowerCost - Historique reconstruit avec succès",
            message=(
                f"✅ **Reconstruction terminée pour {device_cfg.name}**\n\n"
                f"- **Période totale** : du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}\n"
                f"- **Tranches traitées** : {num_chunks}/{num_chunks} (100%)\n"
                f"- **Total enregistrements traités** : {total_records_processed}\n"
                f"- **Consommation totale** : {stats.energy_total:.2f} kWh\n"
                f"- **Coût total calculé** : {stats.cost_total:.2f} €"
                f"{tariff_breakdown}"
                f"{lts_info}"
            ),
        )

        return {
            "success": True,
            "device_id": device_id,
            "cost_total": stats.cost_total,
            "energy_total": stats.energy_total,
            "records_processed": total_records_processed,
            "statistics_imported": imported_stats_count,
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
    ) -> int:
        """Build and import historical statistics curves for Day, Month, Year, and Total sensors."""
        start_hour = start_date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end_hour = end_date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

        points_day, points_month, points_year, points_total = self._build_cumulative_curve_points(
            start_hour, end_hour, hourly_cost_deltas
        )

        total_imported_points = 0

        try:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(self.hass)
            unique_prefix = f"{self.coordinator.entry_id}_{device_cfg.device_id}_"

            mappings = [
                (SENSOR_COST_DAY, f"{device_cfg.name} Coût aujourd'hui", points_day),
                (SENSOR_COST_MONTH, f"{device_cfg.name} Coût ce mois", points_month),
                (SENSOR_COST_YEAR, f"{device_cfg.name} Coût cette année", points_year),
                (SENSOR_COST_TOTAL, f"{device_cfg.name} Coût total", points_total),
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
                        (SENSOR_COST_DAY_OFFPEAK, f"{device_cfg.name} Coût aujourd'hui (Heures Creuses)", hc_d),
                        (SENSOR_COST_MONTH_OFFPEAK, f"{device_cfg.name} Coût ce mois (Heures Creuses)", hc_m),
                        (SENSOR_COST_YEAR_OFFPEAK, f"{device_cfg.name} Coût cette année (Heures Creuses)", hc_y),
                        (SENSOR_COST_TOTAL_OFFPEAK, f"{device_cfg.name} Coût total (Heures Creuses)", hc_t),
                        (SENSOR_COST_DAY_PEAK, f"{device_cfg.name} Coût aujourd'hui (Heures Pleines)", hp_d),
                        (SENSOR_COST_MONTH_PEAK, f"{device_cfg.name} Coût ce mois (Heures Pleines)", hp_m),
                        (SENSOR_COST_YEAR_PEAK, f"{device_cfg.name} Coût cette année (Heures Pleines)", hp_y),
                        (SENSOR_COST_TOTAL_PEAK, f"{device_cfg.name} Coût total (Heures Pleines)", hp_t),
                    ]
                )

            for sensor_key, friendly_name, pts in mappings:
                entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{unique_prefix}{sensor_key}")
                if entity_id and pts:
                    await self._async_import_sensor_stats(entity_id, friendly_name, pts)
                    total_imported_points += len(pts)

        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Could not inject statistics for %s: %s", device_cfg.name, err)

        return total_imported_points

    async def _async_import_sensor_stats(
        self,
        entity_id: str,
        name: str,
        points: list[dict[str, Any]],
    ) -> None:
        """Import statistics data points into Home Assistant recorder."""
        if not points or not entity_id:
            return

        try:
            from homeassistant.components.recorder.statistics import async_import_statistics

            metadata: dict[str, Any] = {
                "has_mean": False,
                "has_sum": True,
                "mean_type": None,
                "name": name,
                "source": "recorder",
                "statistic_id": entity_id,
                "unit_of_measurement": "€",
                "unit_class": None,
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
