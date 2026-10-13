"""An HA select entity for each watch's desired tile device. No watch wakeups."""
from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN
from . import save


async def async_setup_entry(hass, entry, async_add_entities):
    known = {}

    @callback
    def update():
        manager = hass.data[DOMAIN]["manager"]
        added = []
        for watch_id in manager.watches:
            if watch_id not in known:
                known[watch_id] = WatchSelection(hass, watch_id)
                added.append(known[watch_id])
        if added:
            async_add_entities(added)
        for watch_id, entity in known.items():
            if entity.hass is not None and entity.entity_id is not None:
                entity.async_write_ha_state()

    entry.async_on_unload(async_dispatcher_connect(hass, f"{DOMAIN}_updated", update))
    update()


class WatchSelection(SelectEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Tile device"
    _attr_icon = "mdi:watch"

    def __init__(self, hass, watch_id):
        self._runtime_hass, self.watch_id = hass, watch_id
        self._attr_unique_id = watch_id
        watch = self.watch
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, watch_id)}, name=watch["name"], manufacturer="Fan Control", model="Wear OS watch")

    @property
    def watch(self):
        runtime = self._runtime_hass.data.get(DOMAIN)
        return runtime["manager"].watches.get(self.watch_id) if runtime else None

    @property
    def available(self):
        return self.watch is not None

    @property
    def options(self):
        return [device["entity"] for device in self.watch["devices"]] if self.watch else []

    @property
    def current_option(self):
        return self.watch["entity"] if self.watch else None

    async def async_select_option(self, option):
        runtime = self._runtime_hass.data[DOMAIN]
        async with runtime["lock"]:
            if option not in self.options:
                raise HomeAssistantError("Device not approved for this watch")
            self.watch["entity"] = option
            self.watch["revision"] += 1
            await save(runtime)
            async_dispatcher_send(self._runtime_hass, f"{DOMAIN}_updated")
