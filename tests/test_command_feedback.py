import asyncio
import copy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from custom_components.fan_control import WatchView
from custom_components.fan_control import vehicle_operations as vo
from custom_components.fan_control.push import PushCoordinator
from .test_vehicle_operations import setup, complete, DRIVING
from .test_api import request
from .test_protocol import packet

@pytest.mark.asyncio
async def test_climate_intent_survives_closed_watch_and_shares_entry_wake(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "wake", asleep=True)
    rt = hass.data["fan_control"]
    vo.handle(hass, rt, wid, watch, body)
    clock[0] += 1
    climate = {**body, "command":"climate_on", "issued_at_ms":clock[0], "operation_id":"12345678-1234-1234-1234-123456789abd"}
    vo.handle(hass, rt, wid, watch, climate)
    async def deliver(domain, name, data, **kwargs):
        assert not rt["lock"].locked()
        if name == "press": states[DRIVING["awake_entity"]].state = "on"
        if name == "turn_on": states[DRIVING["climate_entity"]].state = "cool"
    hass.services.async_call.side_effect = deliver
    # No watch poll, UI task or phone is present after submission.
    op = await complete(hass, wid, climate)
    await complete(hass, wid, body)
    assert op["status"] == "complete"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["press", "turn_on"]
    assert op["timing"]["command_dispatch_ms"] >= op["timing"]["registered_ms"]

@pytest.mark.asyncio
async def test_stale_online_and_climate_cannot_suppress_wake_or_confirm(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "climate_on")
    old = datetime.fromtimestamp(clock[0]/1000,timezone.utc) - timedelta(hours=1)
    states[DRIVING["awake_entity"]].last_reported = old
    states[DRIVING["climate_entity"]].last_reported = old
    states[DRIVING["climate_entity"]].state = "cool"
    assert not vo.online(hass, DRIVING)
    assert not vo.matches(hass, {"panel":DRIVING,"command":"climate_on"})
    assert not vo.observed(hass, DRIVING)["observation_fresh"]
    async def deliver(domain, name, data, **kwargs):
        if name == "press":
            for role in ("awake_entity", "climate_entity"):
                states[DRIVING[role]].last_reported = datetime.fromtimestamp(clock[0]/1000,timezone.utc)
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    assert (await complete(hass,wid,body))["status"] == "complete"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["press"]

@pytest.mark.asyncio
async def test_acceptance_timing_is_server_owned_and_duplicate_preserves_first(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "climate_on")
    body["timing"] = {"received_ms":0,"accepted_ms":0}
    rt=hass.data["fan_control"]
    result=await WatchView(hass).post(request(packet(wid,watch["secret"],body)))
    assert result.status == 200
    op=rt["vehicle_operations"][(wid,body["operation_id"])]
    assert op["timing"]["received_ms"] > 0
    accepted=op["timing"]["accepted_ms"]
    clock[0]+=1
    await WatchView(hass).post(request(packet(wid,watch["secret"],body)))
    assert op["timing"]["accepted_ms"] == accepted
    op["task"].cancel();await asyncio.gather(op["task"],return_exceptions=True)

@pytest.mark.asyncio
async def test_terminal_climate_failure_notifies_once_without_watch_poll(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "climate_on", asleep=True)
    rt=hass.data["fan_control"]
    push=rt["push"]=SimpleNamespace(command_outcome=AsyncMock(return_value={"http_status":202}),status=PushCoordinator.status)
    vo.handle(hass,rt,wid,watch,body)
    op=await complete(hass,wid,body)
    tasks=list(rt["operation_notifications"].values())
    if tasks: await asyncio.gather(*tasks)
    assert clock[0] >= body["issued_at_ms"]+195000
    assert op["status"] == "unconfirmed"
    push.command_outcome.assert_awaited_once()
    vo.queue_outcome(hass,rt,op)
    assert not rt["operation_notifications"]
    assert op["notified_status"] == "unconfirmed"

