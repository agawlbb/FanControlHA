"""Apple-only, bounded vehicle intents. Network waits never hold the runtime lock."""
import asyncio
import copy
import re
import time

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .panels import find_panel, panel_command, panel_state
from .exercise import climate_unit, select_driving_after_stop

DELIVERY_SECONDS = 180
FINAL_SECONDS = 15
WAKE_SECONDS = 90
STARTED_MS = int(time.time() * 1000)
ACTIVE = {"pending", "waking", "waiting", "delivering", "confirming"}
COMMANDS = {
    "driving": {"wake", "garage_open", "garage_close", "trunk_open", "trunk_close", "climate_on", "climate_off", "honk", "podcast"},
    "exercise": {"wake", "start_exercise", "stop_exercise"},
    "work_morning": {"wake", "climate_on", "climate_off", "leave"},
}


def now_ms():
    return int(time.time() * 1000)


def snapshot(hass, op):
    state = observed(hass, op["panel"])
    return {**state, "operation_id": op["id"], "operation_status": op["status"],
            "issued_at_ms": op["issued"], "deadline_ms": op["issued"] + DELIVERY_SECONDS * 1000,
            "final_deadline_ms": op["issued"] + (DELIVERY_SECONDS + FINAL_SECONDS) * 1000,
            "stage": op["stage"], "reason": op.get("reason", ""), "steps": list(op["steps"])}


def configured_target(watch, panel):
    if panel.get("type") == "device":
        return panel if panel["entity"] in [d["entity"] for d in watch.get("devices", [])] else None
    return next((p for p in watch.get("panels", []) if p.get("id") == panel["id"]), None)


def observed(hass, panel):
    if panel.get("type") != "device":
        return panel_state(hass, panel)
    state = hass.states.get(panel["entity"])
    return {"state": state.state if state else "unavailable", "attributes": {
        k: state.attributes[k] for k in ("percentage", "supported_features") if k in state.attributes} if state else {}}


def records(runtime):
    return [{k: copy.deepcopy(v) for k, v in op.items() if k not in ("task", "cleanup_task", "gate")}
            for op in runtime.get("vehicle_operations", {}).values()]


async def persist(runtime):
    if runtime.get("operation_store") is not None:
        async with runtime.setdefault("operation_save_lock", asyncio.Lock()):
            await runtime["operation_store"].async_save({"operations": records(runtime), "cancellations": [
                [watch_id, identity, timestamp] for (watch_id, identity), timestamp in runtime.get("operation_cancellations", {}).items()]})


async def restore(hass, runtime, saved):
    operations = runtime.setdefault("vehicle_operations", {})
    if isinstance(saved, dict):
        runtime["operation_cancellations"] = {(wid, identity): stamp for wid, identity, stamp in saved.get("cancellations", []) if now_ms() - stamp <= 600000}
        saved = saved.get("operations", [])
    for op in saved or []:
        if now_ms() > op["issued"] + 600000:
            continue
        operations[(op["watch_id"], op["id"])] = op
        if op["command"] != "wake":
            key = (op["watch_id"], op["panel"]["id"])
            previous = runtime.setdefault("vehicle_latest", {}).get(key)
            if previous is None or previous["issued"] < op["issued"]:
                runtime["vehicle_latest"][key] = op
        if op["status"] in ACTIVE:
            op["task"] = asyncio.create_task(run(hass, runtime, op))
        else:
            op["task"] = asyncio.create_task(asyncio.sleep(0))


def online(hass, panel):
    state = hass.states.get(panel["awake_entity"])
    return state is not None and state.state == "on"


def matches(hass, op):
    p, action = op["panel"], op["command"]
    state = observed(hass, p)
    if p.get("type") in ("device", "fan"):
        if action in ("turn_on", "turn_off"):
            return state.get("state") == ("on" if action == "turn_on" else "off")
        if action.startswith("speed"):
            return state.get("state") == "on" and state.get("attributes", {}).get("percentage") == int(action[5:]) * 100 // 6
        return False
    if action.startswith("trunk_"):
        return online(hass, p) and state.get("trunk_state") == ("open" if action.endswith("open") else "closed")
    if action.startswith("garage_"):
        return state.get("garage_state") == ("open" if action.endswith("open") else "closed")
    if p.get("wake_entity") and not online(hass, p):
        return False
    if action == "wake":
        return True
    if action.startswith("climate_"):
        climate = hass.states.get(p["climate_entity"])
        return climate is not None and climate.state in ("off", "on", "heat", "cool", "heat_cool", "auto", "dry", "fan_only") and (climate.state != "off") == (action == "climate_on")
    if action in ("start_exercise", "stop_exercise"):
        lock = hass.states.get(p["lock_entity"])
        if lock is None or lock.state != ("locked" if action == "start_exercise" else "unlocked"):
            return False
        climate = hass.states.get(p["climate_entity"])
        return action == "start_exercise" or climate is not None and climate.state in ("heat", "cool", "heat_cool", "auto", "dry", "fan_only")
    return False


