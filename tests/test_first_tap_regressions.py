import asyncio
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from custom_components.fan_control import WatchView
from custom_components.fan_control import vehicle_operations as vo
from custom_components.fan_control.panels import validate_panel
from .test_vehicle_operations import setup, complete, PANEL, DRIVING
from .test_api import request
from .test_protocol import packet


@pytest.mark.asyncio
@pytest.mark.parametrize('direction', ['open', 'close'])
async def test_matching_trunk_state_completes_first_operation(monkeypatch, direction):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, 'trunk_' + direction)
    states[DRIVING['trunk_entity']].state = 'closed' if direction == 'open' else 'open'
    async def deliver(domain, name, data, **kwargs):
        states[DRIVING['trunk_entity']].state = direction + ('d' if direction == 'close' else '')
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, hass.data['fan_control'], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op['status'] == 'complete', (op['status'], states[DRIVING['trunk_entity']].state)


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['climate_on', 'leave'])
async def test_missing_battery_does_not_block_healthy_climate_or_script(monkeypatch, command):
    p = validate_panel({'type':'work_morning', 'id':'work', 'name':'Work',
                       'battery_entity':'sensor.battery', 'climate_entity':'climate.car',
                       'script_entity':'script.leave', 'wake_entity':'button.wake',
                       'awake_entity':'binary_sensor.online'})
    hass, watch, wid, states, clock, body = setup(monkeypatch, p, command)
    states['sensor.battery'] = SimpleNamespace(state='unavailable', attributes={'unit_of_measurement':'%'})
    states['script.leave'] = SimpleNamespace(state='off', attributes={})
    vo.handle(hass, hass.data['fan_control'], wid, watch, body)
    op = await complete(hass, wid, body)
    names = [c.args[1] for c in hass.services.async_call.await_args_list]
    assert 'turn_on' in names, (op['status'], names)


@pytest.mark.asyncio
async def test_climate_off_confirmation_does_not_require_turn_on_support(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, 'climate_off')
    states[DRIVING['climate_entity']].state = 'cool'
    states[DRIVING['climate_entity']].attributes['supported_features'] = 128
    async def deliver(domain, name, data, **kwargs):
        states[DRIVING['climate_entity']].state = 'off'
    hass.services.async_call.side_effect = deliver
    vo.handle(hass, hass.data['fan_control'], wid, watch, body)
    op = await complete(hass, wid, body)
    assert op['status'] == 'complete', (op['status'], states[DRIVING['climate_entity']].state)


@pytest.mark.asyncio
async def test_replacement_command_can_wake_after_original_cancelled(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, asleep=True)
    rt = hass.data['fan_control']
    vo.handle(hass, rt, wid, watch, body)
    await asyncio.sleep(0)  # Original scheduled its wake but its service has not run yet.
    clock[0] += 1
    replacement = {**body, 'command':'stop_exercise', 'issued_at_ms':clock[0],
                   'operation_id':'12345678-1234-1234-1234-123456789abd'}
    vo.handle(hass, rt, wid, watch, replacement)
    op = await complete(hass, wid, replacement)
    await asyncio.gather(*(item['task'] for item in rt['vehicle_operations'].values()), return_exceptions=True)
    names = [c.args[1] for c in hass.services.async_call.await_args_list]
    assert 'press' in names, (op['status'], names)


@pytest.mark.asyncio
async def test_slow_generic_service_does_not_block_vehicle_acceptance(monkeypatch):
    hass, watch, wid, states, clock, body = setup(monkeypatch, DRIVING, 'garage_open')
    rt = hass.data['fan_control']
    entered, release = asyncio.Event(), asyncio.Event()
    async def deliver(domain, name, data, **kwargs):
        if domain == 'fan':
            entered.set()
            await release.wait()
    hass.services.async_call.side_effect = deliver
    view = WatchView(hass)
    first = asyncio.create_task(view.post(request(packet(wid, watch['secret'],
        {'action':'command', 'entity':'fan.example', 'command':'turn_on'}))))
    await entered.wait()
    second = asyncio.create_task(view.post(request(packet(wid, watch['secret'], body))))
    try:
        try: await asyncio.wait_for(asyncio.shield(second), 0.05)
        except TimeoutError: pass
        assert second.done(), 'A generic service holds the global lock and prevents independent operation acceptance'
    finally:
        release.set()
        await asyncio.gather(first, second)
        for op in rt.get('vehicle_operations', {}).values(): op['task'].cancel()
        await asyncio.gather(*(op['task'] for op in rt.get('vehicle_operations', {}).values()), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['start_exercise', 'stop_exercise'])
async def test_apple_local_music_can_command_existing_spotify_configured_panel(monkeypatch, command):
    panel = validate_panel({**PANEL, 'spotify_entity':'media_player.spotify',
                            'spotify_playlist':'spotify:playlist:' + 'A' * 22,
                            'spotify_source':'Example source'})
    hass, watch, wid, states, clock, body = setup(monkeypatch, panel, command)
    # Apple's Codable Panel has no spotify_* fields, so this is its actual wire shape.
    body['panel_config'] = {k:v for k,v in panel.items() if not k.startswith('spotify_')}
    body['music_mode'] = 'local'
    encrypted = packet(wid, watch['secret'], body)
    result = await WatchView(hass).post(request(encrypted))
    try:
        assert result.status == 200, (result.status, result.text)
    finally:
        rt = hass.data['fan_control']
        for op in rt.get('vehicle_operations', {}).values(): op['task'].cancel()
        await asyncio.gather(*(op['task'] for op in rt.get('vehicle_operations', {}).values()), return_exceptions=True)
