"""UI setup for the CN30 water heater."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_STALE_SECONDS,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_STALE_SECONDS,
    DOMAIN,
    MAX_STALE_SECONDS,
    MIN_STALE_SECONDS,
)
from .gateway import GatewayError, probe_frame

_PORT = NumberSelector(
    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX, step=1)
)
_STALE = NumberSelector(
    NumberSelectorConfig(
        min=MIN_STALE_SECONDS,
        max=MAX_STALE_SECONDS,
        mode=NumberSelectorMode.BOX,
        step=1,
        unit_of_measurement="s",
    )
)


class MideaCn30ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the EW-11, then require one real CN30 frame before saving."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            port = int(user_input[CONF_PORT])
            name = str(user_input[CONF_NAME]).strip() or DEFAULT_NAME
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            errors = await _probe_errors(host, port)
            if not errors:
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: name,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            port = int(user_input[CONF_PORT])
            name = str(user_input[CONF_NAME]).strip() or DEFAULT_NAME
            await self.async_set_unique_id(host)
            for existing in self._async_current_entries():
                if existing.unique_id == host and existing.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")
            errors = await _probe_errors(host, port)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=host,
                    title=name,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: name,
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(
                host=entry.data[CONF_HOST],
                port=entry.data[CONF_PORT],
                name=entry.title,
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MideaCn30OptionsFlow()


class MideaCn30OptionsFlow(OptionsFlow):
    """How long a missing broadcast stays available."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_STALE_SECONDS: int(user_input[CONF_STALE_SECONDS])}
            )
        current = int(
            self.config_entry.options.get(CONF_STALE_SECONDS, DEFAULT_STALE_SECONDS)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STALE_SECONDS, default=current): _STALE,
                }
            ),
        )


def _connection_schema(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    name: str = DEFAULT_NAME,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=name): TextSelector(),
            vol.Required(CONF_HOST, default=host): TextSelector(),
            vol.Required(CONF_PORT, default=port): _PORT,
        }
    )


async def _probe_errors(host: str, port: int) -> dict[str, str]:
    try:
        await probe_frame(host, port)
    except GatewayError as err:
        return {"base": err.code}
    return {}