@pytest.mark.asyncio
async def test_notification_cannot_replay_after_newer_user_intent(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "climate_on")
    rt=hass.data["fan_control"]
    push=rt["push"]=SimpleNamespace(command_outcome=AsyncMock(return_value={"http_status":202}),status=PushCoordinator.status)
    vo.handle(hass,rt,wid,watch,body)
    op=rt["vehicle_operations"][(wid,body["operation_id"])]
    op["task"].cancel();await asyncio.gather(op["task"],return_exceptions=True)
    op["status"]="failed"
    rt["vehicle_latest"][(wid,DRIVING["id"]) ]={"issued":op["issued"]+1}
    vo.queue_outcome(hass,rt,op)
    await asyncio.gather(*list(rt["operation_notifications"].values()))
    push.command_outcome.assert_not_awaited()

@pytest.mark.asyncio
async def test_watch_outcome_registration_is_separate_from_phone(monkeypatch):
    hass, watch, wid, states, clock, body=setup(monkeypatch)
    watch["push_registration"]={"device_token":"b"*64,"environment":"production","destination":"phone"}
    response=await WatchView(hass).post(request(packet(wid,watch["secret"],{"action":"push_register","purpose":"command_outcomes","device_token":"a"*64,"environment":"production"})))
    assert response.status == 200
    assert watch["push_registration"]["destination"] == "phone"
    assert watch["command_push_registration"]["device_token"] == "a"*64
    rt=hass.data["fan_control"];rt["push_settings"]={"enabled":True}
    push=PushCoordinator(hass,rt);push.send=AsyncMock(side_effect=[{"http_status":200},{"http_status":202}])
    op={"id":body["operation_id"],"issued":body["issued_at_ms"],"panel":body["panel_config"],"watch_id":wid,"secret":watch["secret"],"owner":watch["owner_id"],"status":"failed"}
    assert (await push.command_outcome(op))["http_status"] == 202
    assert push.send.await_args_list[0].args[0]["watch_id"] == wid+".commands"
    assert push.send.await_args_list[0].args[0]["destination"] == "watch"
    assert push.send.await_args_list[1].args[0]["status"] == "failed"
    assert "device_token" not in push.send.await_args_list[1].args[0]

@pytest.mark.asyncio
async def test_cosmetic_panel_changes_do_not_cancel_valid_entry(monkeypatch):
    hass,watch,wid,states,clock,body=setup(monkeypatch,DRIVING,"wake")
    watch["panels"][0]["name"]="Renamed car"
    vo.handle(hass,hass.data["fan_control"],wid,watch,body)
    assert (await complete(hass,wid,body))["status"] == "complete"


def test_timing_events_are_bounded_and_do_not_include_credentials():
    op={}
    for i in range(100): vo.event(op,"a" if i%2 else "b",service="turn_on")
    assert len(op["events"]) == 40
    assert set(op["timing"]) == {"a_ms","b_ms"}
    assert set(op["events"][0]) == {"phase","at_ms","service"}

@pytest.mark.asyncio
async def test_legacy_watch_registration_cannot_replace_phone_snapshot_route(monkeypatch):
    hass,watch,wid,states,clock,body=setup(monkeypatch)
    watch["push_registration"]={"device_token":"b"*64,"environment":"production","destination":"phone"}
    response=await WatchView(hass).post(request(packet(wid,watch["secret"],{"action":"push_register","device_token":"a"*64,"environment":"production"})))
    assert response.status == 200
    assert watch["push_registration"]["destination"] == "phone"
    assert watch["push_registration"]["device_token"] == "b"*64

@pytest.mark.asyncio
async def test_notification_retry_rechecks_supersession_after_registration(monkeypatch):
    hass,watch,wid,states,clock,body=setup(monkeypatch)
    rt=hass.data["fan_control"];rt["push_settings"]={"enabled":True}
    watch["command_push_registration"]={"device_token":"a"*64,"environment":"production"}
    push=PushCoordinator(hass,rt)
    op={"id":body["operation_id"],"issued":body["issued_at_ms"],"panel":body["panel_config"],"watch_id":wid,"secret":watch["secret"],"owner":watch["owner_id"],"status":"failed"}
    calls=[]
    async def send(payload):
        calls.append(payload)
        rt.setdefault("vehicle_latest",{})[(wid,op["panel"]["id"]) ]={"issued":op["issued"]+1}
        return {"http_status":200}
    push.send=send
    assert (await push.command_outcome(op))["http_status"] == 409
    assert len(calls) == 1
