import pytest
from custom_components.fan_control.panels import validate_panel, configuration
from custom_components.fan_control.config_flow import FanControlOptions
from .test_driving import PANEL as DRIVING, setup_driving
from .test_panels import PANEL as MORNING

EXERCISE = {"type":"exercise", "id":"exercise", "name":"Exercise", "lock_entity":"lock.example", "climate_entity":"climate.example",
            "wake_entity":"button.wake", "awake_entity":"binary_sensor.online"}

@pytest.mark.parametrize("panel", [DRIVING, MORNING, EXERCISE])
def test_optional_colors_preserve_legacy_panels_and_cannot_mutate_caller(panel):
    assert "color" not in validate_panel(panel)
    color = [32, 90, 255]
    result = validate_panel({**panel, "color": color})
    assert result["color"] == color
    color[0] = 0
    assert result["color"] == [32, 90, 255]

@pytest.mark.parametrize("color", [None, "blue", [1], [1,2,3,4], [-1,0,0], [256,0,0], [True,0,0], [1.5,0,0]])
@pytest.mark.parametrize("panel", [DRIVING, MORNING, EXERCISE])
def test_invalid_colors_are_rejected_for_every_panel_type(panel, color):
    with pytest.raises(ValueError): validate_panel({**panel, "color": color})

@pytest.mark.asyncio
async def test_native_driving_form_has_color_picker_and_edit_preserves_color_without_physical_actions():
    hass, manager, watch_id, states = setup_driving()
    flow=FanControlOptions(); flow.hass=hass; flow.watch_id=watch_id; flow.editing_panel="driving"
    form=await flow.async_step_driving()
    assert any(str(key)=="color" for key in form["data_schema"].schema)
    assert (await flow.async_step_driving({**DRIVING,"color":[255,255,255]}))["type"]=="create_entry"
    saved=manager.watches[watch_id]["panels"][0]
    assert saved["color"]==[255,255,255]
    assert configuration(manager.watches[watch_id])["devices"][-1]["panel"]["color"]==[255,255,255]
    hass.services.async_call.assert_not_awaited()