async def valid(hass, runtime, op):
    """Short lock scope; exact owner/key/config identity, independent of selection."""
    async with runtime["lock"]:
        watch = runtime["manager"].watches.get(op["watch_id"])
        if (hass.data.get(DOMAIN) is not runtime or op["status"] == "cancelled" or watch is None
                or watch.get("owner_id") != op["owner"] or watch.get("secret") != op["secret"]
                or configured_target(watch, op["panel"]) != op["panel"]):
            return False
        owner = await hass.auth.async_get_user(op["owner"])
        return owner is not None and owner.is_active and owner.is_admin


async def service(hass, runtime, op, domain, name, data):
    if not await valid(hass, runtime, op) or now_ms() >= op["issued"] + DELIVERY_SECONDS * 1000:
        raise asyncio.CancelledError
    op["stage"] = "delivery"
    op["status"] = "delivering"
    # Persist the dispatch boundary BEFORE a possibly ambiguous physical call.
    if data.get("entity_id") == op["panel"].get("wake_entity"):
        op["wake_started"] = True
    elif op.get("one_shot"):
        op["sent"] = True
    await persist(runtime)
    if not await valid(hass, runtime, op) or now_ms() >= op["issued"] + DELIVERY_SECONDS * 1000:
        raise asyncio.CancelledError
    await hass.services.async_call(domain, name, data, blocking=True, context=Context(user_id=op["owner"]))


async def wake(hass, runtime, op):
    """A single shared in-flight wake per owner and vehicle; never replay on timeout."""
    p = op["panel"]
    if online(hass, p):
        return
    key = (op["owner"], p["wake_entity"], p["awake_entity"])
    wakes = runtime.setdefault("vehicle_wakes", {})
    attempt = wakes.get(key)
    if attempt is None or now_ms() >= attempt["expires"]:
        async def deliver():
            try:
                async with asyncio.timeout(WAKE_SECONDS):
                    # The wake belongs to the vehicle, not the superseded UI intent.
                    candidates = [v for v in runtime.get("vehicle_operations", {}).values()
                                  if v["status"] in ACTIVE and v["owner"] == op["owner"]
                                  and v["panel"].get("wake_entity") == p["wake_entity"]]
                    candidate = next((v for v in candidates if v["status"] != "cancelled"), op)
                    await service(hass, runtime, candidate, *panel_command(hass, p, "wake"))
            except Exception:
                pass  # A wake timeout is ambiguous; readiness observations decide.
        task = asyncio.create_task(deliver())
        wakes[key] = {"task": task, "expires": now_ms() + WAKE_SECONDS * 1000}
    op["status"], op["stage"] = "waking", "wake"
    end = min(op["issued"] + WAKE_SECONDS * 1000, op["issued"] + DELIVERY_SECONDS * 1000)
    while not online(hass, p) and now_ms() < end:
        if not await valid(hass, runtime, op):
            raise asyncio.CancelledError
        await asyncio.sleep(1)


async def finish(hass, runtime, op, status, reason=""):
    if not await valid(hass, runtime, op):
        op["status"] = "cancelled"
        return
    op["status"], op["reason"] = status, reason
    op["finished_ms"] = now_ms()
    await persist(runtime)
    if status == "complete" and op["command"] == "stop_exercise":
        async with runtime["lock"]:
            # Reuse the same once-only, unique vehicle selection path as legacy Stop.
            key = (op["watch_id"], op["panel"]["id"])
            operations = runtime.setdefault("exercise_operations", {})
            operations[key] = op
            await select_driving_after_stop(hass, runtime, op["panel"], key, op, {"operation_status": "complete"})
    if op["command"] in ("start_exercise", "stop_exercise"):
        notification_id = "fan_control_vehicle_" + op["id"]
        if status == "complete":
            await notify(hass, "dismiss", {"notification_id": notification_id})
        elif status in ("failed", "unconfirmed"):
            await notify(hass, "create", {
                "notification_id": notification_id,
                "title": "Exercise request failed" if status == "failed" else "Exercise not confirmed",
                "message": reason + ". Check " + ("that the car is locked" if op["command"] == "start_exercise" else "that the car is unlocked and climate is on") + ". Based on Home Assistant observations.",
            })


async def notify(hass, service_name, data):
    try:
        await hass.services.async_call("persistent_notification", service_name, data, blocking=True)
    except Exception:
        pass  # Notification delivery must never change a vehicle operation's outcome.


