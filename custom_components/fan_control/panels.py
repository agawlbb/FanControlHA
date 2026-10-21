"""Typed, user-configured panels. Entity permissions remain server-side."""
import math
import re
from .panel_color import add_color
from .exercise import validate_exercise, exercise_state
from .driving import validate_driving, driving_state, driving_command


def validate_panel(value):
    if isinstance(value, dict) and value.get("type") == "fan":
        result = {"type": "fan"}
        for key, default in (("id", "fan"), ("name", "Fan Control")):
            text = value.get(key, default)
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 40:
                raise ValueError("Invalid fan panel label")
            result[key] = text.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", result["id"]) or result["id"] == "devices":
            raise ValueError("Invalid panel ID")
        entity = value.get("fan_entity")
        if not isinstance(entity, str) or len(entity) > 160 or not re.fullmatch(r"fan\.[a-z0-9_]+", entity):
            raise ValueError("Select a fan")
        result["fan_entity"] = entity
        return result
    if isinstance(value, dict) and value.get("type") == "driving":
        return validate_driving(value)
    if isinstance(value, dict) and value.get("type") == "exercise":
        return validate_exercise(value)
    if not isinstance(value, dict) or value.get("type", "work_morning") != "work_morning":
        raise ValueError("Unsupported panel")
    result = {"type": "work_morning"}
    for key, default, maximum in (("id", "work_morning", 40), ("name", "Work Morning", 40),
                                   ("climate_label", "Climate", 12), ("script_label", "Leave for work", 18)):
        text = value.get(key, default)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > maximum:
            raise ValueError("Invalid panel label")
        result[key] = text.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", result["id"]) or result["id"] == "devices":
        raise ValueError("Invalid panel ID")
    for role, domains in (("battery_entity", ("sensor",)), ("climate_entity", ("climate", "switch", "input_boolean")), ("script_entity", ("script",))):
        entity = value.get(role)
        if not isinstance(entity, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", entity) or len(entity) > 160 or entity.split(".")[0] not in domains:
            raise ValueError("Invalid panel entity")
        result[role] = entity
    wake, awake = value.get("wake_entity", ""), value.get("awake_entity", "")
    if wake or awake:
        for entity, domain in ((wake, "button"), (awake, "binary_sensor")):
            if not isinstance(entity, str) or len(entity) > 160 or not re.fullmatch(domain + r"\.[a-z0-9_]+", entity):
                raise ValueError("Configure both vehicle wake and online entities")
        result.update(wake_entity=wake, awake_entity=awake)
    return add_color(result, value)


def find_panel(watch, panel_id):
    panel = next((item for item in watch.get("panels", []) if item["id"] == panel_id), None)
    if panel is None:
        raise ValueError("Panel not approved")
    return validate_panel(panel)


def configuration(watch):
    result = {key: watch[key] for key in ("url", "remote_url", "entity", "devices", "revision")}
    panels = [validate_panel(item) for item in watch.get("panels", [])]
    result["devices"] = list(watch["devices"]) + [{"entity": "panel." + panel["id"], "title": panel["name"], "panel": panel} for panel in panels]
    if watch.get("active_panel"):
        result["entity"] = "panel." + find_panel(watch, watch["active_panel"])["id"]
    if watch.get("platform") == "watch_os":
        result["companion_settings"] = watch.get("companion_settings", {"order": [], "headset": "Joseph's Buds2 Pro"})
    return result


def panel_state(hass, panel):
    if panel.get("type") == "fan":
        state = hass.states.get(panel["fan_entity"])
        return {"state": state.state if state else "unavailable", "attributes": {key: state.attributes[key] for key in ("percentage", "supported_features") if key in state.attributes} if state else {}}
    if panel.get("type") == "driving":
        return driving_state(hass, panel)
    if panel.get("type") == "exercise":
        return exercise_state(hass, panel)
    battery = hass.states.get(panel["battery_entity"])
    percentage = None
    if battery and battery.attributes.get("unit_of_measurement") == "%":
        try:
            number = float(battery.state)
            if math.isfinite(number) and 0 <= number <= 100:
                percentage = round(number)
        except (ValueError, TypeError):
            pass
    climate = hass.states.get(panel["climate_entity"])
    available = climate is not None and climate.state not in ("unknown", "unavailable")
    on = available and climate.state != "off"
    if climate and panel["climate_entity"].startswith("climate."):
        # HA's explicit TURN_OFF (128) and TURN_ON (256) capabilities.
        feature = 128 if on else 256
        available = available and bool(climate.attributes.get("supported_features", 0) & feature)
    script = hass.states.get(panel["script_entity"])
    result = {"battery": percentage, "climate_on": on, "climate_available": available,
              "script_available": script is not None and script.state == "off"}
    if panel.get("wake_entity"):
        awake = hass.states.get(panel["awake_entity"])
        online = awake is not None and awake.state == "on"
        valid_climate = climate is not None and climate.state in (
            ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")
            if panel["climate_entity"].startswith("climate.") else ("on", "off"))
        ready = online and percentage is not None and valid_climate
        result.update(vehicle_ready=ready, vehicle_online=online)
        if not ready:
            result.update(battery=None, climate_on=False, climate_available=False, script_available=False)
    return result


def panel_command(hass, panel, action):
    if panel.get("type") == "fan":
        from . import command
        if panel_state(hass, panel)["state"] not in ("on", "off"):
            raise ValueError("Fan unavailable")
        return command(panel["fan_entity"], action)
    if action == "wake" and panel.get("wake_entity"):
        button = hass.states.get(panel["wake_entity"])
        # Buttons normally have unknown state until first pressed.
        if button is not None and button.state != "unavailable":
            return "button", "press", {"entity_id": panel["wake_entity"]}
        raise ValueError("Wake unavailable")
    if panel.get("type") == "driving":
        return driving_command(hass, panel, action)
    state = panel_state(hass, panel)
    if action in ("climate_on", "climate_off") and state["climate_available"]:
        entity = panel["climate_entity"]
        return entity.split(".")[0], "turn_on" if action == "climate_on" else "turn_off", {"entity_id": entity}
    if action == "leave" and state["script_available"]:
        return "script", "turn_on", {"entity_id": panel["script_entity"]}
    raise ValueError("Panel action unavailable")
