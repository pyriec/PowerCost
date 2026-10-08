"""Unit tests for config flow and options flow."""

import pytest

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.electricity_cost.const import (
    CONF_DEVICES,
    CONF_DEVICE_NAME,
    CONF_OFFPEAK_PRICE_ENTITY,
    CONF_OFFPEAK_STATE,
    CONF_PEAK_PRICE_ENTITY,
    CONF_PEAK_STATE,
    CONF_PRICING_MODE,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_TARIFF_MODE_ENTITY,
    CONF_VARIABLE_PRICE_ENTITY,
    DOMAIN,
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_POWER,
)


@pytest.mark.asyncio
async def test_config_flow_variable_pricing(hass):
    """Test full configuration flow with variable price."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Select Variable pricing
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PRICING_MODE: PRICING_MODE_VARIABLE},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "variable_pricing"

    # Select variable price sensor
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {CONF_VARIABLE_PRICE_ENTITY: "sensor.electricity_market_price"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "device_add"

    # Add first device
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        {
            CONF_DEVICE_NAME: "Lave Linge",
            CONF_SOURCE_ENTITY: "sensor.lave_linge_power",
            CONF_SOURCE_TYPE: SOURCE_TYPE_POWER,
        },
    )
    assert result4["type"] == FlowResultType.CREATE_ENTRY
    assert result4["title"] == "Electricity Cost"
    assert result4["data"][CONF_PRICING_MODE] == PRICING_MODE_VARIABLE
    assert result4["data"][CONF_VARIABLE_PRICE_ENTITY] == "sensor.electricity_market_price"
    assert len(result4["data"][CONF_DEVICES]) == 1
    assert result4["data"][CONF_DEVICES][0][CONF_DEVICE_NAME] == "Lave Linge"


@pytest.mark.asyncio
async def test_config_flow_peak_offpeak(hass):
    """Test full configuration flow with peak/off-peak pricing and dynamic state detection."""
    # Create tariff sensor with attributes options
    hass.states.async_set(
        "sensor.edf_tempo",
        "creuses",
        {"options": ["creuses", "pleines"]},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"

    # Choose peak / off-peak
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PRICING_MODE: PRICING_MODE_PEAK_OFFPEAK},
    )
    assert result2["step_id"] == "peak_offpeak_pricing"

    # Configure entities
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {
            CONF_OFFPEAK_PRICE_ENTITY: "sensor.hc_price",
            CONF_PEAK_PRICE_ENTITY: "sensor.hp_price",
            CONF_TARIFF_MODE_ENTITY: "sensor.edf_tempo",
        },
    )
    assert result3["step_id"] == "peak_offpeak_states"

    # Map detected states
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        {
            CONF_OFFPEAK_STATE: "creuses",
            CONF_PEAK_STATE: "pleines",
        },
    )
    assert result4["step_id"] == "device_add"

    # Add device
    result5 = await hass.config_entries.flow.async_configure(
        result4["flow_id"],
        {
            CONF_DEVICE_NAME: "Chauffe-eau",
            CONF_SOURCE_ENTITY: "sensor.water_heater_power",
            CONF_SOURCE_TYPE: SOURCE_TYPE_POWER,
        },
    )
    assert result5["type"] == FlowResultType.CREATE_ENTRY
    assert result5["data"][CONF_OFFPEAK_STATE] == "creuses"
    assert result5["data"][CONF_PEAK_STATE] == "pleines"
