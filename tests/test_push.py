import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.fan_control.push import PushCoordinator, dependencies, registration
from custom_components.fan_control import WatchView
from .test_api import runtime, request
from .test_protocol import packet


def setup_push():
    hass, manager, watch_id, _ = runtime()
    watch = manager.watches[watch_id]
    watch.update(platform="watch_os", push_registration={"device_token":"a"*64,"environment":"production"})
    data = hass.data["fan_control"]
    data["push_settings"] = {"enabled":True,"team_id":"SYNTHETIC1","key_id":"SYNTHETIC2"}
    hass.async_create_background_task = lambda coro, name: asyncio.create_task(coro)
    push = data["push"] = PushCoordinator(hass, data)
    push.send = AsyncMock(return_value=202)
    return hass, manager, watch_id, push


def test_signed_protocol_and_registration():
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
    attempt = watch["push_history"][-1]
    assert push.send.call_args.args[0] == watch["push_registration"]
    assert push.send.call_args.args[1] == {"aps":{"content-available":1}, "fancontrol":{"refresh":True,"watch_id":watch_id,"update_id":attempt["update_id"]}}
    assert push.send.call_args.kwargs["push_type"] == "background"
    assert attempt["panel"] == "panel.white" and attempt["receipt"] == "unknown"
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
    assert manager.watches[watch_id]["push_history"][-1]["reason"] == "active_panel_state"
    push.send.reset_mock()
    push.queue(watch_id,"active_panel_state")
    del manager.watches[watch_id]
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.assert_not_awaited()
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
async def test_revoked_during_attempt_persistence_never_gets_refresh():
    hass, manager, watch_id, push = setup_push()
    async def persist():
        manager.watches.clear()
    push.persist = persist
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.assert_not_awaited()
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_apple_settings_persist_across_configuration_and_stop_selection(monkeypatch):
    from custom_components.fan_control.config_flow import FanControlOptions
    from custom_components.fan_control import save
    from custom_components.fan_control.exercise import select_driving_after_stop
    from .test_stop_driving import setup
    hass, _, watch, data, key, op = setup()
    flow = FanControlOptions(); flow.hass = hass
    from custom_components.fan_control import apns
    monkeypatch.setattr(apns, "credentials", lambda *args: object())
    settings = {"enabled": True, "team_id":"SYNTHETIC1", "key_id":"SYNTHETIC2"}
    assert (await flow.async_step_push(settings))["type"] == "create_entry"
    await save(data)
    assert data["store"].async_save.call_args.args[0]["push_settings"] == settings
    await select_driving_after_stop(hass,data,watch["panels"][0],key,op,{"operation_status":"complete"})
    assert data["store"].async_save.call_args.args[0]["push_settings"] == settings
    assert (await flow.async_step_push({"enabled":True,"team_id":"SYNTHETIC1"}))["type"] == "create_entry"
    assert data["push_settings"]["key_id"] == settings["key_id"]
    monkeypatch.setattr(apns, "credentials", lambda *args: (_ for _ in ()).throw(ValueError("invalid key")))
    assert (await flow.async_step_push({"enabled":True,"team_id":"bad/name"}))["errors"]
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_changing_apple_settings_cancels_old_delivery():
    from custom_components.fan_control.config_flow import FanControlOptions
    hass, manager, watch_id, push = setup_push()
    started = asyncio.Event()
    async def send(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    push.send = send
    push.updated()
    old_task = push.tasks[watch_id]
    await started.wait()
    flow = FanControlOptions(); flow.hass = hass
    await flow.async_step_push({"enabled":False,"team_id":"SYNTHETIC3","key_id":"SYNTHETIC4"})
    assert old_task.cancelled()
    assert not push.tasks and not push.pending and push.apns.cached is None
    assert push.settings["team_id"] == "SYNTHETIC3"


@pytest.mark.asyncio
async def test_enabling_or_changing_signing_settings_queues_one_fresh_snapshot():
    hass, manager, watch_id, push = setup_push()
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.reset_mock()
    await push.reset_delivery()
    push.updated()
    await asyncio.gather(*list(push.tasks.values()))
    push.send.assert_awaited_once()
    assert push.send.call_args.kwargs["push_type"] == "background"
    await push.close()
