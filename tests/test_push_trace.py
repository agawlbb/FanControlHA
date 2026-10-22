import asyncio
import copy
import json
import uuid

import pytest

from custom_components.fan_control import WatchView
from custom_components.fan_control.diagnostics import async_get_config_entry_diagnostics
from custom_components.fan_control.push_trace import new_attempt, record_events
from .test_push import setup_push
from .test_protocol import packet
from .test_api import request


def event(watch_id, attempt, phase="received", **changes):
    return {"id": str(uuid.uuid4()), "update_id": attempt["update_id"], "watch_id": watch_id,
            "phase": phase, "at_ms": 1800000000000, "panel": attempt["panel"],
            "revision": attempt["revision"], **changes}


def test_receipts_are_idempotent_and_do_not_equate_receipt_with_apply():
    _, manager, watch_id, _ = setup_push()
    watch = manager.watches[watch_id]
    attempt = new_attempt(watch, "selection")
    received = event(watch_id, attempt)
    record_events(watch, watch_id, [received, received])
    assert attempt["receipt"] == "received" and len(attempt["events"]) == 1
    assert "selection_saved" not in attempt
    assert attempt["received_at_ms"] == received["at_ms"]
    assert "reported_at_ms" in attempt["events"][0]
    record_events(watch, watch_id, [event(watch_id, attempt, "selection_saved", revision=attempt["revision"] + 1)])
    assert not attempt["selection_saved"]["matches_attempt"]
    record_events(watch, watch_id, [event(watch_id, attempt, "selection_saved")])
    assert attempt["selection_saved"]["matches_attempt"]


def test_watch_scope_unknown_ids_invalid_batches_and_bounded_retention():
    _, manager, watch_id, _ = setup_push()
    watch = manager.watches[watch_id]
    for _ in range(130):
        new_attempt(watch, "selection")
    assert len(watch["push_history"]) == 120
    attempt = watch["push_history"][-1]
    unknown = event(watch_id, attempt, update_id=str(uuid.uuid4()))
    assert record_events(watch, watch_id, [unknown])["discarded"] == [unknown["id"]]
    before = copy.deepcopy(watch)
    for changes in ({"watch_id": "another_watch"}, {"phase":"command_complete"}, {"at_ms":True}, {"panel":"secret\ntext"}, {"update_id":"wrong"}):
        with pytest.raises(ValueError):
                record_events(watch, watch_id, [event(watch_id, attempt), {**event(watch_id, attempt), **changes}])
        assert watch == before # Invalid batches must not partially append evidence.
    with pytest.raises(ValueError):
        record_events(watch, watch_id, [event(watch_id, attempt)] * 13)


@pytest.mark.asyncio
async def test_attempt_persisted_before_send_and_failures_visible():
    hass, manager, watch_id, push = setup_push()
    watch = manager.watches[watch_id]
    async def failing_send(body):
        assert watch["push_history"][-1]["status"] == "attempted"
        assert hass.data["fan_control"]["store"].async_save.await_count > 0
        raise OSError("synthetic network failure")
    push.send = failing_send
    push.updated()
    await asyncio.gather(*push.tasks.values())
    assert watch["push_history"][-1]["status"] == "delivery_unknown"
    assert watch["push_history"][-1]["receipt"] == "unknown"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_encrypted_receipts_and_secret_free_admin_diagnostics():
    hass, manager, watch_id, push = setup_push()
    watch = manager.watches[watch_id]
    attempt = new_attempt(watch, "selection")
    body = {"action":"push_receipts", "events":[event(watch_id, attempt)]}
    response = await WatchView(hass).post(request(packet(watch_id, watch["secret"], body)))
    assert response.status == 200
    assert attempt["receipt"] == "received"
    # First display is still valuable when no push callback was observed.
    record_events(watch, watch_id, [event(watch_id, attempt, "opened", update_id=None)])
    diagnostic = await async_get_config_entry_diagnostics(hass, None)
    encoded = json.dumps(diagnostic)
    assert watch["secret"] not in encoded and "device_token" not in encoded
    assert push.settings["secret"] not in encoded and "owner_id" not in encoded
    assert diagnostic["watches"][0]["uncorrelated_watch_events"][0]["phase"] == "opened"
    assert diagnostic["watches"][0]["push_history"][0]["receipt"] == "received"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_acceptance_keeps_receipt_unknown_until_watch_reports():
    hass, manager, watch_id, push = setup_push()
    async def send(body):
        if body["action"] == "register":
            return {"http_status":200}
        return {"http_status":202,"status":"accepted_by_apns","apns_id":body["update_id"],"apns_status":200}
    push.send = send
    push.updated()
    await asyncio.gather(*push.tasks.values())
    attempt = manager.watches[watch_id]["push_history"][-1]
    assert attempt["status"] == "accepted_by_apns" and attempt["receipt"] == "unknown"
    assert attempt["relay_result"]["apns_id"] == attempt["update_id"]
    assert "selection_saved" not in attempt
    hass.services.async_call.assert_not_awaited()
