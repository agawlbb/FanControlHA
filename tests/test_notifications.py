"""Automation alerts are phone-only and never call physical services."""
import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from custom_components.fan_control.notifications import SCHEMA, register_service
from .test_push import setup_push


def setup():
    hass, manager, wid, push = setup_push()
    watch = manager.watches[wid]
    watch["companion"] = {"secret": "synthetic-phone-key"}
    watch["push_registration"]["destination"] = "phone"
    return hass, manager, wid, push, watch


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"message": ""}, {"message": "x" * 1501},
    {"message": "😀" * 376}, {"title": "😀" * 51}, {"message": "bad\x00text"},
    {"message": ["invalid"]}, {"message": "\ud800"}, {"extra": "field"},
])
def test_schema_rejects_invalid_or_oversized_text(changes):
    with pytest.raises(Exception):
        SCHEMA({"device_id": "device", "message": "Hello", **changes})


def test_schema_defaults_and_unicode():
    value = SCHEMA({"device_id": "device", "message": "Door open\n😀"})
    assert value["title"] == "Fan Control" and value["message"] == "Door open\n😀"


@pytest.mark.asyncio
async def test_visible_alert_uses_separate_phone_identity_without_refresh_or_command():
    hass, manager, wid, push, watch = setup()
    await push.notify_phone(wid, "Garage", "Still open\n😀")
    push.send.assert_awaited_once()
    target, notification = push.send.call_args.args
    first_id = push.send.call_args.kwargs["update_id"]
    assert target == watch["push_registration"]
    assert notification["fancontrol"]["watch_id"] == wid
    assert notification["aps"]["alert"] == {"title":"Garage", "body":"Still open\n😀"}
    assert push.send.call_args.kwargs["push_type"] == "alert"
    assert watch["push_registration"]["destination"] == "phone"
    assert not push.tasks
    assert "messages" not in watch
    assert notification["aps"]["content-available"] == 1
    assert notification["fancontrol"]["sent_at_ms"] > 0
    push.runtime["store"].async_save.assert_not_awaited()
    hass.services.async_call.assert_not_awaited()
    await push.notify_phone(wid, "Garage", "Still open")
    assert push.send.call_args.kwargs["update_id"] != first_id


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "no_companion", "watch_route", "disabled", "closed", "unloading", "owner", "missing_owner"])
async def test_unavailable_recipients_never_dispatch(change):
    hass, manager, wid, push, watch = setup()
    if change == "missing": manager.watches.clear()
    elif change == "no_companion": watch.pop("companion")
    elif change == "watch_route": watch["push_registration"].pop("destination")
    elif change == "disabled": push.settings["enabled"] = False
    elif change == "closed": push.closed = True
    elif change == "unloading": push.runtime["unloading"] = True
    elif change == "owner": hass.auth.async_get_user.return_value.is_active = False
    elif change == "missing_owner": hass.auth.async_get_user.return_value = None
    with pytest.raises(HomeAssistantError): await push.notify_phone(wid, "Title", "Message")
    push.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "unlink", "token", "owner", "settings", "close"])
async def test_recipient_change_before_signing_prevents_alert(change):
    hass, manager, wid, push, watch = setup()
    async def save_snapshot(*args):
        if change == "revoke": manager.watches.clear()
        elif change == "unlink": watch.pop("companion")
        elif change == "token": watch["push_registration"]["device_token"] = "b" * 64
        elif change == "owner": hass.auth.async_get_user.return_value.is_active = False
        elif change == "settings": push.settings["team_id"] = "other"
        elif change == "close": push.closed = True
    owner = hass.auth.async_get_user.return_value
    calls = [0]
    async def get_owner(*args):
        calls[0] += 1
        if calls[0] == 2: await save_snapshot()
        return owner
    hass.auth.async_get_user.side_effect = get_owner
    with pytest.raises(HomeAssistantError): await push.notify_phone(wid, "Title", "Message")
    push.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [410, 429, 502, TimeoutError("private-token-and-message")])
async def test_failed_or_ambiguous_dispatch_is_not_retried_or_reported_successful(response):
    hass, manager, wid, push, watch = setup()
    push.send.side_effect = [response]
    with pytest.raises(HomeAssistantError) as error: await push.notify_phone(wid, "Title", "Message")
    assert "private-token" not in str(error.value)
    assert push.send.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    hass, manager, wid, push, watch = setup()
    push.send.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError): await push.notify_phone(wid, "Title", "Message")
    assert push.send.await_count == 1


@pytest.mark.asyncio
async def test_admin_service_registration_and_device_scope(monkeypatch):
    hass, manager, wid, push, watch = setup()
    push.notify_phone = AsyncMock()
    push.runtime["entry"] = SimpleNamespace(entry_id="entry")
    device = SimpleNamespace(identifiers={("fan_control", wid)}, config_entries={"entry"})
    registry = SimpleNamespace(async_get=lambda identity: device if identity == "device" else None)
    dr = ModuleType("homeassistant.helpers.device_registry")
    dr.async_get = lambda _: registry
    service = ModuleType("homeassistant.helpers.service")
    calls = []
    service.async_register_admin_service = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, dr.__name__, dr)
    monkeypatch.setitem(sys.modules, service.__name__, service)
    register_service(hass, push.runtime)
    args, kwargs = calls[0]
    assert args[1:3] == ("fan_control", "send_notification")
    assert kwargs["schema"] is SCHEMA
    assert len(calls) == 1 # No server-side clear-all action remains.
    handler = args[3]
    await handler(SimpleNamespace(data=SCHEMA({"device_id": "device", "message": "Hello"})))
    push.notify_phone.assert_awaited_once_with(wid, "Fan Control", "Hello")
    for identity in ("missing", "device"):
        device.config_entries = {"other-entry"}
        with pytest.raises(HomeAssistantError):
            await handler(SimpleNamespace(data={"device_id": identity, "title": "Title", "message": "Hello"}))
    assert push.notify_phone.await_count == 1
