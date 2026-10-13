import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from custom_components.fan_control import PairView, WatchView, async_setup_entry, command, read_body
from custom_components.fan_control.manager import Manager
from custom_components.fan_control.config_flow import FanControlOptions
from custom_components.fan_control.select import WatchSelection
from .test_protocol import CONFIG, packet, session
from cryptography.hazmat.primitives.asymmetric import rsa

class Content:
    def __init__(self, raw): self.raw = raw
    async def iter_chunked(self, count):
        for i in range(0, len(self.raw), 7): yield self.raw[i:i+7]

def request(body, headers=None):
    raw = json.dumps(body).encode()
    return SimpleNamespace(content_type="application/json", content_length=None, content=Content(raw), remote="127.0.0.1", headers=headers or {})

def runtime():
    manager = Manager(); keys = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    created = session(manager, keys); watch_id = manager.approve(created["code"], CONFIG)
    store = SimpleNamespace(async_save=AsyncMock())
    data = {"manager": manager, "store": store, "lock": asyncio.Lock(), "attempts": {}}
    state = SimpleNamespace(state="on", attributes={"percentage": 66, "supported_features": 1, "private_attribute": "hidden"})
    owner = SimpleNamespace(id="example-owner", name="Administrator", is_admin=True, is_active=True)
    hass = SimpleNamespace(data={"fan_control": data}, auth=SimpleNamespace(async_get_user=AsyncMock(return_value=owner), async_get_users=AsyncMock(return_value=[owner])), states=SimpleNamespace(get=lambda entity: state), services=SimpleNamespace(async_call=AsyncMock()))
    return hass, manager, watch_id, created

@pytest.mark.asyncio
async def test_chunked_and_oversized_body():
    assert await read_body(request({"action": "config"})) == {"action": "config"}
    with pytest.raises(ValueError): await read_body(request({"large": "x" * 25000}))

@pytest.mark.asyncio
async def test_encrypted_api_allowlist_and_replay():
    hass, manager, watch_id, created = runtime()
    view = WatchView(hass); secret = manager.watches[watch_id]["secret"]
    value = packet(watch_id, secret, {"action": "command", "entity": "fan.example", "command": "speed4"})
    result = await view.post(request(value)); assert result.status == 200
    hass.services.async_call.assert_awaited_once_with("fan", "set_percentage", {"entity_id": "fan.example", "percentage": 66}, blocking=True, context={"user_id": "example-owner"})
    assert (await view.post(request(value))).status == 403
    assert (await view.post(request(packet(watch_id, secret, {"action": "command", "entity": "fan.other", "command": "toggle"})))).status == 403
    assert hass.services.async_call.await_count == 1
    assert "fan.example" not in result.text
    assert hass.data["fan_control"]["store"].async_save.await_count == 2

@pytest.mark.asyncio
async def test_disabled_owner_and_unloaded_integration():
    hass, manager, watch_id, created = runtime(); hass.auth.async_get_user.return_value.is_active = False
    value = packet(watch_id, manager.watches[watch_id]["secret"], {"action": "config"})
    assert (await WatchView(hass).post(request(value))).status == 403
    hass.data.clear(); assert (await WatchView(hass).post(request(value))).status == 503

@pytest.mark.asyncio
async def test_pair_poll_and_rate_limits():
    hass, manager, watch_id, created = runtime()
    view = PairView(hass, "poll")
    assert (await view.post(request({}, {"X-Fan-Control-Pairing": created["device_token"]}))).status == 200
    for _ in range(29): await view.post(request({}))
    assert (await view.post(request({}))).status == 429

@pytest.mark.parametrize("speed,percentage", [(1,16),(2,33),(3,50),(4,66),(5,83),(6,100)])
def test_original_fan_mapping(speed, percentage):
    assert command("fan.example", f"speed{speed}")[2]["percentage"] == percentage

@pytest.mark.parametrize("entity,action", [("lock.example", "unlock"), ("fan.example", "speed7"), ("script.example", "toggle"), ("light.example", "activate")])
def test_unapproved_commands(entity, action):
    with pytest.raises(ValueError): command(entity, action)

@pytest.mark.asyncio
async def test_native_pairing_flow_and_revoke():
    hass, manager, _, _ = runtime()
    created = session(manager, rsa.generate_private_key(public_exponent=65537, key_size=2048))
    flow = FanControlOptions(); flow.hass = hass
    assert (await flow.async_step_init())["type"] == "menu"
    assert (await flow.async_step_pair({"code": created["code"]}))["step_id"] == "confirm"
    assert (await flow.async_step_confirm({"matches": False}))["step_id"] == "confirm"
    assert (await flow.async_step_confirm({"matches": True}))["step_id"] == "devices"
    result = await flow.async_step_devices({"name": "Example", "entities": ["fan.example", "light.example"], "active_entity": "light.example", "local_url": "http://192.168.1.2:8123", "remote_url": "https://ha.example.com", "owner_id": "example-owner"})
    assert result["type"] == "create_entry"
    assert manager.watches[flow.watch_id]["entity"] == "light.example"
    assert (await flow.async_step_revoke({"watch_id": flow.watch_id}))["type"] == "create_entry"
    assert flow.watch_id not in manager.watches

@pytest.mark.asyncio
async def test_selector_changes_configuration_without_device_service():
    hass, manager, watch_id, _ = runtime()
    manager.watches[watch_id]["devices"].append({"entity": "light.example", "title": "Light"})
    selection = WatchSelection(hass, watch_id)
    assert selection.available and selection.current_option == "fan.example"
    await selection.async_select_option("light.example")
    assert selection.current_option == "light.example"
    assert manager.watches[watch_id]["revision"] == 2
    hass.services.async_call.assert_not_awaited()
    from homeassistant.exceptions import HomeAssistantError
    with pytest.raises(HomeAssistantError): await selection.async_select_option("fan.other")
    del manager.watches[watch_id]
    assert not selection.available and selection.options == [] and selection.current_option is None
