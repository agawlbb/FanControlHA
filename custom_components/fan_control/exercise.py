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
    player, playlist = value.get("spotify_entity", ""), value.get("spotify_playlist", "")
    if player or playlist:
        if not isinstance(player, str) or not re.fullmatch(r"media_player\.[a-z0-9_]+", player):
            raise ValueError("Select a Spotify player")
        from urllib.parse import urlparse
        if not isinstance(playlist, str):
            raise ValueError("Invalid playlist")
        if playlist.startswith("https://open.spotify.com/"):
            playlist = "spotify:" + urlparse(playlist).path.strip("/").replace("/", ":")
        if not re.fullmatch(r"spotify:playlist:[A-Za-z0-9]{22}", playlist):
            raise ValueError("Invalid playlist")
        source = value.get("spotify_source", "")
        if not isinstance(source, str) or len(source) > 160:
            raise ValueError("Invalid Spotify source")
        result.update(spotify_entity=player, spotify_playlist=playlist, spotify_source=source.strip())
    return result


def schedule_music(hass, runtime, watch_id, panel, context):
    """Give the watch five seconds to establish playback, without delaying car controls."""
    import asyncio
    from .const import DOMAIN
    tasks = runtime.setdefault("music_tasks", {})
    key = (watch_id, panel["id"])
    previous = tasks.pop(key, None)
    if previous is not None:
        previous.cancel()
    watch = runtime["manager"].watches[watch_id]
    expected_owner = watch["owner_id"]
    expected_panel = dict(panel)

    async def deliver():
        await asyncio.sleep(5)
        async with runtime["lock"]:
            if hass.data.get(DOMAIN) is not runtime:
                return
            current = runtime["manager"].watches.get(watch_id)
            owner = await hass.auth.async_get_user(expected_owner)
            if (current is None or current["owner_id"] != expected_owner
                    or owner is None or not owner.is_active or not owner.is_admin
                    or next((p for p in current.get("panels", []) if p.get("id") == panel["id"]), None) != expected_panel):
                return
            await start_music(hass, expected_panel, context)

    task = hass.async_create_task(deliver(), "Fan Control delayed music")
    tasks[key] = task
    def finished(completed):
        if tasks.get(key) is completed:
            tasks.pop(key, None)
    task.add_done_callback(finished)
    return {"music_status": "scheduled"}


async def start_music(hass, panel, context):
    """One explicit Start tap, separate from vehicle retries; use existing HA authorization."""
    panel = validate_exercise(panel)
    entity = panel.get("spotify_entity")
    if not entity:
        return {"music_status": "not_configured"}
    player = hass.states.get(entity)
    if player is None or player.state in ("unknown", "unavailable"):
        return {"music_status": "unavailable"}
    source = panel.get("spotify_source", "")
    if source and source not in player.attributes.get("source_list", []):
        return {"music_status": "source_unavailable"}
    target = {"entity_id": entity}
    try:
        if source:
            await hass.services.async_call("media_player", "select_source", {**target, "source": source}, blocking=True, context=context)
        await hass.services.async_call("media_player", "shuffle_set", {**target, "shuffle": False}, blocking=True, context=context)
        await hass.services.async_call("media_player", "play_media", {**target,
            "media_content_id": panel["spotify_playlist"], "media_content_type": "playlist"}, blocking=True, context=context)
        await hass.services.async_call("media_player", "shuffle_set", {**target, "shuffle": False}, blocking=True, context=context)
        return {"music_status": "requested"}
    except Exception:
        # Do not replay an ambiguous playlist request or expose account details.
        return {"music_status": "unconfirmed"}


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


# Ephemeral request-driven retry state. Nothing is scheduled or persisted for replay.
import time
STOP_SERVER_STARTED_MS = int(time.time() * 1000)