async def run(hass, runtime, op):
    action, p = op["command"], op["panel"]
    independent = not p.get("wake_entity") or action.startswith("garage_") or action == "podcast"
    one_shot = action in ("honk", "leave", "activate", "podcast") or action.startswith(("trunk_", "garage_"))
    op["one_shot"] = one_shot
    sent, next_attempt, attempts = op.get("sent", False), 0, 0
    if op.get("gate") is not None:
        await op["gate"].wait()
    while now_ms() < op.get("not_before", op["issued"]):
        await asyncio.sleep(0.1)
    try:
        async with asyncio.timeout(max(0.01, (op["issued"] + DELIVERY_SECONDS * 1000 - now_ms()) / 1000)):
            if not independent and not op.get("wake_started"):
                await wake(hass, runtime, op)
            while now_ms() < op["issued"] + DELIVERY_SECONDS * 1000:
                if not await valid(hass, runtime, op):
                    raise asyncio.CancelledError
                if action == "wake" and online(hass, p) or sent and matches(hass, op):
                    await finish(hass, runtime, op, "complete")
                    return
                op["status"], op["stage"] = "waiting", "readiness"
                if (independent or online(hass, p)) and now_ms() >= next_attempt and (not one_shot or not sent):
                    commands = []
                    if action in ("start_exercise", "stop_exercise"):
                        lock = hass.states.get(p["lock_entity"])
                        desired = "locked" if action == "start_exercise" else "unlocked"
                        if lock is not None and lock.state in ("locked", "unlocked") and lock.state != desired:
                            commands.append(("lock", "lock" if action == "start_exercise" else "unlock", {"entity_id": p["lock_entity"]}))
                        climate = hass.states.get(p["climate_entity"])
                        if action == "stop_exercise" and climate is not None and climate.state == "off":
                            commands.append(("climate", "turn_on", {"entity_id": p["climate_entity"]}))
                        if action == "stop_exercise" and not op.get("temperature_requested") and climate is not None:
                            unit = climate_unit(hass, climate.attributes)
                            target = 63.0 if unit == "°F" else (63 - 32) * 5 / 9 if unit == "°C" else None
                            if target is not None:
                                commands.append(("climate", "set_temperature", {"entity_id": p["climate_entity"], "temperature": target}))
                    elif action != "wake":
                        try:
                            if p.get("type") == "device":
                                from . import command
                                entity_state = hass.states.get(p["entity"])
                                if entity_state is not None and entity_state.state not in ("unknown", "unavailable"):
                                    commands = [command(p["entity"], action)]
                            else:
                                commands = [panel_command(hass, p, action)]
                        except ValueError:
                            pass  # Unknown/unavailable HA fields are not definitive rejections.
                    for domain, name, data in commands:
                        if not independent and not online(hass, p):
                            break
                        sent = True  # Set before awaiting; ambiguity cannot replay a one-shot.
                        try:
                            await service(hass, runtime, op, domain, name, data)
                            op["steps"] = [{"step": name, "status": "requested"}]
                            if name == "set_temperature":
                                op["temperature_requested"] = True
                            if action in ("honk", "leave", "podcast", "activate"):
                                await finish(hass, runtime, op, "accepted")
                                return
                        except ServiceValidationError:
                            if name == "set_temperature":
                                op["temperature_requested"] = True
                                continue  # Best effort; never blocks required unlock/climate.
                            await finish(hass, runtime, op, "failed", "Vehicle integration rejected the request")
                            return
                        except HomeAssistantError:
                            # Providers use this for both transport and validation failures.
                            op["reason"] = "Vehicle delivery not confirmed"
                        except Exception:
                            op["reason"] = "Vehicle delivery not confirmed"
                    # A request whose outcome already holds still requires a matching live read.
                    if action in ("start_exercise", "stop_exercise") and matches(hass, op):
                        await finish(hass, runtime, op, "complete")
                        return
                    attempts += 1
                    next_attempt = now_ms() + min(15000, 2000 * 2 ** min(attempts, 3))
                op["status"], op["stage"] = "confirming" if sent else "waiting", "confirmation" if sent else "readiness"
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        # Integration reload preserves accepted work; explicit cancellation was saved separately.
        if not runtime.get("unloading"):
            op["status"] = "cancelled"
            await persist(runtime)
        return
    except TimeoutError:
        pass
    except Exception:
        op["reason"] = "Vehicle response unavailable"
    # No physical dispatch beyond 180 seconds. Final grace reads HA observations only.
    op["stage"], op["status"] = "final_check", "confirming"
    while now_ms() < op["issued"] + (DELIVERY_SECONDS + FINAL_SECONDS) * 1000:
        if not await valid(hass, runtime, op):
            op["status"] = "cancelled"
            return
        if (sent or action == "wake") and matches(hass, op):
            await finish(hass, runtime, op, "complete")
            return
        await asyncio.sleep(1)
    await finish(hass, runtime, op, "unconfirmed", op.get("reason") or "Vehicle outcome unavailable after three minutes")


