import asyncio
import base64
import hashlib
import time
from unittest.mock import AsyncMock

import pytest

from custom_components.fan_control.local_music import music_response, private_music, CHUNK_SIZE
from custom_components.fan_control.exercise import (
    retry_stop, schedule_vehicle_check, vehicle_mismatches, STOP_SERVER_STARTED_MS,
)
from .test_exercise import PANEL, fixture, operation_body
from .test_api import runtime, request
from .test_protocol import packet


def test_private_music_order_chunk_bounds_and_content_hash(tmp_path):
    content = b"private test bytes" * 20000
    (tmp_path / "02 second.m4a").write_bytes(b"second")
    (tmp_path / "01 first.mp3").write_bytes(content)
    (tmp_path / "not-music.txt").write_text("excluded")
    tracks = music_response(tmp_path, {"action":"exercise_music_list"})["tracks"]
    assert [t["name"] for t in tracks] == ["01 first.mp3", "02 second.m4a"]
    assert tracks[0]["sha256"] == hashlib.sha256(content).hexdigest()
    response = music_response(tmp_path, {"action":"exercise_music_chunk", "name":tracks[0]["name"], "offset":CHUNK_SIZE})
    assert base64.b64decode(response["data"]) == content[CHUNK_SIZE:CHUNK_SIZE * 2]
    for name, offset in [("../01 first.mp3", 0), ("01 first.mp3", -1), ("01 first.mp3", True), ("01 first.mp3", len(content))]:
        with pytest.raises(ValueError):
            music_response(tmp_path, {"action":"exercise_music_chunk", "name":name, "offset":offset})


@pytest.mark.asyncio
async def test_private_music_requires_apple_watch_and_approved_exercise():
    for watch in ({"platform":"wear_os", "panels":[PANEL]}, {"platform":"watch_os", "panels":[]}):
        with pytest.raises(ValueError):
            await private_music(None, watch, {"action":"exercise_music_list"})


@pytest.mark.asyncio
async def test_local_stop_requests_temperature_once_but_confirms_only_lock_and_climate():
    hass, states = fixture(); operations = {}; now = STOP_SERVER_STARTED_MS + 10
    states["climate.example"].attributes.update(temperature_unit="°C", min_temp=16, max_temp=30, target_temp_step=0.5, temperature=18)
    body = {**operation_body(now), "music_mode":"local"}
    assert (await retry_stop(hass, PANEL, None, operations, "watch", body, now))["operation_status"] == "pending"
    temperature_calls = [c for c in hass.services.async_call.await_args_list if c.args[1] == "set_temperature"]
    assert len(temperature_calls) == 1
    assert temperature_calls[0].args[2]["temperature"] == pytest.approx(17.2222222)
    states["lock.example"].state = "unlocked"
    states["climate.example"].state = "cool"
    hass.services.async_call.reset_mock()
    result = await retry_stop(hass, PANEL, None, operations, "watch", body, now + 5000)
    assert result["operation_status"] == "complete"
    assert result["steps"][-1] == {"step":"set_temperature", "status":"requested"}
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_temperature_failure_does_not_block_confirmed_unlock_and_climate():
    hass, states = fixture(); operations = {}; now = STOP_SERVER_STARTED_MS + 10
    states["lock.example"].state = "unlocked"; states["climate.example"].state = "cool"
    hass.services.async_call.side_effect = RuntimeError("ambiguous")
    body = {**operation_body(now), "music_mode":"local"}
    result = await retry_stop(hass, PANEL, None, operations, "watch", body, now)
    assert result["operation_status"] == "complete"
    assert result["steps"][-1]["status"] == "unconfirmed"


@pytest.mark.asyncio
async def test_local_stop_dispatches_required_steps_offline_without_temperature_metadata():
    hass, states = fixture(); now = STOP_SERVER_STARTED_MS + 10
    states["binary_sensor.example_online"].state = "off"
    states["climate.example"].attributes.clear()
    result = await retry_stop(hass, PANEL, None, {}, "watch", {**operation_body(now), "music_mode":"local"}, now)
    assert result["operation_status"] == "pending"
    assert [(c.args[0], c.args[1]) for c in hass.services.async_call.await_args_list] == [("lock", "unlock"), ("climate", "turn_on")]


@pytest.mark.asyncio
async def test_local_stop_unlock_failure_does_not_suppress_climate_or_confirm_success():
    hass, states = fixture(); now = STOP_SERVER_STARTED_MS + 10
    async def deliver(domain, service, *args, **kwargs):
        if service == "unlock": raise RuntimeError("delivery failed")
        if service == "turn_on": states["climate.example"].state = "cool"
    hass.services.async_call.side_effect = deliver
    result = await retry_stop(hass, PANEL, None, {}, "watch", {**operation_body(now), "music_mode":"local"}, now)
    assert result["operation_status"] == "pending"
    assert [c.args[1] for c in hass.services.async_call.await_args_list] == ["unlock", "turn_on", "set_temperature"]


@pytest.mark.asyncio
async def test_offline_cached_matching_states_cannot_confirm_stop():
    hass, states = fixture(); now = STOP_SERVER_STARTED_MS + 10
    states["binary_sensor.example_online"].state = "off"
    states["lock.example"].state = "unlocked"
    states["climate.example"].state = "cool"
    result = await retry_stop(hass, PANEL, None, {}, "watch", {**operation_body(now), "music_mode":"local"}, now)
    assert result["operation_status"] == "pending"


