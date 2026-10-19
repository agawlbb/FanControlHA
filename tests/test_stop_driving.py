import asyncio
import time
import pytest
from custom_components.fan_control.exercise import accept_stop, select_driving_after_stop, STOP_SERVER_STARTED_MS
from .test_api import runtime
from .test_exercise import fixture, PANEL, operation_body


def setup():
    hass, manager, watch_id, _ = runtime()
    _, states = fixture()
    hass.states.get = states.get
    driving = {"id": "driving_blue", "type": "driving", **{r: PANEL[r] for r in ("climate_entity", "wake_entity", "awake_entity")}}
    other = {**driving, "id": "driving_white", "climate_entity": "climate.other"}
    watch = manager.watches[watch_id]
    watch.update(panels=[PANEL, other, driving], active_panel="exercise")
    value = hass.data["fan_control"]
    key = (watch_id, PANEL["id"])
    op = {"complete": True}
    value["exercise_operations"] = {key: op}
    return hass, states, watch, value, key, op


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "expired", "cancelled", "unsupported"])
async def test_never_switches_without_confirmed_stop(status):
    hass, _, watch, value, key, op = setup()
    await select_driving_after_stop(hass, value, PANEL, key, op, {"operation_status": status})
    assert watch["active_panel"] == "exercise"
    value["store"].async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_car_once_and_no_physical_commands_or_location_requirement():
    hass, _, watch, value, key, op = setup()
    revision = watch["revision"]
    await select_driving_after_stop(hass, value, PANEL, key, op, {"operation_status": "complete"})
    assert watch["active_panel"] == "driving_blue"
    assert watch["revision"] == revision + 1
    value["store"].async_save.assert_awaited_once()
    hass.services.async_call.assert_not_awaited()
    watch["active_panel"] = "exercise"  # A later manual choice must survive duplicate Stop polls.
    await select_driving_after_stop(hass, value, PANEL, key, op, {"operation_status": "complete"})
    assert watch["active_panel"] == "exercise"
    value["store"].async_save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["no_match", "ambiguous", "panel_changed", "revoked", "cancelled"])
async def test_mapping_changes_and_stale_operations_fail_closed(change):
    hass, _, watch, value, key, op = setup()
    if change == "no_match": watch["panels"] = watch["panels"][:2]
    if change == "ambiguous": watch["panels"].append({**watch["panels"][-1], "id": "duplicate"})
    if change == "panel_changed": watch["panels"][0] = {**PANEL, "wake_entity": "button.changed"}
    if change == "revoked": value["manager"].watches.pop(key[0])
    if change == "cancelled": value["exercise_operations"][key] = {}
    await select_driving_after_stop(hass, value, PANEL, key, op, {"operation_status": "complete"})
    assert watch["active_panel"] == "exercise"
    value["store"].async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_failure_rolls_back_selection_without_changing_confirmed_result():
    hass, _, watch, value, key, op = setup()
    revision = watch["revision"]
    value["store"].async_save.side_effect = RuntimeError("storage unavailable")
    await select_driving_after_stop(hass, value, PANEL, key, op, {"operation_status": "complete"})
    assert watch["active_panel"] == "exercise" and watch["revision"] == revision
    assert "driving_selected" not in op
    hass.services.async_call.assert_not_awaited()


def confirm(states):
    states["lock.example"].state = "unlocked"
    states["climate.example"].state = "cool"
    states["climate.example"].attributes["temperature"] = 63


@pytest.mark.asyncio
async def test_request_confirmation_selects_driving():
    hass, states, watch, value, key, _ = setup()
    value["exercise_operations"].clear()
    confirm(states)
    result = await accept_stop(hass, value, PANEL, None, key, operation_body(max(STOP_SERVER_STARTED_MS, int(time.time()*1000))))
    assert result["operation_status"] == "complete"
    assert watch["active_panel"] == "driving_blue"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_confirmation_selects_driving_after_screen_can_leave(monkeypatch):
    hass, states, watch, value, key, _ = setup()
    value["exercise_operations"].clear()
    async def tick(_): confirm(states)
    monkeypatch.setattr(asyncio, "sleep", tick)
    result = await accept_stop(hass, value, PANEL, None, key, operation_body(max(STOP_SERVER_STARTED_MS, int(time.time()*1000))))
    assert result["operation_status"] == "pending" and watch["active_panel"] == "exercise"
    await value["exercise_operations"][key]["task"]
    assert watch["active_panel"] == "driving_blue"
