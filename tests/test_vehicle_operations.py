import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.fan_control import WatchView
from custom_components.fan_control import vehicle_operations as vo
from .test_api import runtime, request
from .test_protocol import packet
from .test_exercise import PANEL
from .test_driving import PANEL as DRIVING


def setup(monkeypatch, panel=PANEL, command="start_exercise", asleep=False):
    hass, manager, watch_id, _ = runtime()
    watch = manager.watches[watch_id]
    watch.update(platform="watch_os", panels=[copy.deepcopy(panel)], active_panel=panel["id"])
    states = {
        panel["awake_entity"]: SimpleNamespace(state="off" if asleep else "on", attributes={}),
        panel["wake_entity"]: SimpleNamespace(state="unknown", attributes={}),
        panel["climate_entity"]: SimpleNamespace(state="off", attributes={"supported_features":384, "temperature_unit":"°F"}),
    }
    if "lock_entity" in panel:
        states[panel["lock_entity"]] = SimpleNamespace(state="unlocked", attributes={})
    for role in ("garage_entity", "trunk_entity"):
        if role in panel:
            states[panel[role]] = SimpleNamespace(state="closed", attributes={"supported_features":3})
    if "horn_entity" in panel:
        states[panel["horn_entity"]] = SimpleNamespace(state="unknown", attributes={})
    hass.states.get = states.get
    clock = [vo.STARTED_MS + 10]
    monkeypatch.setattr(vo, "now_ms", lambda: clock[0])
    original_sleep = asyncio.sleep
    async def sleep(seconds):
        clock[0] += int(seconds * 1000)
        await original_sleep(0)
    monkeypatch.setattr(vo.asyncio, "sleep", sleep)
    body = {"action":"vehicle_submit", "panel_id":panel["id"], "panel_config":copy.deepcopy(panel),
            "command":command, "operation_id":"12345678-1234-1234-1234-123456789abc", "issued_at_ms":clock[0]}
    return hass, watch, watch_id, states, clock, body


async def complete(hass, watch_id, body):
    runtime = hass.data["fan_control"]
    op = runtime["vehicle_operations"][(watch_id, body["operation_id"])]
    await asyncio.wait_for(op["task"], 3)
    return op


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [30, 60, 90])
async def test_slow_wake_is_silent_and_first_tap_locks(monkeypatch, delay):
    hass, watch, wid, states, clock, body = setup(monkeypatch, asleep=True)
    issued = clock[0]
    async def deliver(domain, name, data, **kwargs):
        assert not hass.data["fan_control"]["lock"].locked()
        if name == "press":
            clock[0] += delay * 1000
            states[PANEL["awake_entity"]].state = "on"
        elif name == "lock":
            assert states[PANEL["awake_entity"]].state == "on"
            states[PANEL["lock_entity"]].state = "locked"
    hass.services.async_call.side_effect = deliver
    result = vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    assert result["operation_status"] == "pending"
    op = await complete(hass, wid, body)
    assert op["status"] == "complete"
    assert clock[0] - issued >= delay * 1000
    names = [c.args[1] for c in hass.services.async_call.await_args_list]
    assert names.count("press") == names.count("lock") == 1
    assert "create" not in names


@pytest.mark.asyncio
async def test_offline_stop_does_not_dispatch_and_alerts_only_at_final_deadline(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, command="stop_exercise", asleep=True)
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op["status"] == "unconfirmed"
    assert clock[0] >= body["issued_at_ms"] + 195000
    names = [c.args[1] for c in hass.services.async_call.await_args_list]
    assert names == ["press", "create"]
    assert "not confirmed" in hass.services.async_call.await_args_list[-1].args[2]["title"]


