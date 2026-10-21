import json
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from custom_components.fan_control import WatchView
from custom_components.fan_control.protocol import aad, decode
from .test_api import runtime, request
from .test_protocol import packet

def unpack(response, value, secret):
    envelope = json.loads(response.text)
    return json.loads(AESGCM(bytes.fromhex(secret)).decrypt(decode(envelope["iv"]), decode(envelope["ciphertext"], 262144), aad(value) + b":response"))

@pytest.mark.asyncio
async def test_companion_key_permissions_and_revocation():
    hass, manager, wid, _ = runtime()
    watch = manager.watches[wid]; watch["platform"] = "watch_os"
    view = WatchView(hass); secret = watch["secret"]
    value = packet(wid, secret, {"action": "companion_grant"})
    response = await view.post(request(value)); assert response.status == 200
    grant = unpack(response, value, secret); phone = grant["token"]
    assert phone != secret and grant["companion"] is True
    for action in ("command", "panel_command", "companion_grant", "config", "companion_revoke"):
        value = {**packet(wid, phone, {"action": action}), "credential": "companion"}
        assert (await view.post(request(value))).status == 403
    value = {**packet(wid, phone, {"action": "companion_snapshot"}), "credential": "companion"}
    response = await view.post(request(value)); assert response.status == 200
    snapshot = unpack(response, value, phone)
    assert snapshot["states"]["fan.example"]["state"] == "on"
    assert secret not in json.dumps(snapshot) and phone not in json.dumps(snapshot)
    assert (await view.post(request(value))).status == 403
    hass.services.async_call.assert_not_awaited()
    assert (await view.post(request(packet(wid, secret, {"action": "companion_revoke"})))).status == 200
    value = {**packet(wid, phone, {"action": "companion_snapshot"}), "credential": "companion"}
    assert (await view.post(request(value))).status == 403

@pytest.mark.asyncio
async def test_settings_revision_order_and_no_vehicle_commands():
    hass, manager, wid, _ = runtime(); watch = manager.watches[wid]; watch["platform"] = "watch_os"
    watch["devices"].append({"entity": "light.example", "title": "Light"})
    secret = watch["secret"]; view = WatchView(hass)
    async def call(body):
        value = packet(wid, secret, body); response = await view.post(request(value))
        return response.status, unpack(response, value, secret) if response.status == 200 else None
    settings = {"order": ["light.example", "fan.example"], "headset": "My headphones"}
    status, first = await call({"action": "companion_snapshot"}); assert status == 200
    status, saved = await call({"action": "companion_settings", "revision": 1, "settings": settings})
    assert status == 200 and saved["configuration"]["companion_settings"] == settings
    assert saved["sequence"] > first["sequence"] and watch["entity"] == "fan.example"
    assert (await call({"action": "companion_settings", "revision": 1, "settings": settings}))[0] == 403
    assert (await call({"action": "companion_settings", "revision": 2, "settings": {**settings, "order": ["fan.other"]}}))[0] == 403
    assert (await call({"action": "companion_push_register", "device_token": "a" * 64, "environment": "sandbox"}))[0] == 200
    assert watch["push_registration"]["destination"] == "phone"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_wear_os_cannot_grant_companion():
    hass, manager, wid, _ = runtime()
    result = await WatchView(hass).post(request(packet(wid, manager.watches[wid]["secret"], {"action": "companion_grant"})))
    assert result.status == 403 and "companion" not in manager.watches[wid]
