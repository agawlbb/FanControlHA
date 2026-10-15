import math
import pytest
from custom_components.fan_control.exercise import ExerciseTargets, command_plan, target_temperature

TARGETS = ExerciseTargets("lock.example", "climate.example", "button.example_wake", "binary_sensor.example_online")


def test_start_only_locks_and_never_starts_music():
    assert command_plan(TARGETS, "start_exercise", ready=True) == [("lock", "lock", {"entity_id": "lock.example"})]


def test_stop_order_and_fahrenheit_target():
    result = command_plan(TARGETS, "stop_exercise", ready=True, unit="°F", minimum=50, maximum=90)
    assert result == [("lock", "unlock", {"entity_id": "lock.example"}),
                      ("climate", "turn_on", {"entity_id": "climate.example"}),
                      ("climate", "set_temperature", {"entity_id": "climate.example", "temperature": 63.0})]


def test_sixty_three_fahrenheit_is_not_sixty_three_celsius():
    assert target_temperature("°C", 16, 30) == pytest.approx(17.2222222222)


@pytest.mark.parametrize("unit,low,high", [(None, 16, 30), ("K", 16, 30), ("°C", 18, 30),
    ("°C", math.nan, 30), ("°C", 16, math.inf), ("°F", True, 90), ("°F", 90, 50)])
def test_unknown_or_unsupported_temperature_rejected(unit, low, high):
    with pytest.raises(ValueError): target_temperature(unit, low, high)


@pytest.mark.parametrize("ready", [False, None, "on", 1])
def test_readiness_is_explicit_and_fail_closed(ready):
    with pytest.raises(ValueError): command_plan(TARGETS, "start_exercise", ready=ready)


@pytest.mark.parametrize("action", ["unlock", "wake", "play", "run_script", "stop"])
def test_no_arbitrary_services(action):
    with pytest.raises(ValueError): command_plan(TARGETS, action, ready=True)


def test_role_domains_are_fixed_and_identifiers_redacted():
    with pytest.raises(ValueError): ExerciseTargets("script.example", "climate.example", "button.example", "binary_sensor.example")
    assert "example" not in repr(TARGETS)


from types import SimpleNamespace
from unittest.mock import AsyncMock
from custom_components.fan_control.exercise import validate_exercise, exercise_state, execute_exercise

PANEL = validate_exercise({"type": "exercise", **vars(TARGETS)})

def fixture():
    states = {"lock.example": SimpleNamespace(state="locked", attributes={}),
              "binary_sensor.example_online": SimpleNamespace(state="on", attributes={}),
              "button.example_wake": SimpleNamespace(state="unknown", attributes={}),
              "climate.example": SimpleNamespace(state="off", attributes={"supported_features": 257,
                   "temperature_unit": "°F", "min_temp": 50, "max_temp": 90, "target_temp_step": 1})}
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get), services=SimpleNamespace(async_call=AsyncMock()))
    return hass, states

def test_readiness_and_temperature_precision_preflight():
    hass, states = fixture()
    assert exercise_state(hass, PANEL)["stop_available"]
    attrs = states["climate.example"].attributes
    attrs.update(temperature_unit="°C", min_temp=16, max_temp=30, target_temp_step=0.5)
    assert exercise_state(hass, PANEL)["start_available"]
    assert not exercise_state(hass, PANEL)["stop_available"]
    attrs.pop("target_temp_step")
    assert exercise_state(hass, PANEL)["stop_available"]
    attrs["supported_features"] = 256
    assert not exercise_state(hass, PANEL)["stop_available"]
    states["binary_sensor.example_online"].state = "off"
    assert not exercise_state(hass, PANEL)["start_available"]

@pytest.mark.asyncio
async def test_stop_requests_only_allowlisted_steps_and_reports_not_confirmed_physical_success():
    hass, _ = fixture()
    result = await execute_exercise(hass, PANEL, "stop_exercise", "context")
    assert [c.args[:2] for c in hass.services.async_call.await_args_list] == [("lock", "unlock"), ("climate", "turn_on"), ("climate", "set_temperature")]
    assert all(s["status"] == "requested" for s in result["steps"])
    assert result["spotify_pause"] == "watch_local"

