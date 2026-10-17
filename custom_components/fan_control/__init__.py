"""Pair watches locally and serve only explicitly selected device controls."""
import asyncio
from collections import deque
import time

from aiohttp import web
try:
    import probatio as vol
except ImportError:  # Older HA installations use the compatible predecessor.
    import voluptuous as vol
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN
from .panels import configuration, find_panel, panel_state, panel_command
from .exercise import execute_exercise, retry_stop, cancel_stop, accept_stop
from .manager import Manager
from .protocol import devices, seal_response


async def async_setup(hass, config):
    if not hass.data.get(f"{DOMAIN}_views"):
        for action in ("create", "poll", "acknowledge"):
            hass.http.register_view(PairView(hass, action))
        hass.http.register_view(WatchView(hass))
        hass.data[f"{DOMAIN}_views"] = True
    return True


async def async_setup_entry(hass, entry):
    store = Store(hass, 1, DOMAIN)
    saved = await store.async_load() or {}
    manager = Manager(saved.get("watches", {}), nonces=saved.get("nonces", {}))
    runtime = {"manager": manager, "store": store, "lock": asyncio.Lock(), "attempts": {}, "entry": entry}
    hass.data[DOMAIN] = runtime

    async def set_active(call):
        async with runtime["lock"]:
            watch = manager.watches.get(call.data["watch_id"])
            if not watch or call.data["entity_id"] not in [item["entity"] for item in watch["devices"]]:
                raise HomeAssistantError("Select a device already approved for this watch")
            watch["entity"] = call.data["entity_id"]
            watch["active_panel"] = ""
            watch["revision"] += 1
            await save(runtime)
            async_dispatcher_send(hass, f"{DOMAIN}_updated")

    hass.services.async_register(DOMAIN, "set_active_device", set_active, schema=vol.Schema({vol.Required("watch_id"): str, vol.Required("entity_id"): str}))
    async def set_panel(call):
        async with runtime["lock"]:
            watch = manager.watches.get(call.data["watch_id"])
            if watch is None:
                raise HomeAssistantError("Watch not paired")
            panel_id = call.data["panel_id"]
            if panel_id != "devices":
                try:
                    find_panel(watch, panel_id)
                except ValueError as error:
                    raise HomeAssistantError("Select a configured panel") from error
            watch["active_panel"] = "" if panel_id == "devices" else panel_id
            watch["revision"] += 1
            await save(runtime)
            async_dispatcher_send(hass, f"{DOMAIN}_updated")
    hass.services.async_register(DOMAIN, "set_active_panel", set_panel, schema=vol.Schema({vol.Required("watch_id"): str, vol.Required("panel_id"): str}))
    await hass.config_entries.async_forward_entry_setups(entry, ["select"])
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, ["select"]):
        return False
    hass.services.async_remove(DOMAIN, "set_active_device")
    hass.services.async_remove(DOMAIN, "set_active_panel")
    runtime = hass.data.pop(DOMAIN, None)
    if runtime:
        tasks = [op.get("task") for op in runtime.get("exercise_operations", {}).values() if op.get("task") is not None]
        runtime.get("exercise_operations", {}).clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return True


async def save(runtime):
    await runtime["store"].async_save({"watches": runtime["manager"].watches, "nonces": runtime["manager"].nonces})


async def async_remove_entry(hass, entry):
    await Store(hass, 1, DOMAIN).async_remove()


async def async_remove_config_entry_device(hass, entry, device_entry):
    """Let HA remove a watch device after atomically revoking its credential."""
    runtime = hass.data.get(DOMAIN)
    identities = [value for domain, value in device_entry.identifiers if domain == DOMAIN]
    if runtime is None or runtime["entry"].entry_id != entry.entry_id or len(identities) != 1:
        return False
    watch_id = identities[0]
    async with runtime["lock"]:
        manager = runtime["manager"]
        watches = {key: value for key, value in manager.watches.items() if key != watch_id}
        nonces = {key: value for key, value in manager.nonces.items() if not key.startswith(watch_id + ":")}
        # Persist first: if saving fails, HA keeps the device and its current authorization.
        await runtime["store"].async_save({"watches": watches, "nonces": nonces})
        manager.watches, manager.nonces = watches, nonces
        manager.sessions = {key: value for key, value in manager.sessions.items() if value.get("watch_id") != watch_id}
        async_dispatcher_send(hass, f"{DOMAIN}_updated")
    return True


