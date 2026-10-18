from types import SimpleNamespace
import pytest
from custom_components.fan_control.panels import validate_panel, panel_state, panel_command, configuration
from custom_components.fan_control.config_flow import FanControlOptions
from custom_components.fan_control import WatchView
from .test_api import runtime, request
from .test_protocol import packet

PANEL = {"type": "driving", "id": "driving", "name": "Driving", "garage_entity": "cover.example_garage",
         "trunk_entity": "cover.example_trunk", "climate_entity": "climate.example_car",
         "wake_entity": "button.example_wake", "awake_entity": "binary_sensor.example_online"}


def setup_driving():
    hass, manager, watch_id, _ = runtime()
    manager.watches[watch_id]["panels"] = [validate_panel(PANEL)]
    states = {"cover.example_garage": SimpleNamespace(state="closed", attributes={"supported_features": 3}),
              "cover.example_trunk": SimpleNamespace(state="closed", attributes={"supported_features": 3}),
              "climate.example_car": SimpleNamespace(state="off", attributes={"supported_features": 384}),
              "button.example_wake": SimpleNamespace(state="unknown", attributes={}),
              "binary_sensor.example_online": SimpleNamespace(state="on", attributes={})}
    hass.states.get = states.get
    return hass, manager, watch_id, states


@pytest.mark.parametrize("role,value", [("trunk_entity", "button.trunk"), ("garage_entity", "cover.example_trunk"),
    ("climate_entity", "switch.car"), ("wake_entity", "script.wake"), ("awake_entity", "sensor.online"), ("id", "devices")])
def test_driving_roles_are_fixed_and_distinct(role, value):
    with pytest.raises(ValueError):
        validate_panel({**PANEL, role: value})


@pytest.mark.parametrize("action,entity,service", [("garage_open", "cover.example_garage", "open_cover"),
    ("garage_close", "cover.example_garage", "close_cover"), ("trunk_open", "cover.example_trunk", "open_cover"),
    ("trunk_close", "cover.example_trunk", "close_cover"), ("climate_on", "climate.example_car", "turn_on")])
def test_commands_are_explicit_and_bound_to_approved_roles(action, entity, service):
    hass, _, _, _ = setup_driving()
    assert panel_command(hass, PANEL, action) == (entity.split(".")[0], service, {"entity_id": entity})


@pytest.mark.parametrize("action", ["toggle", "climate_off", "unlock", "leave", "stop_exercise", "open_cover"])
def test_unapproved_driving_commands_cannot_dispatch(action):
    hass, _, _, _ = setup_driving()
    with pytest.raises(ValueError): panel_command(hass, PANEL, action)


def test_sleeping_car_masks_trunk_and_climate_but_garage_still_works():
    hass, _, _, states = setup_driving()
    states["binary_sensor.example_online"].state = "off"
    value = panel_state(hass, PANEL)
    assert value["trunk_state"] == "unknown" and not value["trunk_open_available"]
    assert not value["climate_available"] and not value["climate_on"]
    assert value["garage_state"] == "closed" and value["garage_open_available"]
    for action in ("trunk_open", "trunk_close", "climate_on"):
        with pytest.raises(ValueError): panel_command(hass, PANEL, action)
    assert panel_command(hass, PANEL, "garage_open")[1] == "open_cover"


def test_cover_unknown_is_not_reported_as_closed_and_capabilities_are_checked():
    hass, _, _, states = setup_driving()
    states["cover.example_trunk"].state = "unknown"
    assert panel_state(hass, PANEL)["trunk_state"] == "unknown"
    assert panel_command(hass, PANEL, "trunk_open")[1] == "open_cover"
    states["cover.example_trunk"].attributes["supported_features"] = 1
    with pytest.raises(ValueError): panel_command(hass, PANEL, "trunk_close")
    states["cover.example_trunk"].state = "unavailable"
    with pytest.raises(ValueError): panel_command(hass, PANEL, "trunk_open")


