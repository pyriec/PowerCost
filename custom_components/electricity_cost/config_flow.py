"""Config Flow and Options Flow for PowerCost."""

from __future__ import annotations

import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_OFFPEAK_PRICE_ENTITY,
    CONF_OFFPEAK_STATE,
    CONF_PEAK_PRICE_ENTITY,
    CONF_PEAK_STATE,
    CONF_PRICING_MODE,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_TYPE,
    CONF_SOURCE_UNIT,
    CONF_TARIFF_MODE_ENTITY,
    CONF_VARIABLE_PRICE_ENTITY,
    DOMAIN,
    PRICING_MODE_PEAK_OFFPEAK,
    PRICING_MODE_VARIABLE,
    SOURCE_TYPE_ENERGY_DAILY,
    SOURCE_TYPE_ENERGY_MONTHLY,
    SOURCE_TYPE_ENERGY_TOTAL,
    SOURCE_TYPE_ENERGY_YEARLY,
    SOURCE_TYPE_POWER,
    SOURCE_TYPES,
)
from .models import DeviceConfig, PricingConfig

_LOGGER = logging.getLogger(__name__)


def detect_entity_states(hass: Any, entity_id: str) -> list[str]:
    """Detect possible states of a mode/tariff entity."""
    state_obj = hass.states.get(entity_id)
    detected: set[str] = set()

    if state_obj:
        if state_obj.state not in ("unavailable", "unknown", ""):
            detected.add(state_obj.state)
        # Check if select or input_select with options attribute
        if "options" in state_obj.attributes and isinstance(state_obj.attributes["options"], list):
            detected.update(str(opt) for opt in state_obj.attributes["options"])

    if not detected:
        detected = {"off_peak", "peak", "heures_creuses", "heures_pleines", "hc", "hp"}

    return sorted(detected)


class ElectricityCostConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PowerCost."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._pricing_mode: str = PRICING_MODE_VARIABLE
        self._pricing_data: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Step 1: Choose Pricing Mode."""
        if user_input is not None:
            self._pricing_mode = user_input[CONF_PRICING_MODE]
            if self._pricing_mode == PRICING_MODE_VARIABLE:
                return await self.async_step_variable_pricing()
            return await self.async_step_peak_offpeak_pricing()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRICING_MODE, default=PRICING_MODE_VARIABLE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=PRICING_MODE_VARIABLE,
                                    label="Prix variable via une entité",
                                ),
                                selector.SelectOptionDict(
                                    value=PRICING_MODE_PEAK_OFFPEAK,
                                    label="Heures Creuses / Heures Pleines",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_variable_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2a: Configure Variable Pricing entity."""
        if user_input is not None:
            self._pricing_data = {
                CONF_PRICING_MODE: PRICING_MODE_VARIABLE,
                CONF_VARIABLE_PRICE_ENTITY: user_input[CONF_VARIABLE_PRICE_ENTITY],
            }
            return await self.async_step_device_add()

        return self.async_show_form(
            step_id="variable_pricing",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VARIABLE_PRICE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
        )

    async def async_step_peak_offpeak_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2b: Configure Peak / Off-peak entities."""
        if user_input is not None:
            self._pricing_data = {
                CONF_PRICING_MODE: PRICING_MODE_PEAK_OFFPEAK,
                CONF_OFFPEAK_PRICE_ENTITY: user_input[CONF_OFFPEAK_PRICE_ENTITY],
                CONF_PEAK_PRICE_ENTITY: user_input[CONF_PEAK_PRICE_ENTITY],
                CONF_TARIFF_MODE_ENTITY: user_input[CONF_TARIFF_MODE_ENTITY],
            }
            return await self.async_step_peak_offpeak_states()

        return self.async_show_form(
            step_id="peak_offpeak_pricing",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OFFPEAK_PRICE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_PEAK_PRICE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_TARIFF_MODE_ENTITY): selector.EntitySelector(),
                }
            ),
        )

    async def async_step_peak_offpeak_states(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2c: Dynamically detect and map mode states to offpeak and peak."""
        tariff_entity = self._pricing_data.get(CONF_TARIFF_MODE_ENTITY, "")
        detected_states = detect_entity_states(self.hass, tariff_entity)

        if user_input is not None:
            self._pricing_data[CONF_OFFPEAK_STATE] = user_input[CONF_OFFPEAK_STATE]
            self._pricing_data[CONF_PEAK_STATE] = user_input[CONF_PEAK_STATE]
            return await self.async_step_device_add()

        # Build options dynamically from detected states
        options = [
            selector.SelectOptionDict(value=s, label=s) for s in detected_states
        ]

        default_offpeak = detected_states[0] if detected_states else "off_peak"
        default_peak = detected_states[1] if len(detected_states) > 1 else (detected_states[0] if detected_states else "peak")

        return self.async_show_form(
            step_id="peak_offpeak_states",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_OFFPEAK_STATE, default=default_offpeak): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_PEAK_STATE, default=default_peak): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_device_add(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Add first device to monitor."""
        if user_input is not None:
            device_entry = {
                CONF_DEVICE_ID: str(uuid.uuid4())[:8],
                CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                CONF_SOURCE_ENTITY: user_input[CONF_SOURCE_ENTITY],
                CONF_SOURCE_TYPE: user_input[CONF_SOURCE_TYPE],
                CONF_SOURCE_UNIT: user_input.get(CONF_SOURCE_UNIT),
            }
            self._devices.append(device_entry)

            if user_input.get("add_another"):
                return await self.async_step_device_add()

            # Create entry with pricing and device list
            return self.async_create_entry(
                title="PowerCost",
                data={
                    **self._pricing_data,
                    CONF_DEVICES: self._devices,
                },
            )

        return self.async_show_form(
            step_id="device_add",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_NAME, default="Mon Appareil"): selector.TextSelector(),
                    vol.Required(CONF_SOURCE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_SOURCE_TYPE, default=SOURCE_TYPE_POWER
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=SOURCE_TYPE_POWER, label="Puissance instantanée"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_DAILY, label="Énergie quotidienne"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_MONTHLY, label="Énergie mensuelle"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_YEARLY, label="Énergie annuelle"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_TOTAL, label="Énergie totale"),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_SOURCE_UNIT): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="W", label="W"),
                                selector.SelectOptionDict(value="kW", label="kW"),
                                selector.SelectOptionDict(value="Wh", label="Wh"),
                                selector.SelectOptionDict(value="kWh", label="kWh"),
                                selector.SelectOptionDict(value="MWh", label="MWh"),
                            ],
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional("add_another", default=False): selector.BooleanSelector(),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow handler."""
        return ElectricityCostOptionsFlow(config_entry)


class ElectricityCostOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for PowerCost."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        self._custom_config_entry = config_entry
        self._selected_device_id: str | None = None

    @property
    def _entry(self) -> config_entries.ConfigEntry:
        """Return config entry safely without throwing AttributeError."""
        try:
            return self.config_entry
        except (AttributeError, KeyError):
            return self._custom_config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Display options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_device", "manage_device", "pricing"],
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add a new device to monitor."""
        if user_input is not None:
            new_device = {
                CONF_DEVICE_ID: str(uuid.uuid4())[:8],
                CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                CONF_SOURCE_ENTITY: user_input[CONF_SOURCE_ENTITY],
                CONF_SOURCE_TYPE: user_input[CONF_SOURCE_TYPE],
                CONF_SOURCE_UNIT: user_input.get(CONF_SOURCE_UNIT),
            }
            current_devices = list(self._entry.data.get(CONF_DEVICES, []))
            current_devices.append(new_device)

            new_data = dict(self._entry.data)
            new_data[CONF_DEVICES] = current_devices

            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="add_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_NAME): selector.TextSelector(),
                    vol.Required(CONF_SOURCE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_SOURCE_TYPE, default=SOURCE_TYPE_POWER
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=SOURCE_TYPE_POWER, label="Puissance instantanée"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_DAILY, label="Énergie quotidienne"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_MONTHLY, label="Énergie mensuelle"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_YEARLY, label="Énergie annuelle"),
                                selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_TOTAL, label="Énergie totale"),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_SOURCE_UNIT): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="W", label="W"),
                                selector.SelectOptionDict(value="kW", label="kW"),
                                selector.SelectOptionDict(value="Wh", label="Wh"),
                                selector.SelectOptionDict(value="kWh", label="kWh"),
                                selector.SelectOptionDict(value="MWh", label="MWh"),
                            ],
                            custom_value=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_manage_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select a device to edit or remove."""
        devices = self._entry.data.get(CONF_DEVICES, [])
        if not devices:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            self._selected_device_id = user_input[CONF_DEVICE_ID]
            action = user_input["action"]
            if action == "delete":
                return await self.async_step_delete_device()
            return await self.async_step_edit_device()

        device_options = [
            selector.SelectOptionDict(value=d[CONF_DEVICE_ID], label=d[CONF_DEVICE_NAME])
            for d in devices
        ]

        return self.async_show_form(
            step_id="manage_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID, default=devices[0][CONF_DEVICE_ID]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=device_options)
                    ),
                    vol.Required("action", default="edit"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="edit", label="Modifier"),
                                selector.SelectOptionDict(value="delete", label="Supprimer"),
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_delete_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm and delete a single device from the entry."""
        devices = self._entry.data.get(CONF_DEVICES, [])
        device = next((d for d in devices if d[CONF_DEVICE_ID] == self._selected_device_id), None)
        if not device:
            return self.async_abort(reason="device_not_found")

        if user_input is not None:
            dev_id_to_delete = self._selected_device_id
            new_devices = [d for d in devices if d[CONF_DEVICE_ID] != dev_id_to_delete]
            new_data = dict(self._entry.data)
            new_data[CONF_DEVICES] = new_devices

            # Clean up device registry & entity registry
            try:
                from homeassistant.helpers import device_registry as dr

                dev_reg = dr.async_get(self.hass)
                dev_entry = dev_reg.async_get_device(
                    identifiers={(DOMAIN, f"{self._entry.entry_id}_{dev_id_to_delete}")}
                )
                if dev_entry:
                    dev_reg.async_remove_device(dev_entry.id)
            except Exception as err:
                _LOGGER.debug("Could not remove device registry entry: %s", err)

            # Clean up storage for deleted device
            if DOMAIN in self.hass.data and self._entry.entry_id in self.hass.data[DOMAIN]:
                coordinator = self.hass.data[DOMAIN][self._entry.entry_id]["coordinator"]
                coordinator.statistics.pop(dev_id_to_delete, None)
                coordinator.devices.pop(dev_id_to_delete, None)
                await coordinator.async_save_data()

            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="delete_device",
            description_placeholders={"device_name": device[CONF_DEVICE_NAME]},
            data_schema=vol.Schema({}),
        )

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit an existing device."""
        devices = self._entry.data.get(CONF_DEVICES, [])
        device = next((d for d in devices if d[CONF_DEVICE_ID] == self._selected_device_id), None)
        if not device:
            return self.async_abort(reason="device_not_found")

        if user_input is not None:
            updated_device = {
                CONF_DEVICE_ID: self._selected_device_id,
                CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                CONF_SOURCE_ENTITY: user_input[CONF_SOURCE_ENTITY],
                CONF_SOURCE_TYPE: user_input[CONF_SOURCE_TYPE],
                CONF_SOURCE_UNIT: user_input.get(CONF_SOURCE_UNIT),
            }
            new_devices = [
                updated_device if d[CONF_DEVICE_ID] == self._selected_device_id else d
                for d in devices
            ]
            new_data = dict(self._entry.data)
            new_data[CONF_DEVICES] = new_devices
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data={})

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_DEVICE_NAME, default=device[CONF_DEVICE_NAME]): selector.TextSelector(),
            vol.Required(CONF_SOURCE_ENTITY, default=device[CONF_SOURCE_ENTITY]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_SOURCE_TYPE, default=device[CONF_SOURCE_TYPE]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=SOURCE_TYPE_POWER, label="Puissance instantanée"),
                        selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_DAILY, label="Énergie quotidienne"),
                        selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_MONTHLY, label="Énergie mensuelle"),
                        selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_YEARLY, label="Énergie annuelle"),
                        selector.SelectOptionDict(value=SOURCE_TYPE_ENERGY_TOTAL, label="Énergie totale"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        unit_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="W", label="W"),
                    selector.SelectOptionDict(value="kW", label="kW"),
                    selector.SelectOptionDict(value="Wh", label="Wh"),
                    selector.SelectOptionDict(value="kWh", label="kWh"),
                    selector.SelectOptionDict(value="MWh", label="MWh"),
                ],
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        if device.get(CONF_SOURCE_UNIT):
            schema_dict[vol.Optional(CONF_SOURCE_UNIT, default=device[CONF_SOURCE_UNIT])] = unit_selector
        else:
            schema_dict[vol.Optional(CONF_SOURCE_UNIT)] = unit_selector

        return self.async_show_form(
            step_id="edit_device",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_pricing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Reconfigure global pricing settings."""
        entry_data = self._entry.data
        current_mode = entry_data.get(CONF_PRICING_MODE, PRICING_MODE_VARIABLE)

        if user_input is not None:
            new_data = dict(self._entry.data)
            new_data.update(user_input)
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            await self.hass.config_entries.async_reload(self._entry.entry_id)
            return self.async_create_entry(title="", data={})

        if current_mode == PRICING_MODE_VARIABLE:
            schema_dict: dict[Any, Any] = {}
            var_price = entry_data.get(CONF_VARIABLE_PRICE_ENTITY)
            if var_price:
                schema_dict[vol.Required(CONF_VARIABLE_PRICE_ENTITY, default=var_price)] = (
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
                )
            else:
                schema_dict[vol.Required(CONF_VARIABLE_PRICE_ENTITY)] = (
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
                )
            return self.async_show_form(
                step_id="pricing",
                data_schema=vol.Schema(schema_dict),
            )

        fields = [
            (CONF_OFFPEAK_PRICE_ENTITY, selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))),
            (CONF_PEAK_PRICE_ENTITY, selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))),
            (CONF_TARIFF_MODE_ENTITY, selector.EntitySelector()),
            (CONF_OFFPEAK_STATE, selector.TextSelector()),
            (CONF_PEAK_STATE, selector.TextSelector()),
        ]
        schema_dict = {}
        for key, sel in fields:
            val = entry_data.get(key)
            if val is not None:
                schema_dict[vol.Required(key, default=val)] = sel
            else:
                schema_dict[vol.Required(key)] = sel

        return self.async_show_form(
            step_id="pricing",
            data_schema=vol.Schema(schema_dict),
        )

