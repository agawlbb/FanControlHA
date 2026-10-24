import json
from types import SimpleNamespace
import pytest
from custom_components.fan_control.panels import validate_panel, configuration, panel_state, panel_command
from custom_components.fan_control.config_flow import FanControlOptions
from custom_components.fan_control.select import PanelSelection
from custom_components.fan_control import WatchView
from .test_api import runtime, request
from .test_protocol import packet

PANEL = {"id": "work_morning", "name": "Work Morning", "battery_entity": "sensor.example_battery",
         "climate_entity": "climate.example_car", "script_entity": "script.example_leave"}

def with_panel():
    hass, manager, watch_id, _ = runtime()
    watch = manager.watches[watch_id]
    watch["panels"] = [validate_panel(PANEL)]
    states = {"sensor.example_battery": SimpleNamespace(state="82", attributes={"unit_of_measurement": "%", "location": "private"}),
              "climate.example_car": SimpleNamespace(state="off", attributes={"supported_features": 384}),
              "script.example_leave": SimpleNamespace(state="off", attributes={})}
    hass.states.get = states.get
    return hass, manager, watch_id, states

def test_config_has_panel_pages_but_keeps_device_allowlist_separate():
    hass, manager, watch_id, _ = with_panel(); watch = manager.watches[watch_id]
    assert configuration(watch)["entity"] == "fan.example"
    watch["active_panel"] = "work_morning"
    assert configuration(watch)["entity"] == "panel.work_morning"
    assert configuration(watch)["devices"][-1]["panel"]["battery_entity"] == "sensor.example_battery"
    assert not any(item["entity"].startswith(("panel.", "sensor.", "climate.")) for item in watch["devices"])

@pytest.mark.parametrize("key,value", [("battery_entity", "lock.example"), ("script_entity", "scene.example"), ("climate_entity", "sensor.example"), ("id", "devices"), ("script_label", "x" * 19)])
def test_invalid_roles_and_labels_rejected(key, value):
    with pytest.raises(ValueError): validate_panel({**PANEL, key: value})

def test_state_filters_attributes_and_checks_power_features():
    hass, _, _, states = with_panel()
    value = panel_state(hass, validate_panel(PANEL)); assert value["battery"] == 82 and value["climate_available"]
    assert "location" not in json.dumps(value)
    states["climate.example_car"].attributes["supported_features"] = 128
    assert not panel_state(hass, validate_panel(PANEL))["climate_available"]
    states["sensor.example_battery"].state = "unavailable"
    assert panel_state(hass, validate_panel(PANEL))["battery"] is None

@pytest.mark.asyncio
async def test_panel_commands_are_role_scoped_and_replay_protected():
    hass, manager, watch_id, _ = with_panel(); secret = manager.watches[watch_id]["secret"]
    value = packet(watch_id, secret, {"action": "panel_command", "panel_id": "work_morning", "command": "climate_on"})
    assert (await WatchView(hass).post(request(value))).status == 200
    hass.services.async_call.assert_awaited_once_with("climate", "turn_on", {"entity_id": "climate.example_car"}, blocking=True, context={"user_id": "example-owner"})
    assert (await WatchView(hass).post(request(value))).status == 403
    for body in ({"action": "panel_command", "panel_id": "other", "command": "leave"},
                 {"action": "panel_command", "panel_id": "work_morning", "command": "unlock"},
                 {"action": "state", "entity": "sensor.example_battery"}):
        assert (await WatchView(hass).post(request(packet(watch_id, secret, body)))).status == 403
    assert hass.services.async_call.await_count == 1

def test_running_script_is_not_dispatched_again():
    hass, _, _, states = with_panel(); states["script.example_leave"].state = "on"
    with pytest.raises(ValueError): panel_command(hass, validate_panel(PANEL), "leave")

@pytest.mark.asyncio
async def test_panel_flow_and_selector_do_not_control_physical_devices():
    hass, manager, watch_id, _ = with_panel(); manager.watches[watch_id]["panels"] = []
    flow = FanControlOptions(); flow.hass = hass
    assert (await flow.async_step_panels({"watch_id": watch_id}))["type"] == "menu"
    assert (await flow.async_step_add_panel())["step_id"] == "panel"
    assert (await flow.async_step_panel(PANEL))["type"] == "create_entry"
    selection = PanelSelection(hass, watch_id); assert selection.options == ["devices", "work_morning"]
    await selection.async_select_option("work_morning")
    assert selection.current_option == "work_morning"
    await selection.async_select_option("devices")
    assert manager.watches[watch_id]["entity"] == "fan.example"
    hass.services.async_call.assert_not_awaited()
    await flow.async_step_activate_panel({"panel_id": "work_morning"})
    await flow.async_step_remove_panel({"panel_id": "work_morning"})
    assert selection.options == ["devices"] and selection.current_option == "devices"


