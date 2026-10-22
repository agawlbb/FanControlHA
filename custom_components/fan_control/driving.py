"""Role-scoped Driving controls; no toggle services or guessed observed state."""
import re
from .panel_color import add_color


def validate_driving(value):
    result = {"type": "driving"}
    for key, default in (("id", "driving"), ("name", "Driving")):
        text = value.get(key, default)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 40:
            raise ValueError("Invalid Driving label")
        result[key] = text.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", result["id"]) or result["id"] == "devices":
        raise ValueError("Invalid Driving ID")
    for role, domain in (("garage_entity", "cover"), ("trunk_entity", "cover"),
                         ("climate_entity", "climate"), ("wake_entity", "button"),
                         ("awake_entity", "binary_sensor")):
        entity = value.get(role)
        if not isinstance(entity, str) or len(entity) > 160 or not re.fullmatch(domain + r"\.[a-z0-9_]+", entity):
            raise ValueError("Invalid Driving entity role")
        result[role] = entity
    if result["garage_entity"] == result["trunk_entity"]:
        raise ValueError("Garage and trunk must be distinct")
    horn = value.get("horn_entity")
    if horn:
        if not isinstance(horn, str) or len(horn) > 160 or not re.fullmatch(r"button\.[a-z0-9_]+", horn) or horn == result["wake_entity"]:
            raise ValueError("Invalid horn role")
        result["horn_entity"] = horn
    podcast = value.get("podcast_entity")
    if podcast:
        if not isinstance(podcast, str) or len(podcast) > 160 or not re.fullmatch(r"script\.[a-z0-9_]+", podcast):
            raise ValueError("Invalid podcast script role")
        result["podcast_entity"] = podcast
    return add_color(result, value)


def driving_state(hass, panel):
    online = hass.states.get(panel["awake_entity"])
    awake = online is not None and online.state == "on"
    result = {"vehicle_ready": awake}
    for role in ("garage", "trunk"):
        cover = hass.states.get(panel[role + "_entity"])
        reachable = cover is not None and cover.state != "unavailable" and (role == "garage" or awake)
        observed = cover.state if reachable and cover.state in ("open", "closed", "opening", "closing") else "unknown"
        features = cover.attributes.get("supported_features", 0) if cover else 0
        result[role + "_state"] = observed
        result[role + "_open_available"] = reachable and bool(features & 1)
        result[role + "_close_available"] = reachable and bool(features & 2)
    climate = hass.states.get(panel["climate_entity"])
    valid = awake and climate is not None and climate.state in ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only")
    result["climate_on"] = valid and climate.state != "off"
    result["climate_available"] = valid and bool(climate.attributes.get("supported_features", 0) & 256)
    result["climate_off_available"] = valid and bool(climate.attributes.get("supported_features", 0) & 128)
    horn = hass.states.get(panel.get("horn_entity", ""))
    # Button state is the last press timestamp (or unknown before its first press).
    result["honk_available"] = awake and horn is not None and horn.state != "unavailable"
    podcast = hass.states.get(panel.get("podcast_entity", ""))
    result["podcast_available"] = podcast is not None and podcast.state == "off"
    return result


def driving_command(hass, panel, action):
    state = driving_state(hass, panel)
    for role in ("garage", "trunk"):
        for direction in ("open", "close"):
            if action == role + "_" + direction and state[action + "_available"]:
                return "cover", direction + "_cover", {"entity_id": panel[role + "_entity"]}
    if action == "climate_on" and state["climate_available"]:
        return "climate", "turn_on", {"entity_id": panel["climate_entity"]}
    if action == "climate_off" and state["climate_off_available"]:
        return "climate", "turn_off", {"entity_id": panel["climate_entity"]}
    if action == "honk" and state["honk_available"]:
        return "button", "press", {"entity_id": panel["horn_entity"]}
    if action == "podcast" and state["podcast_available"]:
        return "script", "turn_on", {"entity_id": panel["podcast_entity"]}
    raise ValueError("Driving action unavailable")
