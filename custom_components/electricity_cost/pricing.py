"""Pricing management and interval slicing for PowerCost integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State

from .const import (
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
)
from .models import PricingConfig

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


@dataclass
class PriceInterval:
    """Represents a continuous time interval with a constant price."""

    start: datetime
    end: datetime
    price: float
    tariff: str | None = None  # "offpeak", "peak", or None


@dataclass
class CostResult:
    """Detailed cost and energy breakdown across tariff periods."""

    total_cost: float
    cost_offpeak: float = 0.0
    cost_peak: float = 0.0
    energy_offpeak: float = 0.0
    energy_peak: float = 0.0



def parse_float_state(state: State | None) -> float | None:
    """Parse numeric state or return None."""
    if state is None:
        return None
    if state.state in ("unavailable", "unknown", "", "None"):
        return None
    try:
        val = float(state.state.replace(",", "."))
        return val if val >= 0 else None
    except (ValueError, TypeError):
        return None


class PricingManager:
    """Manages active pricing evaluation and timeline slicing."""

    def __init__(self, hass: HomeAssistant, config: PricingConfig) -> None:
        """Initialize the pricing manager."""
        self.hass = hass
        self.config = config

    def get_current_price(self) -> float | None:
        """Get the current applicable price in currency/kWh."""
        if self.config.mode == PRICING_MODE_VARIABLE:
            if not self.config.variable_price_entity:
                return None
            state = self.hass.states.get(self.config.variable_price_entity)
            return parse_float_state(state)

        if self.config.mode == PRICING_MODE_PEAK_OFFPEAK:
            if not self.config.tariff_mode_entity:
                return None
            mode_state = self.hass.states.get(self.config.tariff_mode_entity)
            if not mode_state or mode_state.state in ("unavailable", "unknown"):
                return None

            current_mode = str(mode_state.state).strip().lower()
            offpeak_target = str(self.config.offpeak_state or "").strip().lower()
            peak_target = str(self.config.peak_state or "").strip().lower()

            if current_mode == offpeak_target:
                if not self.config.offpeak_price_entity:
                    return None
                return parse_float_state(self.hass.states.get(self.config.offpeak_price_entity))
            if current_mode == peak_target:
                if not self.config.peak_price_entity:
                    return None
                return parse_float_state(self.hass.states.get(self.config.peak_price_entity))

            _LOGGER.warning(
                "Tariff state '%s' does not match off-peak ('%s') or peak ('%s')",
                mode_state.state,
                self.config.offpeak_state,
                self.config.peak_state,
            )
            return None

        return None

    def get_current_tariff(self) -> str | None:
        """Get the current applicable tariff ('offpeak' or 'peak') in peak/offpeak mode."""
        if self.config.mode != PRICING_MODE_PEAK_OFFPEAK:
            return None
        if not self.config.tariff_mode_entity:
            return None
        mode_state = self.hass.states.get(self.config.tariff_mode_entity)
        if not mode_state or mode_state.state in ("unavailable", "unknown"):
            return None

        current_mode = str(mode_state.state).strip().lower()
        offpeak_target = str(self.config.offpeak_state or "").strip().lower()
        peak_target = str(self.config.peak_state or "").strip().lower()

        if current_mode == offpeak_target:
            return "offpeak"
        if current_mode == peak_target:
            return "peak"
        return None

    def calculate_cost_for_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        energy_kwh: float,
        price_intervals: list[PriceInterval] | None = None,
        fallback_price: float | None = None,
    ) -> float:
        """Calculate total cost by slicing consumption across price intervals."""
        detailed = self.calculate_detailed_cost_for_time_range(
            start_time=start_time,
            end_time=end_time,
            energy_kwh=energy_kwh,
            price_intervals=price_intervals,
            fallback_price=fallback_price,
        )
        return detailed.total_cost

    def calculate_detailed_cost_for_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        energy_kwh: float,
        price_intervals: list[PriceInterval] | None = None,
        fallback_price: float | None = None,
    ) -> CostResult:
        """Calculate detailed cost and energy breakdown across price intervals."""
        if energy_kwh <= 0:
            return CostResult(total_cost=0.0)

        total_duration = (end_time - start_time).total_seconds()
        if total_duration <= 0:
            # Point in time: use current or fallback price
            p = fallback_price if fallback_price is not None else self.get_current_price()
            cost = round(energy_kwh * (p or 0.0), 4)
            t = self.get_current_tariff()
            if t == "offpeak":
                return CostResult(
                    total_cost=cost,
                    cost_offpeak=cost,
                    energy_offpeak=round(energy_kwh, 4),
                )
            if t == "peak":
                return CostResult(
                    total_cost=cost,
                    cost_peak=cost,
                    energy_peak=round(energy_kwh, 4),
                )
            return CostResult(total_cost=cost)

        if not price_intervals:
            p = fallback_price if fallback_price is not None else self.get_current_price()
            cost = round(energy_kwh * (p or 0.0), 4)
            t = self.get_current_tariff()
            if t == "offpeak":
                return CostResult(
                    total_cost=cost,
                    cost_offpeak=cost,
                    energy_offpeak=round(energy_kwh, 4),
                )
            if t == "peak":
                return CostResult(
                    total_cost=cost,
                    cost_peak=cost,
                    energy_peak=round(energy_kwh, 4),
                )
            return CostResult(total_cost=cost)

        # Slice the period against price intervals
        total_cost = 0.0
        cost_offpeak = 0.0
        cost_peak = 0.0
        energy_offpeak = 0.0
        energy_peak = 0.0
        covered_duration = 0.0

        for interval in price_intervals:
            # Calculate overlap [max(start, i_start), min(end, i_end)]
            overlap_start = max(start_time, interval.start)
            overlap_end = min(end_time, interval.end)

            if overlap_end > overlap_start:
                sub_duration = (overlap_end - overlap_start).total_seconds()
                sub_energy = energy_kwh * (sub_duration / total_duration)
                sub_cost = sub_energy * interval.price
                total_cost += sub_cost
                covered_duration += sub_duration

                if interval.tariff == "offpeak":
                    cost_offpeak += sub_cost
                    energy_offpeak += sub_energy
                elif interval.tariff == "peak":
                    cost_peak += sub_cost
                    energy_peak += sub_energy

        # Any unallocated duration uses fallback price
        remaining_duration = total_duration - covered_duration
        if remaining_duration > 1.0:  # Tolerance of 1 sec
            default_p = fallback_price if fallback_price is not None else (self.get_current_price() or 0.0)
            sub_energy = energy_kwh * (remaining_duration / total_duration)
            sub_cost = sub_energy * default_p
            total_cost += sub_cost

            t = self.get_current_tariff()
            if t == "offpeak":
                cost_offpeak += sub_cost
                energy_offpeak += sub_energy
            elif t == "peak":
                cost_peak += sub_cost
                energy_peak += sub_energy

        return CostResult(
            total_cost=round(total_cost, 4),
            cost_offpeak=round(cost_offpeak, 4),
            cost_peak=round(cost_peak, 4),
            energy_offpeak=round(energy_offpeak, 4),
            energy_peak=round(energy_peak, 4),
        )



def extract_timeline_intervals(
    mode: str,
    price_states: list[State],
    offpeak_states: list[State] | None = None,
    peak_states: list[State] | None = None,
    mode_states: list[State] | None = None,
    offpeak_target: str | None = None,
    peak_target: str | None = None,
    start_bound: datetime | None = None,
    end_bound: datetime | None = None,
) -> list[PriceInterval]:
    """Build a continuous sequence of PriceIntervals from recorded states."""
    # Collect all timestamps where anything changed
    all_events: list[datetime] = []
    if start_bound:
        all_events.append(start_bound)
    if end_bound:
        all_events.append(end_bound)

    def add_events(states: list[State] | None) -> None:
        if not states:
            return
        for s in states:
            dt = s.last_updated.astimezone(timezone.utc)
            all_events.append(dt)

    if mode == PRICING_MODE_VARIABLE:
        add_events(price_states)
    else:
        add_events(price_states)
        add_events(offpeak_states)
        add_events(peak_states)
        add_events(mode_states)

    if not all_events:
        return []

    sorted_events = sorted(set(all_events))
    if len(sorted_events) < 2:
        return []

    # Map state lookup at a given timestamp
    def get_val_at(states: list[State] | None, t: datetime) -> float | None:
        if not states:
            return None
        candidate: State | None = None
        for s in states:
            s_dt = s.last_updated.astimezone(timezone.utc)
            if s_dt <= t:
                candidate = s
            else:
                break
        return parse_float_state(candidate)

    def get_mode_at(states: list[State] | None, t: datetime) -> str | None:
        if not states:
            return None
        candidate: State | None = None
        for s in states:
            s_dt = s.last_updated.astimezone(timezone.utc)
            if s_dt <= t:
                candidate = s
            else:
                break
        if candidate and candidate.state not in ("unavailable", "unknown"):
            return str(candidate.state).strip().lower()
        return None

    intervals: list[PriceInterval] = []
    offpeak_t = str(offpeak_target or "").strip().lower()
    peak_t = str(peak_target or "").strip().lower()

    for idx in range(len(sorted_events) - 1):
        t0 = sorted_events[idx]
        t1 = sorted_events[idx + 1]
        mid = t0 + (t1 - t0) / 2

        applicable_price: float | None = None
        tariff: str | None = None

        if mode == PRICING_MODE_VARIABLE:
            applicable_price = get_val_at(price_states, mid)
        else:
            current_mode = get_mode_at(mode_states, mid)
            if current_mode == offpeak_t:
                applicable_price = get_val_at(offpeak_states, mid)
                tariff = "offpeak"
            elif current_mode == peak_t:
                applicable_price = get_val_at(peak_states, mid)
                tariff = "peak"

        if applicable_price is not None and applicable_price >= 0:
            intervals.append(PriceInterval(start=t0, end=t1, price=applicable_price, tariff=tariff))

    return intervals