@pytest.mark.asyncio
async def test_active_panel_form_defaults_to_saved_selection():
    hass, manager, watch_id, _ = with_panel()
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = watch_id
    manager.watches[watch_id]["active_panel"] = "work_morning"
    result = await flow.async_step_activate_panel()
    key = next(key for key in result["data_schema"].schema if str(key) == "panel_id")
    assert key.default() == "work_morning"
    await flow.async_step_activate_panel({"panel_id": "devices"})
    assert configuration(manager.watches[watch_id])["entity"] == "fan.example"
    result = await flow.async_step_activate_panel()
    key = next(key for key in result["data_schema"].schema if str(key) == "panel_id")
    assert key.default() == "devices"


VEHICLE = {**PANEL, "wake_entity": "button.example_wake", "awake_entity": "binary_sensor.example_online"}

@pytest.mark.parametrize("status", ["off", "unknown", "unavailable", None])
def test_offline_vehicle_hides_cached_fields_and_blocks_controls(status):
    hass, _, _, states = with_panel()
    if status is not None:
        states["binary_sensor.example_online"] = SimpleNamespace(state=status, attributes={})
    panel = validate_panel(VEHICLE)
    value = panel_state(hass, panel)
    assert value["battery"] is None and not value["vehicle_ready"]
    assert not value["climate_available"] and not value["script_available"]
    for action in ("climate_on", "climate_off", "leave"):
        with pytest.raises(ValueError): panel_command(hass, panel, action)

def test_online_vehicle_controls_are_independent_of_unrelated_fields():
    hass, _, _, states = with_panel()
    states["binary_sensor.example_online"] = SimpleNamespace(state="on", attributes={})
    panel = validate_panel(VEHICLE)
    assert panel_state(hass, panel)["vehicle_ready"]
    states["climate.example_car"].state = "invalid"
    assert panel_state(hass, panel)["vehicle_ready"]
    assert not panel_state(hass, panel)["climate_available"]
    states["climate.example_car"].state = "off"
    states["sensor.example_battery"].state = "unknown"
    assert panel_state(hass, panel)["vehicle_ready"]
    assert panel_state(hass, panel)["climate_available"]

def test_wake_is_explicit_role_scoped_and_unknown_button_state_is_normal():
    hass, _, _, states = with_panel()
    states["button.example_wake"] = SimpleNamespace(state="unknown", attributes={})
    assert panel_command(hass, validate_panel(VEHICLE), "wake") == ("button", "press", {"entity_id": "button.example_wake"})
    with pytest.raises(ValueError): panel_command(hass, validate_panel(PANEL), "wake")
    states["button.example_wake"].state = "unavailable"
    with pytest.raises(ValueError): panel_command(hass, validate_panel(VEHICLE), "wake")
    hass.services.async_call.assert_not_awaited()

@pytest.mark.parametrize("fields", [{"wake_entity": "button.example"}, {"awake_entity": "binary_sensor.example"},
    {"wake_entity": "script.example", "awake_entity": "binary_sensor.example"}])
def test_partial_or_wrong_vehicle_roles_rejected(fields):
    with pytest.raises(ValueError): validate_panel({**PANEL, **fields})

@pytest.mark.asyncio
async def test_vehicle_configuration_and_state_reads_never_wake():
    hass, manager, watch_id, states = with_panel()
    manager.watches[watch_id]["panels"] = [validate_panel(VEHICLE)]
    secret = manager.watches[watch_id]["secret"]
    for body in ({"action": "config"}, {"action": "panel_state", "panel_id": "work_morning"}):
        assert (await WatchView(hass).post(request(packet(watch_id, secret, body)))).status == 200
    hass.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_vehicle_wake_uses_approved_button_and_replay_is_rejected():
    hass, manager, watch_id, states = with_panel()
    manager.watches[watch_id]["panels"] = [validate_panel(VEHICLE)]
    states["button.example_wake"] = SimpleNamespace(state="unknown", attributes={})
    secret = manager.watches[watch_id]["secret"]
    body = packet(watch_id, secret, {"action": "panel_command", "panel_id": "work_morning", "command": "wake"})
    assert (await WatchView(hass).post(request(body))).status == 200
    assert (await WatchView(hass).post(request(body))).status == 403
    hass.services.async_call.assert_awaited_once_with("button", "press", {"entity_id": "button.example_wake"}, blocking=True, context={"user_id": "example-owner"})
