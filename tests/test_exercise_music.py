from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from custom_components.fan_control.exercise import validate_exercise, start_music

BASE = {"lock_entity":"lock.car", "climate_entity":"climate.car", "wake_entity":"button.wake", "awake_entity":"binary_sensor.online"}
URI = "spotify:playlist:" + "A" * 22

def panel(**extra):
    return validate_exercise({**BASE, "spotify_entity":"media_player.music", "spotify_playlist":URI, **extra})

def hass():
    player = SimpleNamespace(state="paused", attributes={"source_list":["Watch"]})
    return SimpleNamespace(states=SimpleNamespace(get=lambda _:player), services=SimpleNamespace(async_call=AsyncMock()))

def test_share_parameters_are_discarded():
    value = panel(spotify_playlist="https://open.spotify.com/playlist/" + "A"*22 + "?si=share&pt=private")
    assert value["spotify_playlist"] == URI
    assert "private" not in repr(value)

@pytest.mark.parametrize("value", ["https://example.com/playlist/" + "A"*22, "spotify:track:" + "A"*22, "invalid"])
def test_only_spotify_playlists_accepted(value):
    with pytest.raises(ValueError): panel(spotify_playlist=value)

@pytest.mark.asyncio
async def test_music_does_not_depend_on_vehicle_awake_and_orders_shuffle_before_and_after_play():
    instance = hass()
    assert await start_music(instance,panel(),None) == {"music_status":"requested"}
    calls = instance.services.async_call.call_args_list
    assert [c.args[:2] for c in calls] == [("media_player","shuffle_set"),("media_player","play_media"),("media_player","shuffle_set")]
    assert calls[1].args[2]["media_content_id"] == URI
    assert calls[0].args[2]["shuffle"] is False

@pytest.mark.asyncio
async def test_explicit_missing_source_never_falls_back_to_another_device():
    instance = hass()
    assert await start_music(instance,panel(spotify_source="Missing watch"),None) == {"music_status":"source_unavailable"}
    instance.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_existing_panels_without_music_make_no_media_calls():
    instance = hass()
    assert await start_music(instance,validate_exercise(BASE),None) == {"music_status":"not_configured"}
    instance.services.async_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_failed_request_is_not_replayed():
    instance = hass()
    instance.services.async_call.side_effect = RuntimeError()
    assert await start_music(instance,panel(),None) == {"music_status":"unconfirmed"}
    assert instance.services.async_call.await_count == 1

@pytest.mark.asyncio
async def test_music_settings_do_not_invalidate_watch_stop_role_snapshot():
    from custom_components.fan_control.exercise import retry_stop, STOP_SERVER_STARTED_MS
    instance = hass()
    instance.states.get = lambda _: None  # No physical actions; only validate the approved snapshot.
    now = STOP_SERVER_STARTED_MS + 1000
    body = {"operation_id":"12345678-1234-1234-1234-123456789abc", "issued_at_ms":now,
            "panel_config":validate_exercise(BASE)}
    result = await retry_stop(instance,panel(),None,{},"watch",body,now)
    assert result["operation_status"] == "pending"
    body["panel_config"]["lock_entity"] = "lock.changed"
    with pytest.raises(ValueError): await retry_stop(instance,panel(),None,{},"watch",body,now)
