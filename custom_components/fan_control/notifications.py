"""Automation alerts to a paired watch's linked iPhone; no device commands."""
try:
    import probatio as vol
except ImportError:
    import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


def notification_text(value, limit):
    """Bound UTF-8 bytes so APNs payloads fit."""
    if (not isinstance(value, str) or not value.strip()
            or any(ord(c) < 32 and c not in "\n\t" for c in value)):
        raise vol.Invalid("Notification text must not be blank or contain control characters")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise vol.Invalid("Invalid notification text") from error
    if size > limit:
        raise vol.Invalid(f"Notification text exceeds {limit} UTF-8 bytes")
    return value


SCHEMA = vol.Schema({
    vol.Required("device_id"): str,
    vol.Optional("title", default="Fan Control"): lambda value: notification_text(value, 200),
    vol.Required("message"): lambda value: notification_text(value, 1500),
})


def register_service(hass, runtime):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers.service import async_register_admin_service

    def recipient(call):
        device = dr.async_get(hass).async_get(call.data["device_id"])
        identities = [value for domain, value in device.identifiers if domain == DOMAIN] if device else []
        if (device is None or runtime["entry"].entry_id not in device.config_entries
                or len(identities) != 1):
            raise HomeAssistantError("Select a Fan Control Apple Watch with a linked iPhone")
        if runtime["manager"].watches.get(identities[0], {}).get("platform") != "watch_os":
            raise HomeAssistantError("Select a Fan Control Apple Watch")
        return identities[0]

    async def send_notification(call):
        await runtime["push"].notify_phone(recipient(call), call.data["title"], call.data["message"])

    async_register_admin_service(hass, DOMAIN, "send_notification", send_notification, schema=SCHEMA)
