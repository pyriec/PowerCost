"""History reconstruction service and recorder interface for PowerCost."""

from __future__ import annotations

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
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
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
            raise ValueError(f"Appareil '{device_id}' non trouvé dans la configuration PowerCost")

        pricing_cfg = self.coordinator.pricing_config

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
            price_states_all = await self.hass.async_add_executor_job(
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

        # Reconstruct statistics chronologically across all chunks
        stats = DeviceStatistics(device_id=device_id)
        unit = device_cfg.source_unit
        total_records_processed = 0

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
            chunk_history = await self.hass.async_add_executor_job(
                self._fetch_history,
                chunk_start,
                chunk_end,
                entity_ids_to_query,
            )
            source_states = chunk_history.get(device_cfg.source_entity, [])

            # B. If raw states missing in this chunk, try Long-Term Statistics (LTS)
            lts_stats: list[dict[str, Any]] = []
            if not source_states:
                lts_stats = await self.hass.async_add_executor_job(
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
                        step_cost = self.coordinator.pricing_manager.calculate_cost_for_time_range(
                            start_time=t_start,
                            end_time=t_end,
                            energy_kwh=energy_delta,
                            price_intervals=price_intervals,
                            fallback_price=fallback_price,
                        )
                        stats.add_consumption(energy_delta, step_cost, dt_local)

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

        _LOGGER.info(
            "Rebuild complete for %s. Total cost: %.2f EUR, Total energy: %.2f kWh across %d records",
            device_cfg.name,
            stats.cost_total,
            stats.energy_total,
            total_records_processed,
        )

        # 7. Send final completion notification
        _send_progress_notification(
            self.hass,
            device_id=device_id,
            title="PowerCost - Historique reconstruit avec succès",
            message=(
                f"✅ **Reconstruction terminée pour {device_cfg.name}**\n\n"
                f"- **Période** : du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}\n"
                f"- **Tranches traitées** : {num_chunks}/{num_chunks} (100%)\n"
                f"- **Total enregistrements traités** : {total_records_processed}\n"
                f"- **Consommation totale** : {stats.energy_total:.2f} kWh\n"
                f"- **Coût total calculé** : {stats.cost_total:.2f} €"
            ),
        )

        return {
            "success": True,
            "device_id": device_id,
            "cost_total": stats.cost_total,
            "energy_total": stats.energy_total,
            "records_processed": total_records_processed,
        }

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
