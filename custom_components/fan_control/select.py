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
                known[watch_id + "_panel"] = PanelSelection(hass, watch_id)
                added.append(known[watch_id + "_panel"])
        if added:
            async_add_entities(added)
        for watch_id, entity in known.items():
            if entity.hass is not None and entity.entity_id is not None:
                from homeassistant.helpers import device_registry as dr
                registry = dr.async_get(hass)
                device = registry.async_get_device(identifiers={(DOMAIN, entity.watch_id)})
                if device and entity.watch:
                    registry.async_update_device(device.id, name=entity.watch["name"], model=entity.device_info["model"])
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

    @property
    def device_info(self):
        watch = self.watch or {}
        return DeviceInfo(identifiers={(DOMAIN, self.watch_id)}, name=watch.get("name", "Watch"), manufacturer="Fan Control", model="Apple Watch" if watch.get("platform") == "watch_os" else "Wear OS watch")

    @property
    def watch(self):
        runtime = self._runtime_hass.data.get(DOMAIN)
        return runtime["manager"].watches.get(self.watch_id) if runtime else None

    @property
    def available(self):
        return self.watch is not None and bool(self.options)

    @property
    def options(self):
        return [device["entity"] for device in self.watch["devices"]] if self.watch else []

    @property
    def current_option(self):
        return self.watch["entity"] if self.watch and self.watch["entity"] in self.options else None

    async def async_select_option(self, option):
        runtime = self._runtime_hass.data[DOMAIN]
        async with runtime["lock"]:
            if option not in self.options:
                raise HomeAssistantError("Device not approved for this watch")
            self.watch["entity"] = option
            self.watch["active_panel"] = ""
            self.watch["revision"] += 1
            await save(runtime)
            async_dispatcher_send(self._runtime_hass, f"{DOMAIN}_updated")


class PanelSelection(WatchSelection):
    _attr_name = "Tile panel"
    def __init__(self, hass, watch_id):
        super().__init__(hass, watch_id)
        self._attr_unique_id = watch_id + "_panel"

    @property
    def options(self):
        return (["devices"] if self.watch.get("devices") else []) + [panel["id"] for panel in self.watch.get("panels", [])] if self.watch else []

    @property
    def current_option(self):
        value = (self.watch.get("active_panel") or "devices") if self.watch else None
        return value if value in self.options else None

    async def async_select_option(self, option):
        runtime = self._runtime_hass.data[DOMAIN]
        async with runtime["lock"]:
            if option not in self.options:
                raise HomeAssistantError("Panel not configured for this watch")
            self.watch["active_panel"] = "" if option == "devices" else option
            self.watch["revision"] += 1
            await save(runtime)
            async_dispatcher_send(self._runtime_hass, f"{DOMAIN}_updated")
