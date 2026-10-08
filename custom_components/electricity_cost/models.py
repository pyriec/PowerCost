"""Data models for PowerCost integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .const import (
    CONF_OFFPEAK_PRICE_ENTITY,
    CONF_OFFPEAK_STATE,
    CONF_PEAK_PRICE_ENTITY,
    CONF_PEAK_STATE,
    CONF_PRICING_MODE,
    CONF_TARIFF_MODE_ENTITY,
    CONF_VARIABLE_PRICE_ENTITY,
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_POWER,
)


@dataclass
class PricingConfig:
    """Pricing configuration."""

    mode: str = PRICING_MODE_VARIABLE
    variable_price_entity: str | None = None
    offpeak_price_entity: str | None = None
    peak_price_entity: str | None = None
    tariff_mode_entity: str | None = None
    offpeak_state: str | None = None
    peak_state: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PricingConfig:
        """Create from dictionary."""
        return cls(
            mode=data.get(CONF_PRICING_MODE) or data.get("mode", PRICING_MODE_VARIABLE),
            variable_price_entity=data.get(CONF_VARIABLE_PRICE_ENTITY) or data.get("variable_price_entity"),
            offpeak_price_entity=data.get(CONF_OFFPEAK_PRICE_ENTITY) or data.get("offpeak_price_entity"),
            peak_price_entity=data.get(CONF_PEAK_PRICE_ENTITY) or data.get("peak_price_entity"),
            tariff_mode_entity=data.get(CONF_TARIFF_MODE_ENTITY) or data.get("tariff_mode_entity"),
            offpeak_state=data.get(CONF_OFFPEAK_STATE) or data.get("offpeak_state"),
            peak_state=data.get(CONF_PEAK_STATE) or data.get("peak_state"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        res = asdict(self)
        res[CONF_PRICING_MODE] = self.mode
        return res


@dataclass
class DeviceConfig:
    """Device configuration."""

    device_id: str
    name: str
    source_entity: str
    source_type: str = SOURCE_TYPE_POWER
    source_unit: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceConfig:
        """Create from dictionary."""
        return cls(
            device_id=data["device_id"],
            name=data.get("name") or data.get("device_name", ""),
            source_entity=data["source_entity"],
            source_type=data.get("source_type", SOURCE_TYPE_POWER),
            source_unit=data.get("source_unit"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class DeviceStatistics:
    """Runtime and persistent statistics for a single device."""

    device_id: str
    cost_today: float = 0.0
    cost_this_month: float = 0.0
    cost_this_year: float = 0.0
    cost_total: float = 0.0

    energy_today: float = 0.0
    energy_this_month: float = 0.0
    energy_this_year: float = 0.0
    energy_total: float = 0.0

    # Off-peak / Peak breakdowns
    cost_today_offpeak: float = 0.0
    cost_today_peak: float = 0.0
    cost_this_month_offpeak: float = 0.0
    cost_this_month_peak: float = 0.0
    cost_this_year_offpeak: float = 0.0
    cost_this_year_peak: float = 0.0
    cost_total_offpeak: float = 0.0
    cost_total_peak: float = 0.0

    energy_today_offpeak: float = 0.0
    energy_today_peak: float = 0.0
    energy_this_month_offpeak: float = 0.0
    energy_this_month_peak: float = 0.0
    energy_this_year_offpeak: float = 0.0
    energy_this_year_peak: float = 0.0
    energy_total_offpeak: float = 0.0
    energy_total_peak: float = 0.0

    max_cost_day: float = 0.0
    max_cost_month: float = 0.0
    max_cost_year: float = 0.0

    # Historical aggregations: date string -> cost
    daily_history: dict[str, float] = field(default_factory=dict)
    monthly_history: dict[str, float] = field(default_factory=dict)
    yearly_history: dict[str, float] = field(default_factory=dict)

    # Tracking periods
    current_day: str = ""
    current_month: str = ""
    current_year: str = ""

    # Last tracking source state
    last_source_value: float | None = None
    last_source_timestamp: float | None = None
    last_source_unit: str | None = None
    last_rebuild_timestamp: float | None = None

    @property
    def avg_cost_day(self) -> float:
        """Calculate average daily cost based on daily history."""
        # Include completed days plus current day if has cost
        costs = list(self.daily_history.values())
        if self.current_day and self.current_day not in self.daily_history and self.cost_today > 0:
            costs.append(self.cost_today)
        return (sum(costs) / len(costs)) if costs else round(self.cost_today, 4)

    @property
    def avg_cost_month(self) -> float:
        """Calculate average monthly cost based on monthly history."""
        costs = list(self.monthly_history.values())
        if self.current_month and self.current_month not in self.monthly_history and self.cost_this_month > 0:
            costs.append(self.cost_this_month)
        return (sum(costs) / len(costs)) if costs else round(self.cost_this_month, 4)

    @property
    def avg_cost_year(self) -> float:
        """Calculate average yearly cost based on yearly history."""
        costs = list(self.yearly_history.values())
        if self.current_year and self.current_year not in self.yearly_history and self.cost_this_year > 0:
            costs.append(self.cost_this_year)
        return (sum(costs) / len(costs)) if costs else round(self.cost_this_year, 4)

    def rollover_if_needed(self, now: datetime) -> bool:
        """Check and handle period transitions (day, month, year) using local datetime."""
        changed = False
        day_str = now.strftime("%Y-%m-%d")
        month_str = now.strftime("%Y-%m")
        year_str = now.strftime("%Y")

        if not self.current_day:
            self.current_day = day_str
            self.current_month = month_str
            self.current_year = year_str
            return False

        # Day rollover
        if day_str != self.current_day:
            # Save completed day into daily history
            if self.current_day:
                self.daily_history[self.current_day] = round(self.cost_today, 4)
                if self.cost_today > self.max_cost_day:
                    self.max_cost_day = round(self.cost_today, 4)
            self.cost_today = 0.0
            self.energy_today = 0.0
            self.cost_today_offpeak = 0.0
            self.cost_today_peak = 0.0
            self.energy_today_offpeak = 0.0
            self.energy_today_peak = 0.0
            self.current_day = day_str
            changed = True

        # Month rollover
        if month_str != self.current_month:
            if self.current_month:
                self.monthly_history[self.current_month] = round(self.cost_this_month, 4)
                if self.cost_this_month > self.max_cost_month:
                    self.max_cost_month = round(self.cost_this_month, 4)
            self.cost_this_month = 0.0
            self.energy_this_month = 0.0
            self.cost_this_month_offpeak = 0.0
            self.cost_this_month_peak = 0.0
            self.energy_this_month_offpeak = 0.0
            self.energy_this_month_peak = 0.0
            self.current_month = month_str
            changed = True

        # Year rollover
        if year_str != self.current_year:
            if self.current_year:
                self.yearly_history[self.current_year] = round(self.cost_this_year, 4)
                if self.cost_this_year > self.max_cost_year:
                    self.max_cost_year = round(self.cost_this_year, 4)
            self.cost_this_year = 0.0
            self.energy_this_year = 0.0
            self.cost_this_year_offpeak = 0.0
            self.cost_this_year_peak = 0.0
            self.energy_this_year_offpeak = 0.0
            self.energy_this_year_peak = 0.0
            self.current_year = year_str
            changed = True

        return changed

    def add_consumption(
        self,
        energy_kwh: float,
        cost: float,
        timestamp: datetime,
        tariff: str | None = None,
        cost_offpeak: float | None = None,
        cost_peak: float | None = None,
        energy_offpeak: float | None = None,
        energy_peak: float | None = None,
    ) -> None:
        """Add incremental energy and cost to statistics."""
        self.rollover_if_needed(timestamp)

        self.cost_today = round(self.cost_today + cost, 4)
        self.cost_this_month = round(self.cost_this_month + cost, 4)
        self.cost_this_year = round(self.cost_this_year + cost, 4)
        self.cost_total = round(self.cost_total + cost, 4)

        self.energy_today = round(self.energy_today + energy_kwh, 4)
        self.energy_this_month = round(self.energy_this_month + energy_kwh, 4)
        self.energy_this_year = round(self.energy_this_year + energy_kwh, 4)
        self.energy_total = round(self.energy_total + energy_kwh, 4)

        # Off-peak / Peak breakdown
        c_offpeak = cost_offpeak if cost_offpeak is not None else (cost if tariff == "offpeak" else 0.0)
        c_peak = cost_peak if cost_peak is not None else (cost if tariff == "peak" else 0.0)
        e_offpeak = energy_offpeak if energy_offpeak is not None else (energy_kwh if tariff == "offpeak" else 0.0)
        e_peak = energy_peak if energy_peak is not None else (energy_kwh if tariff == "peak" else 0.0)

        if c_offpeak > 0.0:
            self.cost_today_offpeak = round(self.cost_today_offpeak + c_offpeak, 4)
            self.cost_this_month_offpeak = round(self.cost_this_month_offpeak + c_offpeak, 4)
            self.cost_this_year_offpeak = round(self.cost_this_year_offpeak + c_offpeak, 4)
            self.cost_total_offpeak = round(self.cost_total_offpeak + c_offpeak, 4)

        if c_peak > 0.0:
            self.cost_today_peak = round(self.cost_today_peak + c_peak, 4)
            self.cost_this_month_peak = round(self.cost_this_month_peak + c_peak, 4)
            self.cost_this_year_peak = round(self.cost_this_year_peak + c_peak, 4)
            self.cost_total_peak = round(self.cost_total_peak + c_peak, 4)

        if e_offpeak > 0.0:
            self.energy_today_offpeak = round(self.energy_today_offpeak + e_offpeak, 4)
            self.energy_this_month_offpeak = round(self.energy_this_month_offpeak + e_offpeak, 4)
            self.energy_this_year_offpeak = round(self.energy_this_year_offpeak + e_offpeak, 4)
            self.energy_total_offpeak = round(self.energy_total_offpeak + e_offpeak, 4)

        if e_peak > 0.0:
            self.energy_today_peak = round(self.energy_today_peak + e_peak, 4)
            self.energy_this_month_peak = round(self.energy_this_month_peak + e_peak, 4)
            self.energy_this_year_peak = round(self.energy_this_year_peak + e_peak, 4)
            self.energy_total_peak = round(self.energy_total_peak + e_peak, 4)

        if self.cost_today > self.max_cost_day:
            self.max_cost_day = self.cost_today
        if self.cost_this_month > self.max_cost_month:
            self.max_cost_month = self.cost_this_month
        if self.cost_this_year > self.max_cost_year:
            self.max_cost_year = self.cost_this_year

    def to_dict(self) -> dict[str, Any]:
        """Convert statistics to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceStatistics:
        """Construct from dictionary."""
        return cls(
            device_id=data.get("device_id", ""),
            cost_today=float(data.get("cost_today", 0.0)),
            cost_this_month=float(data.get("cost_this_month", 0.0)),
            cost_this_year=float(data.get("cost_this_year", 0.0)),
            cost_total=float(data.get("cost_total", 0.0)),
            energy_today=float(data.get("energy_today", 0.0)),
            energy_this_month=float(data.get("energy_this_month", 0.0)),
            energy_this_year=float(data.get("energy_this_year", 0.0)),
            energy_total=float(data.get("energy_total", 0.0)),
            cost_today_offpeak=float(data.get("cost_today_offpeak", 0.0)),
            cost_today_peak=float(data.get("cost_today_peak", 0.0)),
            cost_this_month_offpeak=float(data.get("cost_this_month_offpeak", 0.0)),
            cost_this_month_peak=float(data.get("cost_this_month_peak", 0.0)),
            cost_this_year_offpeak=float(data.get("cost_this_year_offpeak", 0.0)),
            cost_this_year_peak=float(data.get("cost_this_year_peak", 0.0)),
            cost_total_offpeak=float(data.get("cost_total_offpeak", 0.0)),
            cost_total_peak=float(data.get("cost_total_peak", 0.0)),
            energy_today_offpeak=float(data.get("energy_today_offpeak", 0.0)),
            energy_today_peak=float(data.get("energy_today_peak", 0.0)),
            energy_this_month_offpeak=float(data.get("energy_this_month_offpeak", 0.0)),
            energy_this_month_peak=float(data.get("energy_this_month_peak", 0.0)),
            energy_this_year_offpeak=float(data.get("energy_this_year_offpeak", 0.0)),
            energy_this_year_peak=float(data.get("energy_this_year_peak", 0.0)),
            energy_total_offpeak=float(data.get("energy_total_offpeak", 0.0)),
            energy_total_peak=float(data.get("energy_total_peak", 0.0)),
            max_cost_day=float(data.get("max_cost_day", 0.0)),
            max_cost_month=float(data.get("max_cost_month", 0.0)),
            max_cost_year=float(data.get("max_cost_year", 0.0)),
            daily_history={str(k): float(v) for k, v in data.get("daily_history", {}).items()},
            monthly_history={str(k): float(v) for k, v in data.get("monthly_history", {}).items()},
            yearly_history={str(k): float(v) for k, v in data.get("yearly_history", {}).items()},
            current_day=str(data.get("current_day", "")),
            current_month=str(data.get("current_month", "")),
            current_year=str(data.get("current_year", "")),
            last_source_value=data.get("last_source_value"),
            last_source_timestamp=data.get("last_source_timestamp"),
            last_source_unit=data.get("last_source_unit"),
            last_rebuild_timestamp=data.get("last_rebuild_timestamp"),
        )
