import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from custom_components.fan_control import vehicle_operations as vo
from .test_vehicle_operations import setup, complete, DRIVING

@pytest.mark.asyncio
async def test_persist_before_dispatch_and_duplicate_after_restart(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "trunk_open")
    rt = hass.data["fan_control"]
    store = rt["operation_store"] = SimpleNamespace(async_save=AsyncMock())
    vo.handle(hass, rt, wid, watch, body)
    op = rt["vehicle_operations"][(wid, body["operation_id"])]
    await asyncio.sleep(0)
    hass.services.async_call.assert_not_awaited()
    await vo.persist(rt)
    op["gate"].set()
    async def deliver(*args, **kwargs):
        saved = store.async_save.await_args.args[0]
        assert saved["operations"][0]["sent"] is True
        raise RuntimeError("provider response lost")
    hass.services.async_call.side_effect = deliver
    await complete(hass, wid, body)
    assert hass.services.async_call.await_count == 1
    saved = copy.deepcopy(store.async_save.await_args.args[0])
    saved["operations"][0]["status"] = "delivering"  # crash after sent marker
    rt["vehicle_operations"] = {}; rt["vehicle_latest"] = {}
    await vo.restore(hass, rt, saved)
    await complete(hass, wid, body)
    vo.handle(hass, rt, wid, watch, body)
    assert hass.services.async_call.await_count == 1

@pytest.mark.asyncio
async def test_generic_absolute_retry_and_durable_acceptance(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    body = {**body, "action":"operation_submit", "entity":"fan.example", "command":"turn_off"}
    state = SimpleNamespace(state="on", attributes={})
    states["fan.example"] = state
    calls = []
    async def deliver(domain, name, data, **kwargs):
        assert not rt["lock"].locked()
        calls.append(name)
        if len(calls) == 1: raise RuntimeError("temporary transport error")
        state.state = "off"
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, rt, wid, watch, body)
    op = await complete(hass, wid, body)
    assert op["status"] == "complete"
    assert calls == ["turn_off", "turn_off"]

