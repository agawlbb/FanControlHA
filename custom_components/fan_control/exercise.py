"""Pure Exercise-mode command preparation; no service calls or configured targets.

The authenticated panel dispatcher must validate readiness before executing a plan.
Watch-local Spotify launch and the still-unresolved pause target are not HA commands.
"""
from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class ExerciseTargets:
    lock_entity: str
    climate_entity: str
    wake_entity: str
    awake_entity: str

    def __post_init__(self):
        for value, domain in ((self.lock_entity, "lock"), (self.climate_entity, "climate"),
                              (self.wake_entity, "button"), (self.awake_entity, "binary_sensor")):
            if not isinstance(value, str) or len(value) > 160 or not re.fullmatch(domain + r"\.[a-z0-9_]+", value):
                raise ValueError("Invalid Exercise entity role")

    def __repr__(self):
        return "ExerciseTargets[redacted]"


def target_temperature(unit, minimum, maximum):
    """63 F in HA's exposed climate units; reject unknown units or invalid limits.

    Never silently clamp or round to a different requested temperature. Device
    precision must be inspected by the eventual dispatcher and reported honestly.
    """
    if unit == "°F":
        target = 63.0
    elif unit == "°C":
        target = (63.0 - 32.0) * 5.0 / 9.0
    else:
        raise ValueError("Unsupported climate temperature unit")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (minimum, maximum)) or not minimum <= target <= maximum:
        raise ValueError("Exercise temperature outside supported limits")
    return target


def command_plan(targets, action, *, ready, unit=None, minimum=None, maximum=None, climate_on=False):
    """Return only fixed allowlisted actions; never execute or retry a plan.

    Callers must preflight every required capability before sending any action,
    recheck online status, and report per-step results without replaying completed
    steps after cancellation, partial failure, or an ambiguous response.
    """
    if not isinstance(targets, ExerciseTargets) or ready is not True:
        raise ValueError("Exercise vehicle not ready")
    if action == "start_exercise":
        return [("lock", "lock", {"entity_id": targets.lock_entity})]
    if action == "stop_exercise":
        temperature = target_temperature(unit, minimum, maximum)
        plan = [("lock", "unlock", {"entity_id": targets.lock_entity})]
        if not climate_on:
            plan.append(("climate", "turn_on", {"entity_id": targets.climate_entity}))
        plan.append(("climate", "set_temperature", {"entity_id": targets.climate_entity, "temperature": temperature}))
        return plan
    raise ValueError("Unsupported Exercise action")


def validate_exercise(value):
    targets = ExerciseTargets(*(value.get(role) for role in ("lock_entity", "climate_entity", "wake_entity", "awake_entity")))
    result = {"type": "exercise"}
    for key, default in (("id", "exercise"), ("name", "Exercise")):
        text = value.get(key, default)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 40:
            raise ValueError("Invalid Exercise label")
        result[key] = text.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", result["id"]) or result["id"] == "devices":
        raise ValueError("Invalid Exercise ID")
    result.update(vars(targets))
    return result


def climate_unit(hass, attributes):
    return attributes.get("temperature_unit") or getattr(getattr(getattr(hass, "config", None), "units", None), "temperature_unit", None)


def exercise_state(hass, panel):
    online = hass.states.get(panel["awake_entity"])
    lock = hass.states.get(panel["lock_entity"])
    climate = hass.states.get(panel["climate_entity"])
    ready = (online is not None and online.state == "on" and lock is not None
             and lock.state in ("locked", "unlocked") and climate is not None
             and climate.state in ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"))
    stop = ready
    try:
        if not climate or not (climate.attributes.get("supported_features", 0) & 1):
            raise ValueError("Temperature unsupported")
        if climate.state == "off" and not climate.attributes.get("supported_features", 0) & 256:
            raise ValueError("Climate power unsupported")
        attrs = climate.attributes
        target = target_temperature(climate_unit(hass, attrs), attrs.get("min_temp"), attrs.get("max_temp"))
        step = attrs.get("target_temp_step")
        if step is not None and (isinstance(step, bool) or not isinstance(step, (int, float))
                or not math.isfinite(step) or step <= 0 or not math.isclose(target / step, round(target / step), abs_tol=1e-6)):
            raise ValueError("Exact temperature unsupported")
    except (ValueError, TypeError):
        stop = False
    return {"vehicle_ready": ready, "start_available": ready, "stop_available": stop,
            "locked": ready and lock.state == "locked", "spotify_pause": "watch_local"}


async def execute_exercise(hass, panel, action, context):
    """No retries or rollback: accurately return requested/unknown/skipped steps."""
    state = exercise_state(hass, panel)
    if action not in ("start_exercise", "stop_exercise") or not state[
            "start_available" if action == "start_exercise" else "stop_available"]:
        raise ValueError("Exercise action unavailable")
    climate = hass.states.get(panel["climate_entity"])
    attrs = climate.attributes
    plan = command_plan(ExerciseTargets(*(panel[role] for role in
        ("lock_entity", "climate_entity", "wake_entity", "awake_entity"))), action, ready=True,
        unit=climate_unit(hass, attrs), minimum=attrs.get("min_temp"), maximum=attrs.get("max_temp"), climate_on=climate.state != "off")
    results = []
    failed = False
    for domain, service, data in plan:
        if failed:
            results.append({"step": service, "status": "skipped"})
            continue
        try:
            # Recheck online/readiness before every physical action.
            online = hass.states.get(panel["awake_entity"])
            entity = hass.states.get(data["entity_id"])
            if online is None or online.state != "on" or entity is None or entity.state in ("unknown", "unavailable"):
                raise ValueError("Vehicle unavailable")
            await hass.services.async_call(domain, service, data, blocking=True, context=context)
            results.append({"step": service, "status": "requested"})
        except Exception:
            # A transport/service error cannot prove the physical action did not happen.
            results.append({"step": service, "status": "unconfirmed"})
            failed = True
    result = exercise_state(hass, panel)
    result["steps"] = results
    return result
