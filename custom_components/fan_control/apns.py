"""Direct APNs transport. Keys remain on HA; only Apple receives push tokens."""
import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

PHONE_TOPIC = "dev.fancontrol.watch"
WATCH_TOPIC = "dev.fancontrol.watch.watchkitapp"
KEY_FILE = "fancontrol-apns.p8"
REQUEST_TIMEOUT = 15.0


def _redact_request_log(record):
    # APNs puts the device credential in the URL, including normal HTTPX INFO logs.
    text = record.getMessage()
    cleaned = re.sub(r"(https://api(?:\.sandbox)?\.push\.apple\.com/3/device/)[^\s\"']+", r"\1[redacted]", text)
    if cleaned != text:
        record.msg, record.args = cleaned, ()
    return True


logging.getLogger("httpx").addFilter(_redact_request_log)


def configured(settings):
    return bool(settings.get("enabled") and all(isinstance(settings.get(k), str)
                and re.fullmatch(r"[A-Z0-9]{10}", settings[k]) for k in ("key_id", "team_id")))


def credentials(hass, settings):
    if not configured(settings):
        raise ValueError("APNs key ID and team ID are required")
    # Fixed HA config file; no client-provided path or remote key URL.
    with Path(hass.config.path(KEY_FILE)).open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Invalid APNs key")
    key = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("APNs requires a P-256 signing key")
    return key


def provider_token(key, settings, issued):
    def encode(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")
    header = encode(json.dumps({"alg": "ES256", "kid": settings["key_id"]}, separators=(",", ":")).encode())
    claims = encode(json.dumps({"iss": settings["team_id"], "iat": issued}, separators=(",", ":")).encode())
    data = header + b"." + claims
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return (data + b"." + encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()


class APNs:
    def __init__(self, hass):
        self.hass = hass
        self.lock = asyncio.Lock()
        self.cached = None
        self.client = None

    async def reset(self):
        async with self.lock:
            self.cached = None
            client, self.client = self.client, None
        if client is not None:
            await client.aclose()

    async def http_client(self):
        # APNs connections belong to one provider key. Keep this pool separate
        # from other HA integrations and close it when signing credentials change.
        import httpx
        async with self.lock:
            if self.client is None:
                self.client = await self.hass.async_add_executor_job(lambda: httpx.AsyncClient(http2=True))
            return self.client

    async def token(self, settings):
        async with self.lock:
            now = int(time.time())
            if self.cached and self.cached[0] == settings and 0 <= now - self.cached[1] < 3000:
                return self.cached[2]
            def sign():
                return provider_token(credentials(self.hass, settings), settings, now)
            token = await self.hass.async_add_executor_job(sign)
            self.cached = (dict(settings), now, token)
            return token

    async def send(self, target, payload, *, settings, update_id, push_type, collapse_id, current):
        # Fixed hosts/topics and strict tokens prevent credentials leaking to another host.
        token = target.get("device_token", "")
        environment = target.get("environment")
        destination = target.get("destination", "watch")
        if (not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{64,200}", token)
                or environment not in ("sandbox", "production") or destination not in ("watch", "phone")
                or push_type not in ("alert", "background")):
            return {"http_status": 400, "error": "invalid_registration"}
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if len(raw) > 4096:
            return {"http_status": 400, "error": "payload_too_large"}
        import httpx
        try:
            authorization = await self.token(settings)
            client = await self.http_client()
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, TypeError, UnsupportedAlgorithm):
            return {"http_status": 503, "error": "apns_credentials_unavailable"}
        if not current():
            return {"http_status": 409, "error": "superseded"}
        try:
            host = "api.push.apple.com" if environment == "production" else "api.sandbox.push.apple.com"
            headers = {"authorization": "bearer " + authorization,
                       "apns-topic": PHONE_TOPIC if destination == "phone" else WATCH_TOPIC,
                       "apns-id": update_id, "apns-push-type": push_type,
                       "apns-priority": "10" if push_type == "alert" else "5",
                       "apns-expiration": str(int(time.time()) + 300),
                       "apns-collapse-id": collapse_id, "content-type": "application/json"}
            # No transport retries: a lost response cannot prove nondelivery.
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with client.stream("POST", "https://" + host + "/3/device/" + token,
                                         content=raw, headers=headers, follow_redirects=False,
                                         timeout=httpx.Timeout(REQUEST_TIMEOUT)) as response:
                    if response.http_version != "HTTP/2":
                        return {"http_status": 502, "error": "http2_required"}
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 4096:
                            break
                    try:
                        reason = json.loads(data).get("reason", "") if len(data) <= 4096 and data else ""
                    except (ValueError, AttributeError, UnicodeDecodeError):
                        reason = "Unknown"
                    if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z0-9]{0,80}", reason):
                        reason = "Unknown"
                    code = response.status_code
                    accepted = code == 200
                    return {"http_status": 202 if accepted else (410 if code == 410 or code == 400 and reason == "BadDeviceToken" else 502),
                            "status": "accepted_by_apns" if accepted else "rejected",
                            "apns_id": update_id, "apns_status": code, "apns_reason": reason,
                            "device_delivery_confirmed": False}
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, TimeoutError):
            return {"http_status": 0, "error": "delivery_unknown"}
