"""Passive, per-watch APNs hints. Never invokes an HA service or vehicle wake."""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

from .panels import configuration, panel_state
from .push_trace import new_attempt

RELAY_URL = "https://coffeybean.com/FanControl/"
_LOGGER = logging.getLogger(__name__)


def registration(body):
    token, environment = body.get("device_token"), body.get("environment")
    if (not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{64,200}", token)
            or environment not in ("sandbox", "production")):
        raise ValueError("Invalid push registration")
    return {"device_token": token.lower(), "environment": environment}


def selection(watch):
    config = configuration(watch)
    return next((device for device in config["devices"] if device["entity"] == config["entity"]), None)


def dependencies(watch):
    device = selection(watch)
    if not device:
        return set()
    panel = device.get("panel")
    if not panel:
        return {device["entity"]}
    # Wake buttons dispatch commands; their last-pressed timestamps aren't status.
    return {value for key, value in panel.items() if key.endswith("_entity")
            and key != "wake_entity" and isinstance(value, str) and value}


def observation(hass, watch):
    device = selection(watch)
    if not device:
        return None
    if device.get("panel"):
        return panel_state(hass, device["panel"])
    state = hass.states.get(device["entity"])
    return {"state": state.state if state else "unavailable", "attributes": {
        key: state.attributes[key] for key in ("percentage", "supported_features", "friendly_name")
        if state and key in state.attributes}}