@pytest.mark.asyncio
async def test_dispatch_failure_still_schedules_one_minute_vehicle_check(monkeypatch):
    import custom_components.fan_control as integration
    hass, manager, watch_id, _ = runtime(); _, states = fixture(); hass.states.get = states.get
    manager.watches[watch_id].update(platform="watch_os", panels=[PANEL])
    monkeypatch.setattr(integration, "execute_exercise", AsyncMock(side_effect=ValueError("unavailable")))
    body = {**operation_body(int(time.time() * 1000)), "music_mode":"local", "command":"start_exercise",
            "action":"panel_command", "panel_id":PANEL["id"], "panel_config":PANEL}
    response = await integration.WatchView(hass).post(request(packet(watch_id, manager.watches[watch_id]["secret"], body)))
    assert response.status == 403
    task = hass.data["fan_control"]["exercise_checks"][(watch_id, PANEL["id"])]
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_vehicle_check_ignores_temperature_but_not_unknown_states():
    hass, states = fixture()
    assert vehicle_mismatches(hass, PANEL, "start_exercise") == []
    states["lock.example"].state = "unlocked"; states["climate.example"].state = "cool"
    states["climate.example"].attributes["temperature"] = 72
    assert vehicle_mismatches(hass, PANEL, "stop_exercise") == []
    states["climate.example"].state = "unavailable"
    assert vehicle_mismatches(hass, PANEL, "stop_exercise") == ["Car climate is not confirmed on"]
    states["lock.example"].state = "unavailable"
    assert len(vehicle_mismatches(hass, PANEL, "stop_exercise")) == 2
    states["binary_sensor.example_online"].state = "off"
    assert vehicle_mismatches(hass, PANEL, "stop_exercise") == ["Vehicle state unavailable (vehicle offline or unknown)"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_one_minute_check_is_deduplicated_passive_and_target_scoped(monkeypatch, changed):
    hass, manager, watch_id, _ = runtime(); _, states = fixture(); hass.states.get = states.get
    manager.watches[watch_id]["panels"] = [PANEL]
    value = hass.data["fan_control"]; key = (watch_id, PANEL["id"])
    body = {**operation_body(int(time.time() * 1000)), "command":"stop_exercise"}
    delays = []
    async def tick(seconds):
        delays.append(seconds)
        if changed: manager.watches[watch_id]["panels"] = [{**PANEL, "lock_entity":"lock.other"}]
    monkeypatch.setattr(asyncio, "sleep", tick)
    schedule_vehicle_check(hass, value, watch_id, PANEL, body)
    task = value["exercise_checks"][key]
    schedule_vehicle_check(hass, value, watch_id, PANEL, body)
    assert value["exercise_checks"][key] is task
    await task
    assert len(delays) == 1 and 59 <= delays[0] <= 60
    if changed:
        hass.services.async_call.assert_not_awaited()
    else:
        call = hass.services.async_call.await_args
        assert call.args[:2] == ("persistent_notification", "create")
        assert "not confirmed unlocked" in call.args[2]["message"]
        assert "temperature" not in call.args[2]["message"]


@pytest.mark.asyncio
async def test_local_start_rejects_stale_configuration_before_any_car_command():
    from custom_components.fan_control import WatchView
    hass, manager, watch_id, _ = runtime(); manager.watches[watch_id].update(platform="watch_os", panels=[PANEL])
    body = {**operation_body(int(time.time() * 1000)), "music_mode":"local", "command":"start_exercise",
            "action":"panel_command", "panel_id":PANEL["id"], "panel_config":{**PANEL, "lock_entity":"lock.other"}}
    result = await WatchView(hass).post(request(packet(watch_id, manager.watches[watch_id]["secret"], body)))
    assert result.status == 403
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_music_files_are_only_returned_inside_authenticated_encryption(tmp_path):
    import json
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from custom_components.fan_control import WatchView
    from custom_components.fan_control.protocol import aad, decode
    hass, manager, watch_id, _ = runtime()
    manager.watches[watch_id].update(platform="watch_os", panels=[PANEL])
    content = b"private synthetic test content" * 10000
    (tmp_path / "01 song.mp3").write_bytes(content)
    hass.config = SimpleNamespace(path=lambda name: str(tmp_path))
    async def execute(function, *args): return function(*args)
    hass.async_add_executor_job = execute
    secret = manager.watches[watch_id]["secret"]
    value = packet(watch_id, secret, {"action":"exercise_music_chunk", "name":"01 song.mp3", "offset":0})
    response = await WatchView(hass).post(request(value))
    assert response.status == 200
    assert "synthetic" not in response.text and "song.mp3" not in response.text
    encrypted = json.loads(response.text)
    payload = json.loads(AESGCM(bytes.fromhex(secret)).decrypt(decode(encrypted["iv"]), base64.b64decode(encrypted["ciphertext"]), aad(value) + b":response"))
    assert base64.b64decode(payload["data"]) == content[:CHUNK_SIZE]
    assert (await WatchView(hass).post(request(value))).status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_successful_vehicle_check_is_silent_and_revocation_suppresses_delivery(monkeypatch, revoked):
    hass, manager, watch_id, _ = runtime(); _, states = fixture(); hass.states.get = states.get
    manager.watches[watch_id]["panels"] = [PANEL]
    states["lock.example"].state = "unlocked"; states["climate.example"].state = "cool"
    body = {**operation_body(int(time.time() * 1000)), "command":"stop_exercise"}
    async def tick(seconds):
        if revoked:
            states["lock.example"].state = "locked"
            hass.auth.async_get_user.return_value.is_active = False
    monkeypatch.setattr(asyncio, "sleep", tick)
    value = hass.data["fan_control"]; key = (watch_id, PANEL["id"])
    schedule_vehicle_check(hass, value, watch_id, PANEL, body)
    await value["exercise_checks"][key]
    hass.services.async_call.assert_not_awaited()