@pytest.mark.asyncio
async def test_cancel_before_submit_tombstone_survives_restart(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    rt["operation_store"] = SimpleNamespace(async_save=AsyncMock())
    vo.handle(hass, rt, wid, watch, {"action":"operation_cancel", "operation_id":body["operation_id"]})
    await vo.persist(rt)
    saved = rt["operation_store"].async_save.await_args.args[0]
    rt["operation_cancellations"] = {}
    await vo.restore(hass, rt, saved)
    assert vo.handle(hass, rt, wid, watch, body)["operation_status"] == "cancelled"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_speed_quiet_window_supersession(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    body = {**body, "entity":"fan.example", "command":"speed2", "not_before_ms":clock[0]+1000}
    states["fan.example"] = SimpleNamespace(state="on", attributes={"percentage":16})
    vo.handle(hass, rt, wid, watch, body)
    first = rt["vehicle_operations"][(wid, body["operation_id"])]
    clock[0] += 1
    second = {**body, "operation_id":"12345678-1234-1234-1234-123456789abd", "command":"speed3", "issued_at_ms":clock[0], "not_before_ms":clock[0]+1000}
    vo.handle(hass, rt, wid, watch, second)
    async def deliver(domain, name, data, **kwargs):
        states["fan.example"].attributes["percentage"] = data["percentage"]
    hass.services.async_call.side_effect = deliver
    await complete(hass, wid, second)
    await asyncio.gather(first["task"], return_exceptions=True)
    assert hass.services.async_call.await_count == 1
    assert hass.services.async_call.await_args.args[2]["percentage"] == 50

@pytest.mark.asyncio
async def test_storage_failure_cannot_dispatch_before_acceptance(monkeypatch):
    from custom_components.fan_control import WatchView
    from .test_api import request
    from .test_protocol import packet
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    rt["operation_store"] = SimpleNamespace(async_save=AsyncMock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        await WatchView(hass).post(request(packet(wid, watch["secret"], body)))
    await asyncio.sleep(0)
    hass.services.async_call.assert_not_awaited()
    op = rt["vehicle_operations"][(wid, body["operation_id"])]
    op["task"].cancel()
    await asyncio.gather(op["task"], return_exceptions=True)

@pytest.mark.asyncio
async def test_restart_resumes_accepted_absolute_intent(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    rt["operation_store"] = SimpleNamespace(async_save=AsyncMock())
    vo.handle(hass, rt, wid, watch, body)
    await vo.persist(rt)
    saved = copy.deepcopy(rt["operation_store"].async_save.await_args.args[0])
    original = rt["vehicle_operations"][(wid, body["operation_id"])]
    original["task"].cancel()
    await asyncio.gather(original["task"], return_exceptions=True)
    rt["vehicle_operations"] = {}; rt["vehicle_latest"] = {}
    async def deliver(domain, name, data, **kwargs):
        if name == "lock": states[body["panel_config"]["lock_entity"]].state = "locked"
    hass.services.async_call.side_effect = deliver
    await vo.restore(hass, rt, saved)
    op = await complete(hass, wid, body)
    assert op["status"] == "complete"

@pytest.mark.asyncio
async def test_unrelated_selection_or_other_panel_edit_does_not_drop_intent(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    watch["panels"].append({**DRIVING, "id":"another"})
    watch["active_panel"] = "another"
    watch["revision"] += 1
    async def deliver(domain, name, data, **kwargs):
        if name == "lock": states[body["panel_config"]["lock_entity"]].state = "locked"
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, rt, wid, watch, body)
    assert (await complete(hass, wid, body))["status"] == "complete"

@pytest.mark.asyncio
async def test_wake_reselects_donor_when_cancelled_during_persistence(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, asleep=True)
    rt = hass.data["fan_control"]
    entered, release = asyncio.Event(), asyncio.Event()
    async def save_boundary():
        if not entered.is_set():
            entered.set()
            await release.wait()
    monkeypatch.setattr(vo, "persist", lambda runtime: save_boundary())
    vo.handle(hass, rt, wid, watch, body)
    await entered.wait()
    clock[0] += 1
    second = {**body, "operation_id":"12345678-1234-1234-1234-123456789abd", "issued_at_ms":clock[0], "command":"stop_exercise"}
    vo.handle(hass, rt, wid, watch, second)
    async def deliver(domain, name, data, **kwargs):
        if name == "press": states[body["panel_config"]["awake_entity"]].state = "on"
        if name == "turn_on": states[body["panel_config"]["climate_entity"]].state = "cool"
    hass.services.async_call.side_effect = deliver
    release.set()
    op = await complete(hass, wid, second)
    await asyncio.gather(*(v["task"] for v in rt["vehicle_operations"].values()), return_exceptions=True)
    assert op["status"] == "complete"
    assert [c.args[1] for c in hass.services.async_call.await_args_list].count("press") == 1

@pytest.mark.asyncio
async def test_restart_preserves_shared_wake_ambiguity_for_replacement(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, asleep=True)
    rt = hass.data["fan_control"]
    vo.handle(hass, rt, wid, watch, body)
    first = rt["vehicle_operations"][(wid, body["operation_id"])]
    first["task"].cancel()
    await asyncio.gather(first["task"], return_exceptions=True)
    first.update(status="cancelled", wake_started=True)
    saved = {"operations":vo.records(rt)}
    rt["vehicle_operations"] = {}; rt["vehicle_latest"] = {}
    await vo.restore(hass, rt, saved)
    clock[0] += 1
    second = {**body, "operation_id":"12345678-1234-1234-1234-123456789abd", "issued_at_ms":clock[0]}
    vo.handle(hass, rt, wid, watch, second)
    await complete(hass, wid, second)
    assert "press" not in [c.args[1] for c in hass.services.async_call.await_args_list]
