"""Config flow for Tado integration."""

from __future__ import annotations

import logging
from typing import Any

import PyTado
from PyTado.interface import Tado
import requests.exceptions
import voluptuous as vol

from homeassistant.components import zeroconf
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_FALLBACK,
    CONF_HOME_ID,
    CONST_OVERLAY_TADO_DEFAULT,
    CONST_OVERLAY_TADO_OPTIONS,
    DOMAIN,
    UNIQUE_ID,
)

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """

    try:
        tado = await hass.async_add_executor_job(
            Tado, data[CONF_USERNAME], data[CONF_PASSWORD]
        )
        tado_me = await hass.async_add_executor_job(tado.getMe)
    except KeyError as ex:
        raise InvalidAuth from ex
    except RuntimeError as ex:
        raise CannotConnect from ex
    except requests.exceptions.HTTPError as ex:
        if ex.response.status_code > 400 and ex.response.status_code < 500:
            raise InvalidAuth from ex
        raise CannotConnect from ex

    if "homes" not in tado_me or len(tado_me["homes"]) == 0:
        raise NoHomes

    home = tado_me["homes"][0]
    unique_id = str(home["id"])
    name = home["name"]

    return {"title": name, UNIQUE_ID: unique_id}


class TadoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tado."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                validated = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NoHomes:
                errors["base"] = "no_homes"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

            if "base" not in errors:
                await self.async_set_unique_id(validated[UNIQUE_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=validated["title"], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_homekit(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle HomeKit discovery."""
        self._async_abort_entries_match()
        properties = {
            key.lower(): value for (key, value) in discovery_info.properties.items()
        }
        await self.async_set_unique_id(properties[zeroconf.ATTR_PROPERTIES_ID])
        self._abort_if_unique_id_configured()
        return await self.async_step_user()

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import a config entry from configuration.yaml."""
        _LOGGER.debug("Importing Tado from configuration.yaml")
        username = import_config[CONF_USERNAME]
        password = import_config[CONF_PASSWORD]
        imported_home_id = import_config[CONF_HOME_ID]

        self._async_abort_entries_match(
            {
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_HOME_ID: imported_home_id,
            }
        )

        try:
            validate_result = await validate_input(
                self.hass,
                {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                },
            )
        except HomeAssistantError:
            return self.async_abort(reason="import_failed")
        except PyTado.exceptions.TadoWrongCredentialsException:
            return self.async_abort(reason="import_failed_invalid_auth")

        home_id = validate_result[UNIQUE_ID]
        await self.async_set_unique_id(home_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=import_config[CONF_USERNAME],
            data={
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_HOME_ID: home_id,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(OptionsFlow):
    """Handle an option flow for Tado."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FALLBACK,
                    default=self.config_entry.options.get(
                        CONF_FALLBACK, CONST_OVERLAY_TADO_DEFAULT
                    ),
                ): vol.In(CONST_OVERLAY_TADO_OPTIONS),
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class NoHomes(HomeAssistantError):
    """Error to indicate the account has no homes."""
