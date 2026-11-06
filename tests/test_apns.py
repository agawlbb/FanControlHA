"""Direct Apple delivery with synthetic keys and an in-process HTTP transport."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from custom_components.fan_control.apns import APNs, credentials, PHONE_TOPIC, WATCH_TOPIC
from .test_push import setup_push

SETTINGS = {"enabled": True, "team_id": "SYNTHETIC1", "key_id": "SYNTHETIC2"}
UPDATE = "12345678-1234-4234-a234-123456789abc"
TARGET = {"device_token": "a" * 64, "environment": "production", "destination": "phone"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    key_file = tmp_path / "fancontrol-apns.p8"
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    hass = SimpleNamespace(config=SimpleNamespace(path=lambda name: str(tmp_path / name)),
                           async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)))
    requests = []
    response = {"status": 200, "body": {}, "http_version": b"HTTP/2"}
    def handler(request):
        requests.append(request)
        if "exception" in response: raise response["exception"]
        return httpx.Response(response["status"], json=response["body"], extensions={"http_version":response["http_version"]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    apns = APNs(hass)
    apns.client = client
    return apns, key, key_file, requests, response, client


async def deliver(apns, target=None, **kwargs):
    return await apns.send(target or TARGET, {"aps": {"alert": {"title": "Title", "body": "Body"}}},
                           settings=SETTINGS, update_id=UPDATE, push_type="alert", collapse_id="fc-"+UPDATE,
                           current=kwargs.get("current", lambda: True))


@pytest.mark.asyncio
@pytest.mark.parametrize("environment,host", [("production","api.push.apple.com"),("sandbox","api.sandbox.push.apple.com")])
@pytest.mark.parametrize("destination,topic", [("phone",PHONE_TOPIC),("watch",WATCH_TOPIC)])
async def test_fixed_apple_hosts_topics_and_real_es256_signature(setup, environment, host, destination, topic):
    apns, key, _, requests, _, client = setup
    result = await deliver(apns, {**TARGET,"environment":environment,"destination":destination})
    assert result == {"http_status":202,"status":"accepted_by_apns","apns_id":UPDATE,"apns_status":200,"apns_reason":"","device_delivery_confirmed":False}
    request = requests[0]
    assert str(request.url) == "https://"+host+"/3/device/"+TARGET["device_token"]
    assert request.headers["apns-topic"] == topic
    assert request.headers["apns-priority"] == "10" and request.headers["apns-push-type"] == "alert"
    assert request.headers["apns-collapse-id"] == "fc-"+UPDATE
    header, claims, signature = request.headers["authorization"].removeprefix("bearer ").split(".")
    decode = lambda raw: base64.urlsafe_b64decode(raw+"="*(-len(raw)%4))
    assert json.loads(decode(header)) == {"alg":"ES256","kid":"SYNTHETIC2"}
    assert json.loads(decode(claims))["iss"] == "SYNTHETIC1"
    raw = decode(signature)
    assert len(raw) == 64
    key.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32],"big"),int.from_bytes(raw[32:],"big")),
                            (header+"."+claims).encode(),ec.ECDSA(hashes.SHA256()))
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_token_reuse_concurrency_refresh_and_rotation(setup, monkeypatch):
    apns, _, _, _, _, client = setup
    clock = [1800000000]
    monkeypatch.setattr("custom_components.fan_control.apns.time.time",lambda:clock[0])
    tokens = await asyncio.gather(*(apns.token(SETTINGS) for _ in range(5)))
    assert len(set(tokens)) == 1 and apns.hass.async_add_executor_job.await_count == 1
    clock[0] += 2999
    assert await apns.token(SETTINGS) == tokens[0]
    clock[0] += 1
    assert await apns.token(SETTINGS) != tokens[0]
    await apns.reset()
    await apns.token(SETTINGS)
    assert apns.hass.async_add_executor_job.await_count == 3
    await apns.token({**SETTINGS,"key_id":"SYNTHETIC3"})
    assert apns.hass.async_add_executor_job.await_count == 4
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,reason,expected", [(400,"BadDeviceToken",410),(410,"Unregistered",410),(403,"InvalidProviderToken",502),
                                                 (429,"TooManyRequests",502),(500,"InternalServerError",502),(302,"",502)])
async def test_rejections_never_claim_delivery_or_retry(setup, code, reason, expected):
    apns, _, _, requests, response, client = setup
    response.update(status=code,body={"reason":reason})
    result = await deliver(apns)
    assert result["http_status"] == expected and result["apns_status"] == code
    assert result["status"] == "rejected" and not result["device_delivery_confirmed"]
    assert len(requests) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_and_http11_cannot_be_success(setup):
    apns, _, _, requests, response, client = setup
    response["exception"] = httpx.ReadTimeout("private-token-and-message")
    result = await deliver(apns)
    assert result == {"http_status":0,"error":"delivery_unknown"}
    assert len(requests) == 1 and "private-token" not in json.dumps(result)
    response["exception"] = OSError("private-socket-error")
    assert (await deliver(apns)) == {"http_status":0,"error":"delivery_unknown"}
    response.pop("exception");response["http_version"] = b"HTTP/1.1"
    assert (await deliver(apns))["error"] == "http2_required"
    await client.aclose()


@pytest.mark.asyncio
async def test_scope_rechecked_after_signing_await(setup):
    apns, _, _, requests, _, client = setup
    scope = [True]
    original = apns.hass.async_add_executor_job.side_effect
    def sign(*args):
        scope[0] = False
        return original(*args)
    apns.hass.async_add_executor_job.side_effect = sign
    assert (await deliver(apns,current=lambda:scope[0]))["http_status"] == 409
    assert not requests
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_key_or_invalid_ids_do_not_connect(setup):
    apns, _, key_file, requests, _, client = setup
    key_file.unlink()
    assert (await deliver(apns))["error"] == "apns_credentials_unavailable"
    with pytest.raises(ValueError): credentials(apns.hass,{**SETTINGS,"key_id":"bad"})
    assert not requests
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"device_token":"secret/../../host"},{"environment":"https://other.invalid"},{"destination":"other"}])
async def test_invalid_target_never_connects(setup, changes):
    apns, _, _, requests, _, client = setup
    assert (await deliver(apns,{**TARGET,**changes}))["error"] == "invalid_registration"
    assert not requests
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_token_only_removes_matching_registration():
    hass, manager, wid, push = setup_push()
    # Exercise the coordinator's real expiry path rather than its send mock.
    del push.send
    target = dict(manager.watches[wid]["push_registration"])
    push.apns.send = AsyncMock(return_value={"http_status":410})
    await push.send(target,{},update_id=UPDATE,push_type="background",collapse_id="refresh",current=lambda:True)
    assert "push_registration" not in manager.watches[wid]
    manager.watches[wid]["push_registration"] = {**target,"device_token":"b"*64}
    await push.send(target,{},update_id=UPDATE,push_type="background",collapse_id="refresh",current=lambda:True)
    assert manager.watches[wid]["push_registration"]["device_token"] == "b"*64


@pytest.mark.asyncio
async def test_legacy_settings_do_not_enable_direct_push_without_signing_metadata():
    hass, manager, wid, push = setup_push()
    push.runtime["push_settings"] = {"enabled": True, "installation": "old", "secret": "old-relay-secret"}
    push.updated()
    assert not push.enabled and not push.tasks
    push.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_token_expiry_handles_implicit_watch_destination():
    hass, manager, wid, push = setup_push()
    del push.send
    watch = manager.watches[wid]
    watch["command_push_registration"] = {"device_token":"b"*64,"environment":"production"}
    push.apns.send = AsyncMock(return_value={"http_status":410})
    await push.send({**watch["command_push_registration"],"destination":"watch"},{},update_id=UPDATE,
                    push_type="alert",collapse_id="command",current=lambda:True)
    assert "command_push_registration" not in watch and "push_registration" in watch


@pytest.mark.asyncio
async def test_background_refresh_has_low_priority_and_no_visible_alert(setup):
    apns, _, _, requests, _, client = setup
    payload={"aps":{"content-available":1},"fancontrol":{"refresh":True,"watch_id":"synthetic","update_id":UPDATE}}
    result=await apns.send(TARGET,payload,settings=SETTINGS,update_id=UPDATE,push_type="background",
                           collapse_id="fancontrol-refresh",current=lambda:True)
    assert result["http_status"] == 202
    assert requests[0].headers["apns-priority"] == "5"
    assert requests[0].headers["apns-push-type"] == "background"
    assert json.loads(requests[0].content) == payload
    await client.aclose()


@pytest.mark.asyncio
async def test_wrong_curve_key_and_cancellation_never_dispatch(setup):
    apns, _, key_file, requests, _, client = setup
    key=ec.generate_private_key(ec.SECP384R1())
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    assert (await deliver(apns))["error"] == "apns_credentials_unavailable"
    apns.token=AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError): await deliver(apns)
    assert not requests
    await client.aclose()


@pytest.mark.asyncio
async def test_signing_rotation_closes_only_its_own_connection_pool(setup):
    apns, _, _, _, _, client = setup
    await apns.token(SETTINGS)
    assert not client.is_closed and apns.cached is not None
    await apns.reset()
    assert client.is_closed and apns.client is None and apns.cached is None


@pytest.mark.asyncio
async def test_total_network_deadline_cancels_dispatch_without_retry(setup, monkeypatch):
    apns, _, _, requests, _, old_client = setup
    await old_client.aclose()
    async def hanging(request):
        requests.append(request)
        await asyncio.Event().wait()
    apns.client=httpx.AsyncClient(transport=httpx.MockTransport(hanging))
    monkeypatch.setattr("custom_components.fan_control.apns.REQUEST_TIMEOUT",0.01)
    assert (await deliver(apns)) == {"http_status":0,"error":"delivery_unknown"}
    assert len(requests) == 1
    await apns.reset()


def test_httpx_request_logging_redacts_device_credentials():
    import logging
    from custom_components.fan_control.apns import _redact_request_log
    for host in ("api.push.apple.com", "api.sandbox.push.apple.com"):
        record=logging.LogRecord("httpx",logging.INFO,"",1,"HTTP Request: POST %s HTTP/2 200",("https://"+host+"/3/device/"+"a"*64,),None)
        assert _redact_request_log(record)
        assert "a"*64 not in record.getMessage() and "[redacted]" in record.getMessage()