def headers(settings, raw, timestamp=None, nonce=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or secrets.token_hex(16)
    signature = hmac.new(settings["secret"].encode(), timestamp.encode()+b"\n"+nonce.encode()+b"\n"+raw, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-FanControl-Installation": settings["installation"],
            "X-FanControl-Timestamp": timestamp, "X-FanControl-Nonce": nonce, "X-FanControl-Signature": signature}


class PushCoordinator:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.snapshots = {}
        self.pending = {}
        self.tasks = {}
        self.registered = {}
        self.closed = False

    @property
    def settings(self):
        return self.runtime.get("push_settings", {})

    def updated(self):
        """Called after persisted configuration changes, including Stop selection."""
        watches = self.runtime["manager"].watches
        for watch_id in set(self.snapshots) - set(watches):
            self.snapshots.pop(watch_id, None)
            self.queue(watch_id, "unregister")
        for watch_id, watch in watches.items():
            if watch.get("platform") != "watch_os":
                continue
            try:
                config = configuration(watch)
                snapshot = (config, observation(self.hass, watch))
            except (ValueError, KeyError, TypeError):
                continue
            old = self.snapshots.get(watch_id)
            self.snapshots[watch_id] = snapshot
            if old and old[0] != config:
                reason = "selection" if old[0]["entity"] != config["entity"] else "configuration"
                self.queue(watch_id, reason)
            elif watch.get("push_registration"):
                # Startup and token replacement must sync registration, too.
                if self.registered.get(watch_id) != watch["push_registration"]:
                    self.queue(watch_id, "configuration")

    def state_changed(self, event):
        entity = event.data.get("entity_id")
        for watch_id, watch in self.runtime["manager"].watches.items():
            if watch.get("platform") != "watch_os" or watch_id not in self.snapshots:
                continue
            try:
                if entity not in dependencies(watch):
                    continue
                value = observation(self.hass, watch)
                config, old = self.snapshots[watch_id]
                self.snapshots[watch_id] = (config, value)
                if old != value:
                    self.queue(watch_id, "active_panel_state")
            except (ValueError, KeyError, TypeError):
                continue

    def queue(self, watch_id, reason):
        if self.closed or not self.settings.get("enabled"):
            return
        # Prefer selection/configuration over ordinary state bursts.
        if reason != "active_panel_state" or watch_id not in self.pending:
            self.pending[watch_id] = reason
        if watch_id not in self.tasks:
            self.tasks[watch_id] = self.hass.async_create_background_task(self.flush(watch_id), "FanControl push hint")

    async def send(self, body):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        from aiohttp import ClientTimeout
        raw = json.dumps(body, separators=(",", ":")).encode()
        session = async_get_clientsession(self.hass)
        async with session.post(RELAY_URL, data=raw, headers=headers(self.settings, raw),
                                allow_redirects=False, timeout=ClientTimeout(total=15)) as result:
            # Bounded structured evidence only; never retain token/credential fields.
            data = bytearray()
            async for chunk in result.content.iter_chunked(1024):
                data.extend(chunk)
                if len(data) > 4096:
                    break
            try:
                payload = json.loads(data) if len(data) <= 4096 else {}
            except (ValueError, UnicodeDecodeError):
                payload = {}
            evidence = {"http_status": result.status}
            if isinstance(payload, dict):
                for key in ("status", "error", "apns_id", "apns_status", "apns_reason"):
                    value = payload.get(key)
                    if type(value) is int or isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_. -]{1,100}", value):
                        evidence[key] = value
            return evidence

    async def persist(self):
        from . import save
        async with self.runtime["lock"]:
            await save(self.runtime)

    @staticmethod
    def status(result):
        return result if isinstance(result, int) else result["http_status"]

    async def flush(self, watch_id):
        attempt = None
        try:
            await asyncio.sleep(1)
            while watch_id in self.pending and self.settings.get("enabled") and not self.closed:
                reason = self.pending.pop(watch_id)
                watch = self.runtime["manager"].watches.get(watch_id)
                if watch is None:
                    await self.send({"action": "unregister", "watch_id": watch_id})
                    self.registered.pop(watch_id, None)
                    continue
                owner = await self.hass.auth.async_get_user(watch["owner_id"])
                if owner is None or not owner.is_active or not owner.is_admin:
                    continue
                target = watch.get("push_registration")
                attempt = new_attempt(watch, reason)
                _LOGGER.info("FanControl push attempt %s watch=%s revision=%s", attempt["update_id"], watch_id, attempt["revision"])
                await self.persist() # Save the attempt before any relay network work.
                if not target or watch.get("platform") != "watch_os":
                    attempt["status"] = "not_registered"
                    await self.persist()
                    attempt = None
                    continue
                target = dict(target)
                if self.registered.get(watch_id) != target:
                    status = await self.send({"action": "register", "watch_id": watch_id, **target})
                    if self.status(status) != 200:
                        attempt.update(status="registration_failed", registration_result=status)
                        await self.persist()
                        _LOGGER.warning("FanControl push registration unavailable (HTTP %s)", status)
                        attempt = None
                        continue
                    self.registered[watch_id] = target
                # Recheck revocation/token replacement after every network await.
                if self.runtime["manager"].watches.get(watch_id) is not watch or watch.get("push_registration") != target:
                    attempt["status"] = "superseded"
                    await self.persist()
                    continue
                status = await self.send({"action": "refresh", "watch_id": watch_id, "reason": reason, "update_id": attempt["update_id"]})
                attempt.update(status="accepted_by_apns" if self.status(status) == 202 else "relay_failed", relay_result=status,
                               responded_at_ms=int(time.time() * 1000))
                _LOGGER.info("FanControl push result %s status=%s receipt=unknown", attempt["update_id"], attempt["status"])
                await self.persist()
                attempt = None
                if self.status(status) == 410:
                    self.registered.pop(watch_id, None)
                elif self.status(status) != 202:
                    _LOGGER.warning("FanControl push hint unavailable (HTTP %s)", status)
                if watch_id in self.pending:
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            if attempt:
                attempt["status"] = "cancelled_delivery_unknown"
                # reset_delivery may be awaited while the settings flow owns
                # runtime.lock; that caller saves after cancellation completes.
            raise
        except Exception:
            if attempt:
                attempt["status"] = "delivery_unknown"
                await self.persist()
            _LOGGER.warning("FanControl push relay unavailable; passive polling remains enabled")
        finally:
            self.tasks.pop(watch_id, None)

    async def close(self):
        self.closed = True
        await self.reset_delivery()
        await self.persist()

    async def reset_delivery(self):
        """Cancel in-flight delivery before changing installation credentials."""
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.registered.clear()
