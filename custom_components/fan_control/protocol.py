"""HA-independent authenticated encryption and validation. No credentials in logs."""
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .const import MAX_DEVICES, SUPPORTED_DOMAINS


def encode(value):
    return base64.b64encode(value).decode("ascii")


def decode(value, maximum=24576):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Invalid message")
    return base64.b64decode(value, validate=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def public_key(value):
    raw = decode(value, 1024)
    key = serialization.load_der_public_key(raw)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size != 2048 or key.public_numbers().e != 65537:
        raise ValueError("Invalid watch key")
    return key, hashlib.sha256(raw).hexdigest()[:12].upper()


def envelope(key, code, payload):
    secret, iv = secrets.token_bytes(32), secrets.token_bytes(12)
    return {
        "version": 1,
        "wrapped_key": encode(key.encrypt(secret, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))),
        "iv": encode(iv),
        "ciphertext": encode(AESGCM(secret).encrypt(iv, json.dumps(payload).encode(), code.encode("ascii"))),
    }


def address(value, external=False):
    value = value.strip().rstrip("/")
    uri = urlsplit(value)
    if uri.scheme not in (("https",) if external else ("http", "https")) or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError("Invalid address")
    if uri.scheme == "http":
        import ipaddress
        host = uri.hostname
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_private or ip.is_unspecified or ip.is_multicast:
                raise ValueError("Local HTTP only")
        except ValueError:
            if not host.endswith(".local"):
                raise ValueError("Local HTTP only") from None
    return value


def devices(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_DEVICES or len(set(values)) != len(values):
        raise ValueError("Select 1–16 distinct devices")
    for entity in values:
        if not isinstance(entity, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", entity) or entity.split(".")[0] not in SUPPORTED_DOMAINS:
            raise ValueError("Unsupported device")
    return list(values)


def aad(packet):
    watch_id, timestamp, nonce = packet["watch_id"], packet["timestamp"], packet["nonce"]
    if not isinstance(watch_id, str) or not re.fullmatch(r"[a-f0-9]{32}", watch_id) or type(timestamp) is not int or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("Invalid message")
    return f"fan-control-v1:{watch_id}:{timestamp}:{nonce}".encode("ascii")


def open_request(secret, packet):
    iv = decode(packet["iv"], 32)
    if len(iv) != 12:
        raise ValueError("Invalid message")
    return json.loads(AESGCM(bytes.fromhex(secret)).decrypt(iv, decode(packet["ciphertext"]), aad(packet)))


def seal_response(secret, packet, result):
    iv = secrets.token_bytes(12)
    return {"iv": encode(iv), "ciphertext": encode(AESGCM(bytes.fromhex(secret)).encrypt(iv, json.dumps(result).encode(), aad(packet) + b":response"))}
