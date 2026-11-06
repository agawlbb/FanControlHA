from types import SimpleNamespace
import pytest
from custom_components.fan_control.panels import validate_panel, panel_state, panel_command, configuration
from custom_components.fan_control.companion import handle
from custom_components.fan_control.config_flow import FanControlOptions
from custom_components.fan_control.push import dependencies
from .test_notifications import setup

PANEL = {"type": "on_duty", "id": "on_duty", "name": "On Duty",
         "city_pair_entity": "sensor.airports", "departure_entity": "sensor.departure"}

def test_on_duty_reads_only_supplied_flight_and_rejects_commands():
    hass, manager, wid, push, watch = setup()
    values = {"sensor.airports": "TPA → CLT", "sensor.departure": "2026-11-05T18:52:00+00:00"}
    hass.states.get = lambda entity: SimpleNamespace(state=values[entity]) if entity in values else None
    panel = validate_panel(PANEL)
    assert panel_state(hass, panel) == {"city_pair": "TPA → CLT", "departure": values["sensor.departure"], "available": True}
    watch.update(panels=[panel], active_panel="on_duty")
    assert configuration(watch)["entity"] == "panel.on_duty"
    assert dependencies(watch) == set(values)
    for action in ("wake", "climate_on", "leave"):
        with pytest.raises(ValueError): panel_command(hass, panel, action)
    for departure in ("unknown", "unavailable", "bad", "2026-11-05T18:52:00"):
        values["sensor.departure"] = departure
        assert panel_state(hass, panel)["available"] is False
    hass.services.async_call.assert_not_awaited()

@pytest.mark.parametrize("role", ["city_pair_entity", "departure_entity"])
def test_roles_cannot_authorize_other_domains(role):
    with pytest.raises(ValueError): validate_panel({**PANEL, role: "button.wake"})

@pytest.mark.asyncio
async def test_native_panel_create_edit_and_snapshot_messages():
    hass, manager, wid, push, watch = setup()
    flow = FanControlOptions(); flow.hass = hass; flow.watch_id = wid
    assert (await flow.async_step_add_on_duty())["step_id"] == "on_duty"
    assert (await flow.async_step_on_duty(PANEL))["type"] == "create_entry"
    assert (await flow.async_step_edit_panel({"panel_id": "on_duty"}))["step_id"] == "on_duty"
    assert (await flow.async_step_on_duty({**PANEL, "id": "changed"}))["errors"]
    await push.notify_phone(wid, "Delayed", "Full alert text")
    message = watch["messages"][0]
    assert push.send.call_args.kwargs["update_id"] == message["id"]
    assert handle(hass, watch, {"action": "companion_snapshot"})["messages"] == [message]
    assert message["title"] == "Delayed" and message["message"] == "Full alert text"
    hass.data["fan_control"]["store"].async_save.assert_awaited()
    handle(hass, watch, {"action": "companion_unlink"})
    assert "messages" not in watch
