"""HA's Download diagnostics exposes push history without pairing/signing secrets."""
from .const import DOMAIN
from .apns import configured
from .panels import configuration


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = hass.data.get(DOMAIN, {})
    watches = []
    for watch_id, watch in runtime.get("manager").watches.items() if runtime.get("manager") else []:
        config = configuration(watch)
        watches.append({"watch_id": watch_id, "name": watch.get("name"), "platform": watch.get("platform"),
                        "active_panel": config["entity"], "revision": config["revision"],
                        "registered_with_ha": bool(watch.get("push_registration")),
                        "push_destination": watch.get("push_registration", {}).get("destination", "watch"),
                        "companion_linked": bool(watch.get("companion")),
                        "push_history": watch.get("push_history", []),
                        "uncorrelated_watch_events": watch.get("push_client_events", []),
                        "vehicle_operations": [{"panel_id": op["panel"]["id"], **{key: op[key] for key in ("id", "issued", "command", "status", "stage", "finished_ms", "reason", "timing", "events", "notification_result", "notified_status") if key in op}}
                            for op in runtime.get("vehicle_operations", {}).values() if op["watch_id"] == watch_id]})
    return {"push_transport": "direct_apns", "push_enabled": configured(runtime.get("push_settings", {})),
            "receipt_unknown_means": "No watch receipt reported; not proof of nondelivery. History is bounded to 120 attempts per watch.",
            "watches": watches}