@pytest.mark.asyncio
async def test_config_and_state_are_passive_and_never_expand_generic_device_allowlist():
    hass, manager, watch_id, _ = setup_driving()
    watch = manager.watches[watch_id]
    approved_devices = list(watch["devices"])
    for body in ({"action": "config"}, {"action": "panel_state", "panel_id": "driving"}):
        assert (await WatchView(hass).post(request(packet(watch_id, watch["secret"], body)))).status == 200
    assert configuration(watch)["devices"][-1]["panel"] == PANEL
    assert watch["devices"] == approved_devices
    assert not any(item["entity"].startswith("cover.") for item in watch["devices"])
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_encrypted_driving_dispatch_rejects_replay_and_stale_target():
    hass, manager, watch_id, states = setup_driving()
    secret = manager.watches[watch_id]["secret"]
    body = {"action": "panel_command", "panel_id": "driving", "command": "trunk_open", "panel_config": PANEL}
    message = packet(watch_id, secret, body)
    assert (await WatchView(hass).post(request(message))).status == 200
    assert (await WatchView(hass).post(request(message))).status == 403
    hass.services.async_call.assert_awaited_once_with("cover", "open_cover", {"entity_id": "cover.example_trunk"}, blocking=True, context={"user_id": "example-owner"})
    for config in (None, {**PANEL, "trunk_entity": "cover.other"}):
        assert (await WatchView(hass).post(request(packet(watch_id, secret, {**body, "panel_config": config})))).status == 403
    assert (await WatchView(hass).post(request(packet(watch_id, secret, {"action": "command", "entity": "cover.example_trunk", "command": "open_cover"})))).status == 403
    assert hass.services.async_call.await_count == 1


@pytest.mark.asyncio
async def test_explicit_wake_is_role_scoped_and_does_not_need_online_car():
    hass, manager, watch_id, states = setup_driving()
    states["binary_sensor.example_online"].state = "off"
    body = {"action": "panel_command", "panel_id": "driving", "command": "wake", "panel_config": PANEL}
    assert (await WatchView(hass).post(request(packet(watch_id, manager.watches[watch_id]["secret"], body)))).status == 200
    hass.services.async_call.assert_awaited_once_with("button", "press", {"entity_id": "button.example_wake"}, blocking=True, context={"user_id": "example-owner"})


@pytest.mark.asyncio
async def test_native_driving_add_edit_selection_and_removal_do_not_call_devices():
    hass, manager, watch_id, states = setup_driving()
    manager.watches[watch_id]["panels"] = []
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = watch_id
    assert "add_driving" in (await flow.async_step_panel_menu())["menu_options"]
    assert (await flow.async_step_add_driving())["step_id"] == "driving"
    assert (await flow.async_step_driving(PANEL))["type"] == "create_entry"
    assert (await flow.async_step_edit_panel({"panel_id": "driving"}))["step_id"] == "driving"
    await flow.async_step_activate_panel({"panel_id": "driving"})
    assert configuration(manager.watches[watch_id])["entity"] == "panel.driving"
    await flow.async_step_remove_panel({"panel_id": "driving"})
    assert manager.watches[watch_id]["active_panel"] == ""
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_form_rejects_wrong_roles_missing_entities_unsupported_covers_and_changed_id():
    hass, manager, watch_id, states = setup_driving()
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = watch_id; flow.editing_panel = "driving"
    for value in ({**PANEL, "id": "other"}, {**PANEL, "trunk_entity": "cover.missing"}):
        assert (await flow.async_step_driving(value))["errors"] == {"base": "invalid_panel"}
    states["cover.example_trunk"].attributes["supported_features"] = 1
    assert (await flow.async_step_driving(PANEL))["errors"] == {"base": "invalid_panel"}
    assert manager.watches[watch_id]["panels"] == [PANEL]