async def retry_stop(hass, panel, context, operations, key, body, now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    identity, issued = body.get("operation_id"), body.get("issued_at_ms")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9-]{36}", identity) or type(issued) is not int:
        raise ValueError("Invalid Stop operation")
    # Reject old requests after HA restart, expiry, or a newer user action.
    if issued < STOP_SERVER_STARTED_MS or issued > now + 5000 or now >= issued + 120000:
        return {"operation_status":"expired", "steps":[]}
    # Music is independent of Stop's physical-role snapshot. Older watch models omit it.
    vehicle_panel = {key: value for key, value in panel.items() if not key.startswith("spotify_")}
    if body.get("panel_config") not in (panel, vehicle_panel):
        raise ValueError("Exercise target changed")
    previous = operations.get(key)
    if previous and (identity in previous.get("blocked", set()) or issued <= previous.get("cancelled_at", -1) or issued < previous["issued"]):
        return {"operation_status":"cancelled", "steps":[]}
    if previous and previous.get("id") == identity:
        operation = previous
        if operation["panel"] != panel or operation["issued"] != issued:
            raise ValueError("Exercise target changed")
    else:
        if previous and issued == previous["issued"]:
            return {"operation_status":"cancelled", "steps":[]}
        operation = {"id":identity, "issued":issued, "panel":dict(panel), "done":set(), "next":0, "attempts":0, "blocked": previous.get("blocked", set()) if previous else set()}
        operations[key] = operation
    if operation.get("complete"):
        return {"operation_status":"complete", "steps":[{"step":s,"status":"confirmed"} for s in ("unlock","turn_on","set_temperature")]}
    state = exercise_state(hass, panel)
    if not state["vehicle_ready"]:
        return {"operation_status":"pending", "steps":[], "message":"Waiting for vehicle"}
    if not state["stop_available"]:
        return {"operation_status":"unsupported", "steps":[], "message":"Temperature or climate capability unsupported"}
    climate = hass.states.get(panel["climate_entity"])
    attrs = climate.attributes
    target = target_temperature(climate_unit(hass, attrs), attrs.get("min_temp"), attrs.get("max_temp"))
    lock = hass.states.get(panel["lock_entity"])
    desired = {"unlock": lock.state == "unlocked", "turn_on": climate.state != "off",
               "set_temperature": isinstance(attrs.get("temperature"), (int,float)) and not isinstance(attrs.get("temperature"), bool)
                    and math.isclose(attrs["temperature"], target, abs_tol=0.01)}
    operation["done"].update(step for step, satisfied in desired.items() if satisfied)
    names = ("unlock", "turn_on", "set_temperature")
    if len(operation["done"]) == 3:
        operation["complete"] = True
        return {"operation_status":"complete", "steps":[{"step":s,"status":"confirmed"} for s in names]}
    results = []
    allowed = now >= operation["next"]
    operation["attempts"] += int(allowed)
    if allowed:
        operation["next"] = now + min(15000, 2000 * 2 ** min(operation["attempts"], 3))
    commands = {"unlock":("lock","unlock",{"entity_id":panel["lock_entity"]}),
                "turn_on":("climate","turn_on",{"entity_id":panel["climate_entity"]}),
                "set_temperature":("climate","set_temperature",{"entity_id":panel["climate_entity"],"temperature":target})}
    for step in names:
        if step in operation["done"]:
            results.append({"step":step,"status":"confirmed"}); continue
        if not allowed:
            results.append({"step":step,"status":"pending"}); continue
        try:
            if (int(time.time()*1000) if now_ms is None else now) >= issued + 120000:
                return {"operation_status":"expired", "steps":results}
            online = hass.states.get(panel["awake_entity"])
            if online is None or online.state != "on": raise ValueError("Offline")
            domain, service, data = commands[step]
            await hass.services.async_call(domain, service, data, blocking=True, context=context)
            results.append({"step":step,"status":"requested"})
        except Exception:
            results.append({"step":step,"status":"unconfirmed"})
            allowed = False
    return {"operation_status":"pending", "steps":results}


def cancel_stop(operations, key, now_ms=None, identity=None):
    now = int(time.time()*1000) if now_ms is None else now_ms
    previous = operations.get(key, {})
    blocked = set(previous.get("blocked", set()))
    if previous.get("id"): blocked.add(previous["id"])
    if identity: blocked.add(identity)
    # Only short-lived operations are valid; cap memory without creating a retry queue.
    if len(blocked) > 32: blocked = {value for value in (previous.get("id"), identity) if value}
    operations[key] = {"id":None, "issued":now, "cancelled_at":now, "blocked":blocked}


async def accept_stop(hass, runtime, panel, context, key, body):
    import asyncio
    operations = runtime.setdefault("exercise_operations", {})
    result = await retry_stop(hass, panel, context, operations, key, body)
    operation = operations.get(key)
    if result["operation_status"] != "pending" or operation is None or operation.get("task") is not None:
        return result
    expected_owner = runtime["manager"].watches[key[0]]["owner_id"]
    async def continue_stop():
        try:
            for _ in range(30):
                await asyncio.sleep(4)
                async with runtime["lock"]:
                    if operations.get(key) is not operation:
                        return
                    watch = runtime["manager"].watches.get(key[0])
                    if watch is None or watch["owner_id"] != expected_owner or next((p for p in watch.get("panels", []) if p.get("id")==key[1]), None) != panel:
                        cancel_stop(operations,key); return
                    owner = await hass.auth.async_get_user(watch["owner_id"])
                    if owner is None or not owner.is_active or not owner.is_admin:
                        cancel_stop(operations,key); return
                    update = await retry_stop(hass,panel,context,operations,key,body)
                    if update["operation_status"] != "pending":
                        return
        finally:
            operation["task"] = None
    operation["task"] = asyncio.create_task(continue_stop())
    return result