@pytest.mark.asyncio
async def test_duplicate_submission_preserves_task_and_deadline(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    first = vo.handle(hass, rt, wid, watch, body)
    op = rt["vehicle_operations"][(wid, body["operation_id"])]
    task = op["task"]
    clock[0] += 1000
    second = vo.handle(hass, rt, wid, watch, body)
    assert first["deadline_ms"] == second["deadline_ms"]
    assert op["task"] is task
    vo.handle(hass, rt, wid, watch, {"action":"vehicle_cancel", "operation_id":op["id"]})
    await asyncio.gather(task, return_exceptions=True)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_trunk_is_never_replayed(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "trunk_open")
    hass.services.async_call.side_effect = RuntimeError("lost response")
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op["status"] == "unconfirmed"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["open_cover"]


@pytest.mark.asyncio
async def test_final_grace_confirms_without_more_dispatch(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    original_sleep = vo.asyncio.sleep
    async def late_observation(seconds):
        await original_sleep(seconds)
        if clock[0] >= body["issued_at_ms"] + 185000:
            states[PANEL["lock_entity"]].state = "locked"
    monkeypatch.setattr(vo.asyncio, "sleep", late_observation)
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op["status"] == "complete"
    assert "create" not in [c.args[1] for c in hass.services.async_call.await_args_list]


@pytest.mark.asyncio
async def test_changed_panel_or_revocation_cancels_stale_operation(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    vo.handle(hass, rt, wid, watch, body)
    watch["panels"][0]["lock_entity"] = "lock.changed"
    result = vo.handle(hass, rt, wid, watch, {"action":"vehicle_status", "operation_id":body["operation_id"]})
    assert result["operation_status"] == "cancelled"
    await asyncio.gather(rt["vehicle_operations"][(wid, body["operation_id"])]["task"], return_exceptions=True)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_garage_and_podcast_do_not_wait_for_car(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, "garage_open", asleep=True)
    async def deliver(domain, name, data, **kwargs):
        states[DRIVING["garage_entity"]].state = "open"
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    assert (await complete(hass, wid, body))["status"] == "complete"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["open_cover"]


@pytest.mark.asyncio
async def test_api_acknowledges_without_waiting_for_vehicle_service(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    response = await WatchView(hass).post(request(packet(wid, watch["secret"], body)))
    assert response.status == 200
    assert "lock.example" not in response.text
    op = hass.data["fan_control"]["vehicle_operations"][(wid, body["operation_id"])]
    op["task"].cancel()
    await asyncio.gather(op["task"], return_exceptions=True)


@pytest.mark.asyncio
async def test_other_client_and_changed_duplicate_are_rejected(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    rt = hass.data["fan_control"]
    watch["platform"] = "wear_os"
    with pytest.raises(ValueError): vo.handle(hass, rt, wid, watch, body)
    watch["platform"] = "watch_os"
    vo.handle(hass, rt, wid, watch, body)
    with pytest.raises(ValueError): vo.handle(hass, rt, wid, watch, {**body, "command":"stop_exercise"})
    op = rt["vehicle_operations"][(wid, body["operation_id"])]
    op["task"].cancel()
    await asyncio.gather(op["task"], return_exceptions=True)


@pytest.mark.asyncio
async def test_completed_wake_http_call_is_shared_until_readiness_deadline(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, asleep=True)
    rt = hass.data["fan_control"]
    op = {"id":body["operation_id"], "issued":clock[0], "watch_id":wid, "owner":watch["owner_id"],
          "secret":watch["secret"], "panel":copy.deepcopy(PANEL), "status":"pending"}
    # Let the first wake call return while the sensor remains offline.
    first = asyncio.create_task(vo.wake(hass, rt, op))
    real_sleep = asyncio.sleep
    for _ in range(3): await real_sleep(0)
    second = asyncio.create_task(vo.wake(hass, rt, {**op, "id":"second"}))
    await asyncio.gather(first, second)
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["press"]


@pytest.mark.asyncio
async def test_definitive_rejection_can_alert_early(monkeypatch):
    from homeassistant.exceptions import ServiceValidationError
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    async def deliver(domain, name, data, **kwargs):
        if domain == "lock": raise ServiceValidationError("permission rejected")
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, hass.data["fan_control"], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op["status"] == "failed"
    assert clock[0] < body["issued_at_ms"] + 60000
    assert hass.services.async_call.await_args_list[-1].args[2]["title"] == "Exercise request failed"


@pytest.mark.asyncio
async def test_companion_cannot_submit_operations(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch)
    watch["companion"] = {"secret":"a" * 64}
    encrypted = packet(wid, watch["companion"]["secret"], body)
    encrypted["credential"] = "companion"
    assert (await WatchView(hass).post(request(encrypted))).status == 403
    assert not hass.data["fan_control"].get("vehicle_operations")
