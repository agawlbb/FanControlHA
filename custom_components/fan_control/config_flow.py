"""Native HA setup forms: pairing, device selection, addresses, and revocation."""
try:
    import probatio as vol
except ImportError:
    import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.network import get_url, NoURLAvailableError

from .const import DOMAIN, SUPPORTED_DOMAINS
from .protocol import address, devices
from .panels import validate_panel
from . import save
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send


class FanControlFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Fan Control", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FanControlOptions()


class FanControlOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        # HA protects native options endpoints with require_admin. Options context
        # does not carry the browser's user ID, so select an active admin owner explicitly.
        return self.async_show_menu(step_id="init", menu_options=["pair", "configure", "panels", "revoke"])

    async def async_step_pair(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self.code = user_input["code"].replace(" ", "")
                session = self.hass.data[DOMAIN]["manager"].lookup(self.code)
                self.fingerprint = session["fingerprint"]
                return await self.async_step_confirm()
            except ValueError:
                errors["base"] = "invalid_pairing"
        return self.async_show_form(step_id="pair", errors=errors, data_schema=vol.Schema({vol.Required("code"): str}))

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            if user_input.get("matches"):
                self.watch_id = None
                return await self.async_step_devices()
        return self.async_show_form(step_id="confirm", description_placeholders={"fingerprint": self.fingerprint}, data_schema=vol.Schema({vol.Required("matches", default=False): bool}))

    def watch_selector(self):
        watches = self.hass.data[DOMAIN]["manager"].watches
        return selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": key, "label": f'{watch["name"]} ({key[:8]})'} for key, watch in watches.items()]))

    async def async_step_configure(self, user_input=None):
        if not self.hass.data[DOMAIN]["manager"].watches:
            return self.async_abort(reason="no_watches")
        if user_input is not None:
            self.watch_id = user_input["watch_id"]
            return await self.async_step_devices()
        return self.async_show_form(step_id="configure", data_schema=vol.Schema({vol.Required("watch_id"): self.watch_selector()}))

    async def async_step_devices(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        existing = runtime["manager"].watches.get(getattr(self, "watch_id", None), {})
        admins = [user for user in await self.hass.auth.async_get_users() if user.is_admin and user.is_active]
        if not admins:
            return self.async_abort(reason="admin_required")
        errors = {}
        if user_input is not None:
            try:
                selected = devices(user_input["entities"])
                if len(selected) + len(existing.get("panels", [])) > 16:
                    raise ValueError("Too many watch pages")
                if user_input["owner_id"] not in [user.id for user in admins]:
                    raise ValueError("Invalid owner")
                if user_input["active_entity"] not in selected or any(self.hass.states.get(entity) is None for entity in selected):
                    raise ValueError("Invalid selection")
                local = address(user_input["local_url"])
                remote = address(user_input.get("remote_url", ""), external=True) if user_input.get("remote_url", "").strip() else ""
                config = {"name": user_input["name"].strip()[:80] or "Watch", "url": local, "remote_url": remote, "entity": user_input["active_entity"], "devices": [{"entity": entity, "title": (str(self.hass.states.get(entity).attributes.get("friendly_name", entity)).strip() or entity)[:80]} for entity in selected], "owner_id": user_input["owner_id"]}
                async with runtime["lock"]:
                    if self.watch_id is None:
                        self.watch_id = runtime["manager"].approve(self.code, config)
                    else:
                        watch = runtime["manager"].watches[self.watch_id]
                        watch.update(config)
                        watch["revision"] += 1
                    await save(runtime)
                    async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
                return self.async_create_entry(title="", data={})
            except (ValueError, KeyError):
                errors["base"] = "invalid_configuration"
        def instance_url(external):
            try:
                return get_url(self.hass, allow_internal=not external, allow_external=external, allow_cloud=external, require_ssl=external, prefer_external=external)
            except NoURLAvailableError:
                return ""
        schema = vol.Schema({
            vol.Required("name", default=existing.get("name", "Watch")): str,
            vol.Required("owner_id", default=existing.get("owner_id", admins[0].id)): selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": user.id, "label": user.name or "Administrator"} for user in admins])),
            vol.Required("entities", default=[item["entity"] for item in existing.get("devices", [])]): selector.EntitySelector(selector.EntitySelectorConfig(domain=list(SUPPORTED_DOMAINS), multiple=True)),
            vol.Required("active_entity", **({"default": existing["entity"]} if existing else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain=list(SUPPORTED_DOMAINS))),
            vol.Required("local_url", default=existing.get("url", instance_url(False))): str,
            vol.Optional("remote_url", default=existing.get("remote_url", instance_url(True))): str,
        })
        return self.async_show_form(step_id="devices", errors=errors, data_schema=schema)

    async def async_step_revoke(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        if not runtime["manager"].watches:
            return self.async_abort(reason="no_watches")
        if user_input is not None:
            async with runtime["lock"]:
                runtime["manager"].watches.pop(user_input["watch_id"], None)
                await save(runtime)
                async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="revoke", data_schema=vol.Schema({vol.Required("watch_id"): self.watch_selector()}))


    async def async_step_panels(self, user_input=None):
        if not self.hass.data[DOMAIN]["manager"].watches:
            return self.async_abort(reason="no_watches")
        if user_input is not None:
            self.watch_id = user_input["watch_id"]
            return await self.async_step_panel_menu()
        return self.async_show_form(step_id="panels", data_schema=vol.Schema({vol.Required("watch_id"): self.watch_selector()}))

    async def async_step_panel_menu(self, user_input=None):
        return self.async_show_menu(step_id="panel_menu", menu_options=["add_panel", "add_exercise", "edit_panel", "remove_panel", "activate_panel"])

    async def async_step_add_panel(self, user_input=None):
        self.editing_panel = None
        return await self.async_step_panel()

    def panel_selector(self, include_devices=False):
        watch = self.hass.data[DOMAIN]["manager"].watches[self.watch_id]
        options = [{"value": item["id"], "label": item["name"]} for item in watch.get("panels", [])]
        if include_devices:
            active = next((item for item in watch["devices"] if item["entity"] == watch["entity"]), watch["devices"][0])
            options.insert(0, {"value": "devices", "label": active["title"]})
        return selector.SelectSelector(selector.SelectSelectorConfig(options=options))

    async def async_step_edit_panel(self, user_input=None):
        watch = self.hass.data[DOMAIN]["manager"].watches[self.watch_id]
        if not watch.get("panels"):
            return self.async_abort(reason="no_panels")
        if user_input is not None:
            self.editing_panel = user_input["panel_id"]
            selected = next(item for item in watch["panels"] if item["id"] == self.editing_panel)
            return await (self.async_step_exercise() if selected.get("type") == "exercise" else self.async_step_panel())
        return self.async_show_form(step_id="edit_panel", data_schema=vol.Schema({vol.Required("panel_id"): self.panel_selector()}))

    async def async_step_panel(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        watch = runtime["manager"].watches[self.watch_id]
        existing = next((item for item in watch.get("panels", []) if item["id"] == self.editing_panel), {})
        errors = {}
        if user_input is not None:
            try:
                panel = validate_panel(user_input)
                if self.editing_panel and panel["id"] != self.editing_panel:
                    raise ValueError("Panel ID cannot change while editing")
                if any(self.hass.states.get(panel[role]) is None for role in ("battery_entity", "climate_entity", "script_entity")):
                    raise ValueError("Entity not found")
                if panel.get("wake_entity") and any(self.hass.states.get(panel[role]) is None for role in ("wake_entity", "awake_entity")):
                    raise ValueError("Vehicle entity not found")
                battery = self.hass.states.get(panel["battery_entity"])
                if battery.attributes.get("unit_of_measurement") != "%":
                    raise ValueError("Select a percentage sensor")
                async with runtime["lock"]:
                    current = runtime["manager"].watches[self.watch_id]
                    panels = list(current.get("panels", []))
                    if not self.editing_panel and any(item["id"] == panel["id"] for item in panels):
                        raise ValueError("Duplicate panel ID")
                    panels = [item for item in panels if item["id"] != self.editing_panel]
                    panels.append(panel)
                    if len(panels) > 8 or len(panels) + len(current["devices"]) > 16:
                        raise ValueError("Too many watch pages")
                    current["panels"] = panels; current["revision"] += 1
                    await save(runtime); async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
                return self.async_create_entry(title="", data={})
            except (ValueError, KeyError):
                errors["base"] = "invalid_panel"
        return self.async_show_form(step_id="panel", errors=errors, data_schema=vol.Schema({
            vol.Required("id", default=existing.get("id", "work_morning")): str,
            vol.Required("name", default=existing.get("name", "Work Morning")): str,
            vol.Required("battery_entity", **({"default": existing["battery_entity"]} if existing else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required("climate_entity", **({"default": existing["climate_entity"]} if existing else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain=["climate", "switch", "input_boolean"])),
            vol.Required("script_entity", **({"default": existing["script_entity"]} if existing else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain="script")),
            vol.Optional("wake_entity", **({"default": existing["wake_entity"]} if existing.get("wake_entity") else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain="button")),
            vol.Optional("awake_entity", **({"default": existing["awake_entity"]} if existing.get("awake_entity") else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            vol.Required("climate_label", default=existing.get("climate_label", "Climate")): str,
            vol.Required("script_label", default=existing.get("script_label", "Leave for work")): str,
        }))

    async def async_step_remove_panel(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        if not runtime["manager"].watches[self.watch_id].get("panels"):
            return self.async_abort(reason="no_panels")
        if user_input is not None:
            async with runtime["lock"]:
                watch = runtime["manager"].watches[self.watch_id]
                watch["panels"] = [item for item in watch.get("panels", []) if item["id"] != user_input["panel_id"]]
                if watch.get("active_panel") == user_input["panel_id"]:
                    watch["active_panel"] = ""
                watch["revision"] += 1
                await save(runtime); async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="remove_panel", data_schema=vol.Schema({vol.Required("panel_id"): self.panel_selector()}))

    async def async_step_activate_panel(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        if user_input is not None:
            async with runtime["lock"]:
                watch = runtime["manager"].watches[self.watch_id]
                if user_input["panel_id"] not in ["devices"] + [item["id"] for item in watch.get("panels", [])]:
                    return self.async_abort(reason="no_panels")
                watch["active_panel"] = "" if user_input["panel_id"] == "devices" else user_input["panel_id"]
                watch["revision"] += 1
                await save(runtime); async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
            return self.async_create_entry(title="", data={})
        watch = runtime["manager"].watches[self.watch_id]
        return self.async_show_form(step_id="activate_panel", data_schema=vol.Schema({vol.Required("panel_id", default=watch.get("active_panel") or "devices"): self.panel_selector(True)}))


    async def async_step_add_exercise(self, user_input=None):
        self.editing_panel = None
        return await self.async_step_exercise()

    async def async_step_exercise(self, user_input=None):
        runtime = self.hass.data[DOMAIN]
        watch = runtime["manager"].watches[self.watch_id]
        existing = next((item for item in watch.get("panels", []) if item["id"] == self.editing_panel), {})
        errors = {}
        if user_input is not None:
            try:
                panel = validate_panel({**user_input, "type": "exercise"})
                if self.editing_panel and panel["id"] != self.editing_panel:
                    raise ValueError("Keep panel ID")
                if any(self.hass.states.get(panel[role]) is None for role in ("lock_entity", "climate_entity", "wake_entity", "awake_entity")):
                    raise ValueError("Entity not found")
                async with runtime["lock"]:
                    current = runtime["manager"].watches[self.watch_id]
                    panels = list(current.get("panels", []))
                    if not self.editing_panel and any(item["id"] == panel["id"] for item in panels):
                        raise ValueError("Duplicate ID")
                    panels = [item for item in panels if item["id"] != self.editing_panel] + [panel]
                    if len(panels) > 8 or len(panels) + len(current["devices"]) > 16:
                        raise ValueError("Too many pages")
                    current["panels"] = panels
                    current["revision"] += 1
                    await save(runtime)
                    async_dispatcher_send(self.hass, f"{DOMAIN}_updated")
                return self.async_create_entry(title="", data={})
            except (ValueError, KeyError):
                errors["base"] = "invalid_panel"
        fields = {vol.Required("id", default=existing.get("id", "exercise")): str,
                  vol.Required("name", default=existing.get("name", "Exercise")): str}
        for role, domain in (("lock_entity", "lock"), ("climate_entity", "climate"), ("wake_entity", "button"), ("awake_entity", "binary_sensor")):
            fields[vol.Required(role, **({"default": existing[role]} if role in existing else {}))] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
        return self.async_show_form(step_id="exercise", errors=errors, data_schema=vol.Schema(fields))
