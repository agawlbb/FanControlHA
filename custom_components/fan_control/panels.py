"""Typed, user-configured panels. Entity permissions remain server-side."""
import math
import re


def validate_panel(value):
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
    return result


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
    return result


def panel_state(hass, panel):
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
    return {"battery": percentage, "climate_on": on, "climate_available": available,
            "script_available": script is not None and script.state == "off"}


def panel_command(hass, panel, action):
    state = panel_state(hass, panel)
    if action in ("climate_on", "climate_off") and state["climate_available"]:
        entity = panel["climate_entity"]
        return entity.split(".")[0], "turn_on" if action == "climate_on" else "turn_off", {"entity_id": entity}
    if action == "leave" and state["script_available"]:
        return "script", "turn_on", {"entity_id": panel["script_entity"]}
    raise ValueError("Panel action unavailable")
