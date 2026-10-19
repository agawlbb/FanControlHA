from types import SimpleNamespace
import pytest
from custom_components.fan_control.config_flow import FanControlOptions
from custom_components.fan_control.panels import configuration, validate_panel, panel_command
from custom_components.fan_control.select import WatchSelection, PanelSelection
from .test_api import runtime
from .test_protocol import session
from .test_panel_color import EXERCISE
from cryptography.hazmat.primitives.asymmetric import rsa

def flow_setup():
    hass, manager, watch_id, _ = runtime()
    watch = manager.watches[watch_id]
    watch.update(platform="watch_os", devices=[], entity="", panels=[])
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = watch_id; flow.editing_panel = None
    return flow, hass, manager, watch_id

@pytest.mark.asyncio
async def test_apple_watch_pairing_does_not_require_a_fan_and_retains_name():
    flow, hass, manager, watch_id = flow_setup()
    form = await flow.async_step_devices()
    assert not {"entities", "active_entity"} & {str(k) for k in form["data_schema"].schema}
    value = dict(name="Joes Ultra 4", owner_id="example-owner", local_url="http://192.168.1.2:8123")
    rejected = await flow.async_step_devices({**value, "local_url":"invalid"})
    assert rejected["errors"]
    key = next(k for k in rejected["data_schema"].schema if str(k) == "name")
    assert key.default() == "Joes Ultra 4"
    assert (await flow.async_step_devices(value))["type"] == "create_entry"
    assert manager.watches[watch_id]["name"] == "Joes Ultra 4"
    assert configuration(manager.watches[watch_id])["devices"] == []
    assert WatchSelection(hass, watch_id).device_info["model"] == "Apple Watch"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_fan_panel_add_edit_selection_removal_and_scoped_commands():
    flow, hass, manager, watch_id = flow_setup()
    value = dict(id="bedroom", name="Bedroom", fan_entity="fan.example")
    assert (await flow.async_step_add_fan())["step_id"] == "fan"
    assert (await flow.async_step_fan(value))["type"] == "create_entry"
    watch = manager.watches[watch_id]
    assert configuration(watch)["entity"] == "panel.bedroom"
    assert PanelSelection(hass, watch_id).options == ["bedroom"]
    assert panel_command(hass, watch["panels"][0], "speed4") == ("fan", "set_percentage", {"entity_id":"fan.example", "percentage":66})
    with pytest.raises(ValueError): panel_command(hass, watch["panels"][0], "unlock")
    assert (await flow.async_step_edit_panel({"panel_id":"bedroom"}))["step_id"] == "fan"
    assert (await flow.async_step_fan({**value,"id":"changed"}))["errors"]
    await flow.async_step_remove_panel({"panel_id":"bedroom"})
    assert configuration(watch)["devices"] == []
    assert (await flow.async_step_activate_panel())["type"] == "abort"
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["panel", "exercise"])
async def test_unused_color_picker_removed(step):
    flow, hass, manager, watch_id = flow_setup()
    form = await getattr(flow, "async_step_"+step)()
    assert "color" not in {str(k) for k in form["data_schema"].schema}

@pytest.mark.asyncio
async def test_spotify_dependency_error_is_specific_and_retains_entities():
    flow, hass, manager, watch_id = flow_setup()
    value = {**EXERCISE,"spotify_entity":"media_player.spotify"}
    form = await flow.async_step_exercise(value)
    assert form["errors"] == {"spotify_playlist":"spotify_playlist_required"}
    key = next(k for k in form["data_schema"].schema if str(k) == "lock_entity")
    assert key.default() == EXERCISE["lock_entity"]
    value["spotify_playlist"] = "https://open.spotify.com/playlist/" + "a"*22
    assert (await flow.async_step_exercise(value))["type"] == "create_entry"
    assert configuration(manager.watches[watch_id])["entity"] == "panel.exercise"
    hass.services.async_call.assert_not_awaited()