def response(value, status=200):
    return web.json_response(value, status=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def read_body(request):
    # Both declared and chunked bodies are bounded before JSON parsing.
    if request.content_type != "application/json":
        raise ValueError("JSON required")
    if request.content_length is not None and request.content_length > 24576:
        raise ValueError("Message too large")
    raw = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.content.iter_chunked(4096):
                raw.extend(chunk)
                if len(raw) > 24576:
                    raise ValueError("Message too large")
    except TimeoutError:
        raise ValueError("Message timeout") from None
    import json
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("Invalid message")
    return body


class PairView(HomeAssistantView):
    requires_auth = False

    def __init__(self, hass, action):
        self.hass, self.action = hass, action
        self.url = f"/api/fan_control/pair/{action}"
        self.name = f"api:fan_control:pair:{action}"

    async def post(self, request):
        runtime = self.hass.data.get(DOMAIN)
        if runtime is None:
            return response({"error": "Integration unavailable"}, 503)
        try:
            body = await read_body(request)
            async with runtime["lock"]:
                # Bound both per-source bursts and total retained counters.
                now = time.monotonic()
                attempts = runtime["attempts"]
                for source in list(attempts):
                    if not attempts[source] or attempts[source][-1] < now - 60:
                        del attempts[source]
                source = (request.remote, self.action)
                if source not in attempts and len(attempts) >= 128:
                    return response({"error": "Try later"}, 429)
                queue = attempts.setdefault(source, deque())
                while queue and queue[0] < now - 60:
                    queue.popleft()
                if len(queue) >= (5 if self.action == "create" else 30):
                    return response({"error": "Try later"}, 429)
                queue.append(now)
                manager = runtime["manager"]
                if self.action == "create":
                    result = manager.create(body["public_key"])
                else:
                    token = request.headers.get("X-Fan-Control-Pairing", "")
                    if len(token) != 64:
                        raise ValueError("Invalid pairing")
                    result = manager.poll(token, self.action == "acknowledge")
                return response(result)
        except (ValueError, KeyError, TypeError):
            return response({"error": "Invalid or expired pairing"}, 400)


class WatchView(HomeAssistantView):
    url = "/api/fan_control/watch/request"
    name = "api:fan_control:watch:request"
    requires_auth = False  # Independent encrypted, revocable watch credential; no HA bearer token.

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request):
        runtime = self.hass.data.get(DOMAIN)
        if runtime is None:
            return response({"error": "Integration unavailable"}, 503)
        try:
            packet = await read_body(request)
            async with runtime["lock"]:
                watch, body = runtime["manager"].authenticate(packet)
                # Persist consumed nonces before dispatch: a restart cannot replay a command.
                await save(runtime)
                owner = await self.hass.auth.async_get_user(watch["owner_id"])
                if owner is None or not owner.is_active or not owner.is_admin:
                    raise ValueError("Pairing owner unavailable")
                action = body.get("action")
                if action == "config":
                    result = configuration(watch)
                elif action in ("panel_state", "panel_command"):
                    panel = find_panel(watch, body.get("panel_id"))
                    if action == "panel_command" and panel.get("type") == "exercise" and body.get("command") != "wake":
                        operations = runtime.setdefault("exercise_operations", {})
                        operation_key = (packet["watch_id"], panel["id"])
                        if body.get("command") == "start_music":
                            from .exercise import start_music
                            result = await start_music(self.hass, panel, Context(user_id=owner.id))
                        elif body.get("command") == "stop_exercise" and "operation_id" in body:
                            result = await accept_stop(self.hass, runtime, panel, Context(user_id=owner.id), operation_key, body)
                        elif body.get("command") == "cancel_stop":
                            current = operations.get(operation_key, {})
                            identity = body.get("operation_id")
                            if isinstance(identity, str) and len(identity) == 36 and (not current or current.get("id") == identity):
                                cancel_stop(operations, operation_key, identity=identity)
                            result = {"operation_status":"cancelled", "steps":[]}
                        else:
                            if body.get("command") == "start_exercise":
                                cancel_stop(operations, operation_key)
                            result = await execute_exercise(self.hass, panel, body.get("command"), Context(user_id=owner.id))
                    else:
                        if action == "panel_command":
                            domain, service, data = panel_command(self.hass, panel, body.get("command"))
                            await self.hass.services.async_call(domain, service, data, blocking=True, context=Context(user_id=owner.id))
                        result = panel_state(self.hass, panel)
                elif action in ("state", "command"):
                    entity = body.get("entity")
                    if entity not in devices([item["entity"] for item in watch["devices"]]):
                        raise ValueError("Device not approved")
                    if action == "command":
                        domain, service, data = command(entity, body.get("command"))
                        await self.hass.services.async_call(domain, service, data, blocking=True, context=Context(user_id=owner.id))
                    state = self.hass.states.get(entity)
                    result = {"entity_id": entity, "state": state.state if state else "unavailable", "attributes": {}}
                    if state:
                        # Only controls' state fields; omit HA context, location, and unrelated attributes.
                        result["attributes"] = {key: state.attributes[key] for key in ("percentage", "supported_features", "friendly_name") if key in state.attributes}
                else:
                    raise ValueError("Unknown action")
                return response(seal_response(watch["secret"], packet, result))
        except Exception as error:
            # Never expose cryptographic, entity, credential, or service details in public responses.
            from cryptography.exceptions import InvalidTag
            if isinstance(error, (ValueError, KeyError, TypeError, InvalidTag, HomeAssistantError)):
                return response({"error": "Request rejected"}, 403)
            raise


def command(entity, action):
    domain = entity.split(".")[0]
    data = {"entity_id": entity}
    if domain == "fan" and isinstance(action, str) and action in [f"speed{i}" for i in range(1, 7)]:
        return domain, "set_percentage", {**data, "percentage": int(action[5:]) * 100 // 6}
    if domain in ("fan", "light", "switch", "input_boolean") and action in ("turn_on", "turn_off", "toggle"):
        return domain, action, data
    if domain in ("script", "scene") and action == "activate":
        return domain, "turn_on", data
    raise ValueError("Unsupported command")
