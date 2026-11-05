"""Read-only flight presentation; FlightSync owns flight selection."""
import re
from datetime import datetime


def validate_on_duty(value):
    result = {"type": "on_duty"}
    for key, default in (("id", "on_duty"), ("name", "On Duty")):
        text = value.get(key, default)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 40:
            raise ValueError("Invalid panel label")
        result[key] = text.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", result["id"]) or result["id"] == "devices":
        raise ValueError("Invalid panel ID")
    for role in ("city_pair_entity", "departure_entity"):
        entity = value.get(role)
        if not isinstance(entity, str) or len(entity) > 160 or not re.fullmatch(r"sensor\.[a-z0-9_]+", entity):
            raise ValueError("Select flight sensors")
        result[role] = entity
    return result


def on_duty_state(hass, panel):
    pair = hass.states.get(panel["city_pair_entity"])
    departure = hass.states.get(panel["departure_entity"])
    city_pair = pair.state if pair and pair.state not in ("unknown", "unavailable", "") else None
    timestamp = None
    if departure and departure.state not in ("unknown", "unavailable", ""):
        try:
            parsed = datetime.fromisoformat(departure.state.replace("Z", "+00:00"))
            if parsed.utcoffset() is not None:
                timestamp = parsed.isoformat()
        except ValueError:
            pass
    return {"city_pair": city_pair, "departure": timestamp,
            "available": city_pair is not None and timestamp is not None}
