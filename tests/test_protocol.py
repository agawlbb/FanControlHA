import json
import secrets
import time
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from custom_components.fan_control.manager import Manager
from custom_components.fan_control.protocol import aad, address, decode, devices, encode, seal_response

CONFIG = {"url": "http://192.168.1.2:8123", "remote_url": "https://ha.example.com", "entity": "fan.example", "devices": [{"entity": "fan.example", "title": "Fan"}], "owner_id": "example-owner", "name": "Example watch"}

@pytest.fixture
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def session(manager, keys):
    return manager.create(encode(keys.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)))

def packet(watch_id, secret, body, timestamp=None):
    value = {"watch_id": watch_id, "timestamp": int(time.time()) if timestamp is None else timestamp, "nonce": secrets.token_hex(16)}
    iv = secrets.token_bytes(12)
    value.update(iv=encode(iv), ciphertext=encode(AESGCM(bytes.fromhex(secret)).encrypt(iv, json.dumps(body).encode(), aad(value))))
    return value

def test_pairing_encrypted_and_single_use(keys):
    manager = Manager()
    created = session(manager, keys)
    assert manager.poll(created["device_token"]) == {"status": "pending"}
    watch_id = manager.approve(created["code"], CONFIG)
    result = manager.poll(created["device_token"])
    encrypted = result["envelope"]
    key = keys.decrypt(decode(encrypted["wrapped_key"]), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    payload = json.loads(AESGCM(key).decrypt(decode(encrypted["iv"]), decode(encrypted["ciphertext"]), created["code"].encode()))
    assert payload["watch_id"] == watch_id
    assert payload["token"] == manager.watches[watch_id]["secret"]
    assert payload["remote_url"] == CONFIG["remote_url"]
    assert "secret" not in json.dumps(result) and CONFIG["entity"] not in json.dumps(result)
    assert manager.poll(created["device_token"]) == result
    with pytest.raises(ValueError): manager.approve(created["code"], CONFIG)
    manager.poll(created["device_token"], acknowledge=True)
    with pytest.raises(ValueError): manager.poll(created["device_token"])

def test_pairing_expiry_and_wrong_token(keys):
    now = [1000]
    manager = Manager(clock=lambda: now[0]); created = session(manager, keys)
    with pytest.raises(ValueError): manager.poll("0" * 64)
    now[0] += 601
    with pytest.raises(ValueError): manager.lookup(created["code"])
    assert not manager.sessions

def test_rejects_bad_keys_and_limits_pending(keys):
    manager = Manager()
    with pytest.raises(ValueError): manager.create(encode(b"invalid"))
    for _ in range(32): session(manager, keys)
    with pytest.raises(ValueError): session(manager, keys)

def test_request_replay_tamper_expiry_and_revocation(keys):
    manager = Manager(); created = session(manager, keys); watch_id = manager.approve(created["code"], CONFIG)
    secret = manager.watches[watch_id]["secret"]
    value = packet(watch_id, secret, {"action": "config"})
    watch, body = manager.authenticate(value); assert body["action"] == "config"
    with pytest.raises(ValueError): manager.authenticate(value)
    restarted = Manager(manager.watches.copy(), nonces=manager.nonces.copy())
    with pytest.raises(ValueError): restarted.authenticate(value)
    changed = packet(watch_id, secret, {"action": "config"}); changed["nonce"] = "0" * 32
    with pytest.raises(InvalidTag): manager.authenticate(changed)
    with pytest.raises(ValueError): manager.authenticate(packet(watch_id, secret, {}, int(time.time()) - 61))
    encrypted = seal_response(secret, value, {"state": "on"})
    assert json.loads(AESGCM(bytes.fromhex(secret)).decrypt(decode(encrypted["iv"]), decode(encrypted["ciphertext"]), aad(value) + b":response"))["state"] == "on"
    del manager.watches[watch_id]
    with pytest.raises(ValueError): manager.authenticate(packet(watch_id, secret, {}))

@pytest.mark.parametrize("value", ["http://example.com", "https://user:secret@example.com", "https://example.com?secret=value", "ftp://example.com", "http://999.999.999.999"])
def test_unsafe_addresses_rejected(value):
    with pytest.raises(ValueError): address(value)

def test_addresses_and_device_limits():
    assert address("http://192.168.1.2:8123/") == "http://192.168.1.2:8123"
    assert address("http://homeassistant.local:8123")
    with pytest.raises(ValueError): address("http://192.168.1.2:8123", external=True)
    assert devices(["fan.example", "light.example", "script.example"])
    for values in ([], ["fan.example"] * 2, ["lock.example"], [f"fan.example{i}" for i in range(17)]):
        with pytest.raises(ValueError): devices(values)

def test_only_durable_watch_submission_may_arrive_after_normal_request_window(keys):
    manager = Manager(); created = session(manager, keys); wid = manager.approve(created["code"], CONFIG)
    secret = manager.watches[wid]["secret"]
    old = int(time.time()) - 90
    manager.authenticate(packet(wid, secret, {"action":"operation_submit"}, old))
    with pytest.raises(ValueError): manager.authenticate(packet(wid, secret, {"action":"command"}, old))
    with pytest.raises(ValueError): manager.authenticate(packet(wid, secret, {"action":"operation_submit"}, int(time.time()) - 181))
