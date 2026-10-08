"""Calculation logic for consumption and power integration."""

from __future__ import annotations

from datetime import datetime
import logging

from .const import (
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

_LOGGER = logging.getLogger(__name__)

# Maximum gap for power integration (2 hours). Gaps larger than this are treated as downtime.
MAX_POWER_INTEGRATION_GAP_SECONDS = 7200.0


def normalize_power_to_kw(value: float, unit: str | None) -> float:
    """Convert power reading to kW."""
    if not unit:
        # Default assume W unless value is unreasonably small for typical devices
        return value / 1000.0

    normalized_unit = unit.strip()
    if normalized_unit == UNIT_KW:
        return value
    if normalized_unit == UNIT_W:
        return value / 1000.0
    return value / 1000.0


def normalize_energy_to_kwh(value: float, unit: str | None) -> float:
    """Convert energy reading to kWh."""
    if not unit:
        return value

    normalized_unit = unit.strip()
    if normalized_unit == UNIT_KWH:
        return value
    if normalized_unit == UNIT_WH:
        return value / 1000.0
    if normalized_unit == UNIT_MWH:
        return value * 1000.0
    return value


def calculate_power_consumption(
    last_power_val: float | None,
    current_power_val: float,
    last_ts: float | None,
    current_ts: float,
    unit: str | None,
) -> float:
    """Integrate power over elapsed time to produce kWh.

    Uses trapezoidal integration between last and current readings.
    """
    if last_power_val is None or last_ts is None:
        return 0.0

    elapsed_seconds = current_ts - last_ts
    if elapsed_seconds <= 0.0:
        return 0.0

    if elapsed_seconds > MAX_POWER_INTEGRATION_GAP_SECONDS:
        _LOGGER.debug(
            "Power reading gap of %.1f seconds exceeds limit (%.1f s). Skipping integration.",
            elapsed_seconds,
            MAX_POWER_INTEGRATION_GAP_SECONDS,
        )
        return 0.0

    p0_kw = normalize_power_to_kw(last_power_val, unit)
    p1_kw = normalize_power_to_kw(current_power_val, unit)

    # Average power in kW
    avg_power_kw = (p0_kw + p1_kw) / 2.0
    hours = elapsed_seconds / 3600.0
    kwh = avg_power_kw * hours

    return round(max(0.0, kwh), 6)


def calculate_energy_delta(
    source_type: str,
    last_val: float | None,
    current_val: float,
    unit: str | None,
) -> float:
    """Calculate delta consumption in kWh handling resets and different source types."""
    curr_kwh = normalize_energy_to_kwh(current_val, unit)

    if last_val is None:
        return 0.0

    prev_kwh = normalize_energy_to_kwh(last_val, unit)

    if curr_kwh >= prev_kwh:
        # Standard monotonically increasing delta
        return round(curr_kwh - prev_kwh, 6)

    # Value dropped: curr_kwh < prev_kwh -> Reset detected!
    if source_type in (
        SOURCE_TYPE_ENERGY_DAILY,
        SOURCE_TYPE_ENERGY_MONTHLY,
        SOURCE_TYPE_ENERGY_YEARLY,
    ):
        # The sensor reset to 0 at midnight / new month / new year.
        # The previous period ended with prev_kwh, and the current cycle already accumulated curr_kwh.
        _LOGGER.debug(
            "Period reset detected for %s: drop from %.4f to %.4f kWh. Accounting new cycle delta = %.4f kWh",
            source_type,
            prev_kwh,
            curr_kwh,
            curr_kwh,
        )
        return round(curr_kwh, 6)

    if source_type == SOURCE_TYPE_ENERGY_TOTAL:
        # Total energy counter dropped. Could be hardware replacement, rollover or recalibration.
        # To avoid creating massive false consumption or negative consumption:
        _LOGGER.warning(
            "Total energy counter drop detected: %.4f -> %.4f kWh. Treating as counter reset/recalibration.",
            prev_kwh,
            curr_kwh,
        )
        # If reset to a very small number close to 0 (<= 1.0 kWh), consider it a zero-reset:
        if curr_kwh <= 1.0:
            return round(curr_kwh, 6)
        # Otherwise, re-anchor baseline without registering delta
        return 0.0

    return 0.0
