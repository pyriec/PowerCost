"""Constants for the PowerCost integration."""

from typing import Final

DOMAIN: Final = "electricity_cost"

# Pricing modes
CONF_PRICING_MODE: Final = "pricing_mode"
PRICING_MODE_VARIABLE: Final = "variable"
PRICING_MODE_PEAK_OFFPEAK: Final = "peak_offpeak"

# Mode Variable configuration
CONF_VARIABLE_PRICE_ENTITY: Final = "variable_price_entity"

# Mode Peak / Off-peak configuration
CONF_OFFPEAK_PRICE_ENTITY: Final = "offpeak_price_entity"
CONF_PEAK_PRICE_ENTITY: Final = "peak_price_entity"
CONF_TARIFF_MODE_ENTITY: Final = "tariff_mode_entity"
CONF_OFFPEAK_STATE: Final = "offpeak_state"
CONF_PEAK_STATE: Final = "peak_state"

# Devices configuration
CONF_DEVICES: Final = "devices"
CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_NAME: Final = "device_name"
CONF_SOURCE_ENTITY: Final = "source_entity"
CONF_SOURCE_TYPE: Final = "source_type"
CONF_SOURCE_UNIT: Final = "source_unit"

# Source types
SOURCE_TYPE_POWER: Final = "power"
SOURCE_TYPE_ENERGY_DAILY: Final = "energy_daily"
SOURCE_TYPE_ENERGY_MONTHLY: Final = "energy_monthly"
SOURCE_TYPE_ENERGY_YEARLY: Final = "energy_yearly"
SOURCE_TYPE_ENERGY_TOTAL: Final = "energy_total"

SOURCE_TYPES: Final = [
    SOURCE_TYPE_POWER,
    SOURCE_TYPE_ENERGY_DAILY,
    SOURCE_TYPE_ENERGY_MONTHLY,
    SOURCE_TYPE_ENERGY_YEARLY,
    SOURCE_TYPE_ENERGY_TOTAL,
]

# Supported units
UNIT_W: Final = "W"
UNIT_KW: Final = "kW"
UNIT_WH: Final = "Wh"
UNIT_KWH: Final = "kWh"
UNIT_MWH: Final = "MWh"

# Storage
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}_storage"

# Sensors generated per device
SENSOR_COST_DAY: Final = "cost_day"
SENSOR_COST_MONTH: Final = "cost_month"
SENSOR_COST_YEAR: Final = "cost_year"
SENSOR_COST_TOTAL: Final = "cost_total"

# Peak / Off-peak sensors
SENSOR_COST_DAY_OFFPEAK: Final = "cost_day_offpeak"
SENSOR_COST_DAY_PEAK: Final = "cost_day_peak"
SENSOR_COST_MONTH_OFFPEAK: Final = "cost_month_offpeak"
SENSOR_COST_MONTH_PEAK: Final = "cost_month_peak"
SENSOR_COST_YEAR_OFFPEAK: Final = "cost_year_offpeak"
SENSOR_COST_YEAR_PEAK: Final = "cost_year_peak"
SENSOR_COST_TOTAL_OFFPEAK: Final = "cost_total_offpeak"
SENSOR_COST_TOTAL_PEAK: Final = "cost_total_peak"

SENSOR_ENERGY_DAY_OFFPEAK: Final = "energy_day_offpeak"
SENSOR_ENERGY_DAY_PEAK: Final = "energy_day_peak"
SENSOR_ENERGY_TOTAL_OFFPEAK: Final = "energy_total_offpeak"
SENSOR_ENERGY_TOTAL_PEAK: Final = "energy_total_peak"

SENSOR_COST_MAX_DAY: Final = "cost_max_day"
SENSOR_COST_MAX_MONTH: Final = "cost_max_month"
SENSOR_COST_MAX_YEAR: Final = "cost_max_year"
SENSOR_COST_AVG_DAY: Final = "cost_avg_day"
SENSOR_COST_AVG_MONTH: Final = "cost_avg_month"
SENSOR_COST_AVG_YEAR: Final = "cost_avg_year"

SENSOR_TYPES: Final = (
    SENSOR_COST_DAY,
    SENSOR_COST_MONTH,
    SENSOR_COST_YEAR,
    SENSOR_COST_TOTAL,
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
    SENSOR_ENERGY_TOTAL_OFFPEAK,
    SENSOR_ENERGY_TOTAL_PEAK,
    SENSOR_COST_MAX_DAY,
    SENSOR_COST_MAX_MONTH,
    SENSOR_COST_MAX_YEAR,
    SENSOR_COST_AVG_DAY,
    SENSOR_COST_AVG_MONTH,
    SENSOR_COST_AVG_YEAR,
)

# Services
SERVICE_REBUILD_HISTORY: Final = "rebuild_history"
SERVICE_RESET_STATISTICS: Final = "reset_statistics"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_START_DATE: Final = "start_date"
ATTR_END_DATE: Final = "end_date"
ATTR_REBUILD_ALL: Final = "rebuild_all"