def handle(hass, runtime, watch_id, watch, body):
    """Called under runtime lock; only registers tasks or returns their current state."""
    if watch.get("platform") != "watch_os":
        raise ValueError("Apple Watch required")
    identity, issued = body.get("operation_id"), body.get("issued_at_ms")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9-]{36}", identity):
        raise ValueError("Invalid operation")
    operations = runtime.setdefault("vehicle_operations", {})
    key = (watch_id, identity)
    op = operations.get(key)
    action = body["action"]
    if action in ("vehicle_status", "vehicle_cancel", "operation_status", "operation_cancel"):
        if op is None:
            if action in ("vehicle_cancel", "operation_cancel"):
                runtime.setdefault("operation_cancellations", {})[(watch_id, identity)] = now_ms()
            return {"operation_id": identity, "operation_status": "unknown"}
        if (op["secret"] != watch["secret"] or op["owner"] != watch["owner_id"]
                or configured_target(watch, op["panel"]) != op["panel"]):
            op["status"] = "cancelled"
            op["task"].cancel()
            return {"operation_id": identity, "operation_status": "cancelled"}
        if action in ("vehicle_cancel", "operation_cancel"):
            op["status"] = "cancelled"
            op["task"].cancel()
        elif op["status"] == "unconfirmed" and matches(hass, op):
            # Late evidence can clear an obsolete alert but must not select a panel.
            op["status"] = "complete"
            notification_id = "fan_control_vehicle_" + op["id"]
            op["cleanup_task"] = asyncio.create_task(notify(hass, "dismiss", {"notification_id": notification_id}))
        return snapshot(hass, op)
    cancelled = runtime.setdefault("operation_cancellations", {})
    for old in list(cancelled):
        if now_ms() - cancelled[old] > 600000:
            del cancelled[old]
    if key in cancelled:
        return {"operation_id": identity, "operation_status": "cancelled"}
    if body.get("entity") is not None:
        entity = body["entity"]
        if entity not in [d["entity"] for d in watch.get("devices", [])]:
            raise ValueError("Device not approved")
        from . import command
        if body.get("command") == "toggle":
            raise ValueError("Absolute intent required")
        command(entity, body.get("command"))
        p = {"id": entity, "entity": entity, "type": "device"}
    else:
        p = find_panel(watch, body.get("panel_id"))
        config = {k: v for k, v in p.items() if not k.startswith("spotify_")}
        allowed = COMMANDS.get(p["type"], set()) if p["type"] != "fan" else {"turn_on", "turn_off", *[f"speed{i}" for i in range(1, 7)]}
        if body.get("panel_config") not in (p, config) or body.get("command") not in allowed:
            raise ValueError("Command target changed")
    if op is not None:
        if op["panel"] != p or op["command"] != body["command"] or op["issued"] != issued:
            raise ValueError("Operation changed")
        return snapshot(hass, op)
    now = now_ms()
    if type(body.get("not_before_ms", issued)) is not int:
        raise ValueError("Invalid delay")
    if type(issued) is not int or issued > now + 5000 or now >= issued + DELIVERY_SECONDS * 1000:
        raise ValueError("Expired operation")
    latest = runtime.setdefault("vehicle_latest", {})
    target = (watch_id, p["id"])
    if body["command"] != "wake":
        previous = latest.get(target)
        if previous is not None and previous["issued"] >= issued:
            raise ValueError("Stale operation")
        if previous is not None and previous["command"] in ("start_exercise", "stop_exercise"):
            asyncio.create_task(notify(hass, "dismiss", {"notification_id": "fan_control_vehicle_" + previous["id"]}))
    for old_key, old in list(operations.items()):
        if now > old["issued"] + 600000:
            operations.pop(old_key)
        elif old["watch_id"] == watch_id and old["panel"]["id"] == p["id"] and old["status"] in ACTIVE and body["command"] != "wake" and old["command"] != "wake":
            if old["issued"] >= issued:
                raise ValueError("Stale operation")
            old["status"] = "cancelled"
            old["task"].cancel()
    if len(operations) >= 128:
        raise ValueError("Too many operations")
    op = {"id": identity, "issued": issued, "watch_id": watch_id, "owner": watch["owner_id"],
          "secret": watch["secret"], "panel": copy.deepcopy(p), "command": body["command"],
          "status": "pending", "stage": "accepted", "steps": [],
          "not_before": min(issued + 1000, max(issued, body.get("not_before_ms", issued))) }
    operations[key] = op
    if body["command"] != "wake":
        latest[target] = op
    if runtime.get("operation_store") is not None:
        op["gate"] = asyncio.Event()
    op["task"] = asyncio.create_task(run(hass, runtime, op))
    return snapshot(hass, op)
