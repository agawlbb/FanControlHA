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
    state = panel_state(hass, op["panel"])
    return {**state, "operation_id": op["id"], "operation_status": op["status"],
            "issued_at_ms": op["issued"], "deadline_ms": op["issued"] + DELIVERY_SECONDS * 1000,
            "final_deadline_ms": op["issued"] + (DELIVERY_SECONDS + FINAL_SECONDS) * 1000,
            "stage": op["stage"], "reason": op.get("reason", ""), "steps": list(op["steps"])}


def online(hass, panel):
    state = hass.states.get(panel["awake_entity"])
    return state is not None and state.state == "on"


def matches(hass, op):
    p, action = op["panel"], op["command"]
    state = panel_state(hass, p)
    if action.startswith("garage_"):
        return state.get("garage_state") == ("open" if action.endswith("open") else "closed")
    if not online(hass, p):
        return False
    if action == "wake":
        return True
    if action.startswith("climate_"):
        return state.get("climate_available") is True and state.get("climate_on") == (action == "climate_on")
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
                or next((p for p in watch.get("panels", []) if p.get("id") == op["panel"]["id"]), None) != op["panel"]):
            return False
        owner = await hass.auth.async_get_user(op["owner"])
        return owner is not None and owner.is_active and owner.is_admin


async def service(hass, runtime, op, domain, name, data):
    if not await valid(hass, runtime, op) or now_ms() >= op["issued"] + DELIVERY_SECONDS * 1000:
        raise asyncio.CancelledError
    op["stage"] = "delivery"
    op["status"] = "delivering"
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
                    await service(hass, runtime, op, *panel_command(hass, p, "wake"))
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
    independent = action.startswith("garage_") or action == "podcast"
    one_shot = independent or action in ("honk", "leave") or action.startswith("trunk_")
    sent, next_attempt, attempts = False, 0, 0
    try:
        async with asyncio.timeout(max(0.01, (op["issued"] + DELIVERY_SECONDS * 1000 - now_ms()) / 1000)):
            if not independent:
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
                            if action in ("honk", "leave", "podcast"):
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
        op["status"] = "cancelled"
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
    if action in ("vehicle_status", "vehicle_cancel"):
        if op is None:
            return {"operation_id": identity, "operation_status": "unknown"}
        if (op["secret"] != watch["secret"] or op["owner"] != watch["owner_id"]
                or next((p for p in watch.get("panels", []) if p.get("id") == op["panel"]["id"]), None) != op["panel"]):
            op["status"] = "cancelled"
            op["task"].cancel()
            return {"operation_id": identity, "operation_status": "cancelled"}
        if action == "vehicle_cancel":
            op["status"] = "cancelled"
            op["task"].cancel()
        elif op["status"] == "unconfirmed" and matches(hass, op):
            # Late evidence can clear an obsolete alert but must not select a panel.
            op["status"] = "complete"
            notification_id = "fan_control_vehicle_" + op["id"]
            op["cleanup_task"] = asyncio.create_task(notify(hass, "dismiss", {"notification_id": notification_id}))
        return snapshot(hass, op)
    p = find_panel(watch, body.get("panel_id"))
    if not p.get("wake_entity") or body.get("panel_config") != p or body.get("command") not in COMMANDS.get(p["type"], set()):
        raise ValueError("Vehicle target changed")
    if op is not None:
        if op["panel"] != p or op["command"] != body["command"] or op["issued"] != issued:
            raise ValueError("Operation changed")
        return snapshot(hass, op)
    now = now_ms()
    if type(issued) is not int or issued < STARTED_MS or issued > now + 5000 or now >= issued + DELIVERY_SECONDS * 1000:
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
          "status": "pending", "stage": "accepted", "steps": []}
    operations[key] = op
    if body["command"] != "wake":
        latest[target] = op
    op["task"] = asyncio.create_task(run(hass, runtime, op))
    return snapshot(hass, op)
