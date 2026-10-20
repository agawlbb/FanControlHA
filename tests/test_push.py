import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.fan_control.push import PushCoordinator, dependencies, headers, registration
from custom_components.fan_control import WatchView
from .test_api import runtime, request
from .test_protocol import packet


def setup_push():
    hass, manager, watch_id, _ = runtime()
    watch = manager.watches[watch_id]
    watch.update(platform="watch_os", push_registration={"device_token":"a"*64,"environment":"production"})
    data = hass.data["fan_control"]
    data["push_settings"] = {"enabled":True,"installation":"example","secret":"x"*64}
    hass.async_create_background_task = lambda coro, name: asyncio.create_task(coro)
    push = data["push"] = PushCoordinator(hass, data)
    push.send = AsyncMock(return_value=202)
    push.send.side_effect = lambda body: 200 if body["action"] == "register" else 202
    return hass, manager, watch_id, push


def test_signed_protocol_and_registration():
    raw = b'{"action":"refresh"}'
    settings = {"installation":"example","secret":"x"*64}
    value = headers(settings, raw, 1800000000, "a"*32)
    assert value["X-FanControl-Signature"] == hmac.new(b"x"*64,b"1800000000\n"+b"a"*32+b"\n"+raw, hashlib.sha256).hexdigest()
    assert registration({"device_token":"A"*64,"environment":"production"})["device_token"] == "a"*64
    for body in ({}, {"device_token":"z"*64,"environment":"production"}, {"device_token":"a"*64,"environment":"arbitrary-host"}):
        with pytest.raises(ValueError): registration(body)


@pytest.mark.asyncio
async def test_inactive_car_and_irrelevant_attributes_do_not_push():
    hass, manager, watch_id, push = setup_push()
    watch = manager.watches[watch_id]
    watch["panels"] = [{"type":"work_morning","id":"white","name":"White",
                        "battery_entity":"sensor.white_battery","climate_entity":"climate.white",
                        "script_entity":"script.white","wake_entity":"button.white","awake_entity":"binary_sensor.white"}]
    assert dependencies(watch) == {"fan.example"}
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.reset_mock()
    push.state_changed(SimpleNamespace(data={"entity_id":"climate.white"}))
    push.state_changed(SimpleNamespace(data={"entity_id":"fan.example"}))
    assert not push.tasks
    watch["active_panel"] = "white"
    watch["revision"] += 1
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    assert push.send.call_args.args[0] == {"action":"refresh","watch_id":watch_id,"reason":"selection"}
    assert "button.white" not in dependencies(watch)
    hass.services.async_call.assert_not_awaited()
    await push.close()


@pytest.mark.asyncio
async def test_state_bursts_coalesce_and_revocation_wins():
    hass, manager, watch_id, push = setup_push()
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.reset_mock()
    for value in (16,33,50):
        hass.states.get("fan.example").attributes["percentage"] = value
        push.state_changed(SimpleNamespace(data={"entity_id":"fan.example"}))
    assert len(push.tasks) == 1
    await asyncio.gather(*list(push.tasks.values()))
    assert push.send.await_count == 1
    assert push.send.call_args.args[0]["reason"] == "active_panel_state"
    push.send.reset_mock()
    push.queue(watch_id,"active_panel_state")
    del manager.watches[watch_id]
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    assert push.send.call_args.args[0]["action"] == "unregister"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_relay_and_disabled_owner():
    hass, manager, watch_id, push = setup_push()
    hass.data["fan_control"]["push_settings"]["enabled"] = False
    push.updated()
    assert not push.tasks
    hass.data["fan_control"]["push_settings"]["enabled"] = True
    hass.auth.async_get_user.return_value.is_active = False
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.assert_not_awaited()
    await push.close()


@pytest.mark.asyncio
async def test_only_authenticated_apple_watch_can_register():
    hass, manager, watch_id, _ = runtime()
    secret = manager.watches[watch_id]["secret"]
    body = {"action":"push_register","device_token":"a"*64,"environment":"production"}
    assert (await WatchView(hass).post(request(packet(watch_id,secret,body)))).status == 403
    manager.watches[watch_id]["platform"] = "watch_os"
    assert (await WatchView(hass).post(request(packet(watch_id,secret,body)))).status == 200
    assert manager.watches[watch_id]["push_registration"] == registration(body)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_during_registration_never_gets_refresh():
    hass, manager, watch_id, push = setup_push()
    calls = []
    async def send(body):
        calls.append(body)
        if body["action"] == "register":
            del manager.watches[watch_id]
            push.updated()
            return 200
        return 200
    push.send = send
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    assert [body["action"] for body in calls] == ["register", "unregister"]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_settings_persist_across_configuration_and_stop_selection():
    from custom_components.fan_control.config_flow import FanControlOptions
    from custom_components.fan_control import save
    from custom_components.fan_control.exercise import select_driving_after_stop
    from .test_stop_driving import setup
    hass, _, watch, data, key, op = setup()
    flow = FanControlOptions(); flow.hass = hass
    settings = {"enabled": True, "installation":"example", "secret":"x"*64}
    assert (await flow.async_step_push(settings))["type"] == "create_entry"
    await save(data)
    assert data["store"].async_save.call_args.args[0]["push_settings"] == settings
    await select_driving_after_stop(hass,data,watch["panels"][0],key,op,{"operation_status":"complete"})
    assert data["store"].async_save.call_args.args[0]["push_settings"] == settings
    assert (await flow.async_step_push({"enabled":True,"installation":"example"}))["type"] == "create_entry"
    assert data["push_settings"]["secret"] == settings["secret"]
    assert (await flow.async_step_push({"enabled":True,"installation":"bad/name"}))["errors"]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_changing_relay_settings_cancels_old_delivery():
    from custom_components.fan_control.config_flow import FanControlOptions
    hass, manager, watch_id, push = setup_push()
    started = asyncio.Event()
    async def send(body):
        started.set()
        await asyncio.Event().wait()
    push.send = send
    push.updated()
    old_task = push.tasks[watch_id]
    await started.wait()
    flow = FanControlOptions(); flow.hass = hass
    await flow.async_step_push({"enabled":False,"installation":"replacement","secret":"z"*64})
    assert old_task.cancelled()
    assert not push.tasks and not push.registered and not push.pending
    assert push.settings["installation"] == "replacement"