@pytest.mark.asyncio
async def test_partial_failure_stops_sequence_without_replay_or_rollback():
    hass, _ = fixture()
    hass.services.async_call.side_effect = [None, RuntimeError("private backend message")]
    result = await execute_exercise(hass, PANEL, "stop_exercise", "context")
    assert [s["status"] for s in result["steps"]] == ["requested", "unconfirmed", "skipped"]
    assert hass.services.async_call.await_count == 2
    assert "private" not in repr(result)

@pytest.mark.asyncio
async def test_unsupported_stop_does_not_unlock_first():
    hass, states = fixture()
    states["climate.example"].attributes["supported_features"] = 0
    with pytest.raises(ValueError): await execute_exercise(hass, PANEL, "stop_exercise", None)
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_offline_transition_stops_subsequent_steps():
    hass, states = fixture()
    async def offline(*args, **kwargs): states["binary_sensor.example_online"].state = "off"
    hass.services.async_call.side_effect = offline
    result = await execute_exercise(hass, PANEL, "stop_exercise", None)
    assert hass.services.async_call.await_count == 1
    assert [s["status"] for s in result["steps"]] == ["requested", "unconfirmed", "skipped"]


@pytest.mark.asyncio
async def test_unlock_transition_does_not_prevent_following_climate_request():
    hass, states = fixture()
    async def transition(*args, **kwargs): states["lock.example"].state = "unlocking"
    hass.services.async_call.side_effect = transition
    result = await execute_exercise(hass, PANEL, "stop_exercise", None)
    assert hass.services.async_call.await_count == 3
    assert all(s["status"] == "requested" for s in result["steps"])

@pytest.mark.asyncio
async def test_encrypted_exercise_dispatch_and_replay_protection():
    from .test_api import runtime, request
    from .test_protocol import packet
    from custom_components.fan_control import WatchView
    hass, manager, watch_id, _ = runtime()
    _, states = fixture()
    hass.states.get = states.get
    manager.watches[watch_id]["panels"] = [PANEL]
    secret = manager.watches[watch_id]["secret"]
    value = packet(watch_id, secret, {"action":"panel_command", "panel_id":"exercise", "command":"start_exercise"})
    assert (await WatchView(hass).post(request(value))).status == 200
    assert (await WatchView(hass).post(request(value))).status == 403
    hass.services.async_call.assert_awaited_once_with("lock", "lock", {"entity_id":"lock.example"}, blocking=True, context={"user_id":"example-owner"})

@pytest.mark.asyncio
async def test_exercise_configuration_form_and_reads_are_passive():
    from .test_api import runtime, request
    from .test_protocol import packet
    from custom_components.fan_control import WatchView
    from custom_components.fan_control.config_flow import FanControlOptions
    hass, manager, watch_id, _ = runtime()
    _, states = fixture()
    hass.states.get = states.get
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = watch_id; flow.editing_panel = None
    assert (await flow.async_step_exercise(PANEL))["type"] == "create_entry"
    secret = manager.watches[watch_id]["secret"]
    for body in ({"action":"config"}, {"action":"panel_state", "panel_id":"exercise"}):
        assert (await WatchView(hass).post(request(packet(watch_id, secret, body)))).status == 200
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_power", [True, False])
async def test_running_climate_skips_unnecessary_turn_on(supports_power):
    hass, states = fixture()
    states["climate.example"].state = "cool"
    states["climate.example"].attributes["supported_features"] = 257 if supports_power else 1
    assert exercise_state(hass, PANEL)["stop_available"]
    result = await execute_exercise(hass, PANEL, "stop_exercise", None)
    assert [c.args[:2] for c in hass.services.async_call.await_args_list] == [("lock", "unlock"), ("climate", "set_temperature")]
    assert result["steps"] == [{"step":"unlock", "status":"requested"}, {"step":"set_temperature", "status":"requested"}]

@pytest.mark.asyncio
async def test_off_climate_without_turn_on_support_does_not_unlock():
    hass, states = fixture()
    states["climate.example"].attributes["supported_features"] = 1
    assert not exercise_state(hass, PANEL)["stop_available"]
    with pytest.raises(ValueError): await execute_exercise(hass, PANEL, "stop_exercise", None)
    hass.services.async_call.assert_not_awaited()


from custom_components.fan_control.exercise import retry_stop, cancel_stop, accept_stop, STOP_SERVER_STARTED_MS

