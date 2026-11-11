"""Moj Elektro integration."""

from datetime import datetime
import json
import logging
import re

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import statistics
from .const import (
    BACKFILL_DAYS,
    CONF_METER_ID,
    DOMAIN,
    SETUP_TAG_15_ARRAY,
    SETUP_TAG_ARRAY,
    SETUP_TAG_BLOCKS_ARRAY,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

SERVICE_REBUILD_STATISTICS = "rebuild_statistics"
REBUILD_SCHEMA = vol.Schema(
    {
        vol.Optional("days", default=BACKFILL_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=366)
        )
    }
)


def _expected_sensor_names() -> list[str]:
    """Return all sensor keys created by this integration."""
    names: list[str] = []

    for item in json.loads(SETUP_TAG_15_ARRAY):
        names.append(item["sensor"])

    for item in json.loads(SETUP_TAG_ARRAY):
        names.append(item["sensor"])
        names.append(item["sensor"].replace("daily_", "monthly_", 1))

    for item in json.loads(SETUP_TAG_BLOCKS_ARRAY):
        names.append(item["sensor"])

    names.extend(f"casovni_blok_{block_number}" for block_number in range(1, 6))
    return names


def _migrate_legacy_sensor_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Migrate legacy suffixed unique IDs without changing entity IDs."""
    meter_id = entry.data[CONF_METER_ID]
    registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(registry, entry.entry_id)

    for measurement_name in _expected_sensor_names():
        desired_unique_id = (
            f"{meter_id}-sensor.{DOMAIN}_{measurement_name.lower()}"
        )
        legacy_pattern = re.compile(
            rf"^{re.escape(desired_unique_id)}_(?P<suffix>(?:[2-9]|[1-9]\d+))$"
        )

        for entity_entry in registry_entries:
            if entity_entry.domain != Platform.SENSOR:
                continue
            if not legacy_pattern.fullmatch(entity_entry.unique_id):
                continue

            existing_entity_id = registry.async_get_entity_id(
                entity_entry.domain,
                entity_entry.platform,
                desired_unique_id,
            )
            if existing_entity_id not in (None, entity_entry.entity_id):
                continue

            registry.async_update_entity(
                entity_entry.entity_id,
                new_unique_id=desired_unique_id,
            )
            break


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""

    async def async_rebuild_statistics(call: ServiceCall) -> None:
        """Fetch recent 15-minute data again and rewrite the statistics."""
        apis = hass.data.get(DOMAIN, {}).values()
        if not apis:
            raise HomeAssistantError("No Moj Elektro meter is loaded")
        end = datetime.now().date()
        for api in apis:
            try:
                await statistics.async_rebuild(hass, api, end, call.data["days"])
            except Exception as err:
                raise HomeAssistantError(
                    f"Rebuilding statistics for {api.meter_id} failed: {err}"
                ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_STATISTICS,
        async_rebuild_statistics,
        schema=REBUILD_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Moj Elektro from a config entry."""
    if entry.unique_id is None:
        meter_id = entry.data[CONF_METER_ID]
        duplicate_entries = [
            existing_entry
            for existing_entry in hass.config_entries.async_entries(DOMAIN)
            if existing_entry.entry_id != entry.entry_id
            and existing_entry.data.get(CONF_METER_ID) == meter_id
        ]
        if not duplicate_entries:
            hass.config_entries.async_update_entry(entry, unique_id=meter_id)

    _migrate_legacy_sensor_unique_ids(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Moj Elektro config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
