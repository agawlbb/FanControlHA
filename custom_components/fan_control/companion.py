"""Apple companion permissions: observations/preferences, never physical commands."""
import secrets
import time

from .panels import configuration, panel_state
from .push import registration


def handle(hass, watch, body):
    if watch.get("platform") != "watch_os":
        raise ValueError("Apple Watch required")
    action = body["action"]
    if action == "companion_grant":
        # Grant only through the independently authenticated watch. Explicit
        # unlink rotates this credential; never export the watch command key.
        linked = watch.setdefault("companion", {"secret": secrets.token_hex(32)})
        return {**configuration(watch), "token": linked["secret"], "companion": True}
    if action in ("companion_revoke", "companion_unlink"):
        watch.pop("companion", None)
        if watch.get("push_registration", {}).get("destination") == "phone":
            watch.pop("push_registration", None)
        return {"status": "revoked"}
    if action == "companion_push_register":
        watch["push_registration"] = {**registration(body), "destination": "phone"}
        return {"status": "registered_with_ha"}
    if action == "companion_settings":
        config = configuration(watch)
        if body.get("revision") != watch["revision"]:
            raise ValueError("Configuration changed; refresh first")
        settings = body.get("settings")
        allowed = {d["entity"] for d in config["devices"]}
        if not isinstance(settings, dict) or set(settings) != {"order", "headset"}:
            raise ValueError("Invalid settings")
        order, headset = settings["order"], settings["headset"]
        if (not isinstance(order, list) or len(order) != len(allowed)
                or any(not isinstance(v, str) for v in order) or set(order) != allowed
                or not isinstance(headset, str) or not headset.strip() or len(headset) > 80):
            raise ValueError("Invalid settings")
        watch["companion_settings"] = {"order": list(order), "headset": headset.strip()}
        watch["revision"] += 1
    config = configuration(watch)
    states = {}
    for device in config["devices"]:
        if device.get("panel"):
            states[device["entity"]] = panel_state(hass, device["panel"])
        else:
            state = hass.states.get(device["entity"])
            states[device["entity"]] = {"state": state.state if state else "unavailable", "attributes": {
                key: state.attributes[key] for key in ("percentage", "supported_features")
                if state and key in state.attributes}}
    # Sequence is persisted with the watch and orders observations independently
    # of configuration revision and phone/watch clock differences.
    watch["companion_sequence"] = watch.get("companion_sequence", 0) + 1
    return {"configuration": config, "states": states, "sequence": watch["companion_sequence"],
            "at_ms": int(time.time() * 1000)}