def operation_body(issued):
    return {"operation_id":"12345678-1234-1234-1234-123456789abc", "issued_at_ms":issued, "panel_config":PANEL}

@pytest.mark.asyncio
async def test_retry_reconciles_ambiguous_unlock_and_only_retries_unfinished_steps():
    hass, states = fixture(); operations = {}; now = STOP_SERVER_STARTED_MS + 10
    async def ambiguous(domain, service, *args, **kwargs):
        if service == "unlock":
            states["lock.example"].state = "unlocked"
            raise RuntimeError("lost response")
    hass.services.async_call.side_effect = ambiguous
    body = operation_body(now)
    first = await retry_stop(hass,PANEL,None,operations,"watch",body,now)
    assert first["steps"][0]["status"] == "unconfirmed"
    hass.services.async_call.reset_mock(); hass.services.async_call.side_effect=None
    await retry_stop(hass,PANEL,None,operations,"watch",body,now+5000)
    assert [c.args[:2] for c in hass.services.async_call.await_args_list] == [("climate","turn_on"),("climate","set_temperature")]
    states["climate.example"].state="cool"; states["climate.example"].attributes["temperature"]=63
    hass.services.async_call.reset_mock()
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now+6000))["operation_status"] == "complete"
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now+7000))["operation_status"] == "complete"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_duplicate_requests_backoff_without_duplicate_service_calls():
    hass, _ = fixture(); operations={}; now=STOP_SERVER_STARTED_MS+10; body=operation_body(now)
    await retry_stop(hass,PANEL,None,operations,"watch",body,now)
    count=hass.services.async_call.await_count
    for delta in (1,100,1000): await retry_stop(hass,PANEL,None,operations,"watch",body,now+delta)
    assert hass.services.async_call.await_count==count

@pytest.mark.asyncio
async def test_expiry_restart_and_start_cancel_prevent_delayed_unlock():
    hass,_=fixture(); operations={}; now=STOP_SERVER_STARTED_MS+10; body=operation_body(now)
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now+120000))["operation_status"]=="expired"
    assert (await retry_stop(hass,PANEL,None,operations,"watch",operation_body(STOP_SERVER_STARTED_MS-1),now))["operation_status"]=="expired"
    cancel_stop(operations,"watch",now+100)
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now+101))["operation_status"]=="cancelled"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_retry_rejects_changed_target_and_unsupported_precision_before_unlock():
    hass,states=fixture(); operations={}; now=STOP_SERVER_STARTED_MS+10; body=operation_body(now)
    with pytest.raises(ValueError): await retry_stop(hass,PANEL,None,operations,"watch",{**body,"panel_config":{**PANEL,"lock_entity":"lock.other"}},now)
    states["climate.example"].attributes.update(temperature_unit="°C",min_temp=16,max_temp=30,target_temp_step=0.5)
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now))["operation_status"]=="unsupported"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_accepted_operation_continues_without_watch_requests(monkeypatch):
    import asyncio
    from .test_api import runtime
    hass,manager,watch_id,_=runtime(); _,states=fixture(); hass.states.get=states.get
    manager.watches[watch_id]["panels"]=[PANEL]
    runtime_value=hass.data["fan_control"]
    now=max(STOP_SERVER_STARTED_MS,int(__import__("time").time()*1000))
    body=operation_body(now)
    async def tick(seconds):
        states["lock.example"].state="unlocked"
        states["climate.example"].state="cool"
        states["climate.example"].attributes["temperature"]=63
    monkeypatch.setattr(asyncio,"sleep",tick)
    result=await accept_stop(hass,runtime_value,PANEL,None,(watch_id,PANEL["id"]),body)
    assert result["operation_status"]=="pending"
    task=runtime_value["exercise_operations"][(watch_id,PANEL["id"])]["task"]
    await task
    assert runtime_value["exercise_operations"][(watch_id,PANEL["id"])]["complete"]


@pytest.mark.asyncio
async def test_cancel_before_delayed_delivery_blocks_same_id_despite_clock_skew():
    hass,_=fixture(); operations={}; now=STOP_SERVER_STARTED_MS+10; body=operation_body(now+2000)
    cancel_stop(operations,"watch",now,identity=body["operation_id"])
    assert (await retry_stop(hass,PANEL,None,operations,"watch",body,now+3000))["operation_status"]=="cancelled"
    hass.services.async_call.assert_not_awaited()
