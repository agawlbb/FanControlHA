"""Passive, per-watch APNs hints. Never invokes an HA service or vehicle wake."""
import asyncio
import logging
import re
import time

from .panels import configuration, panel_state
from .push_trace import new_attempt

from .apns import APNs, configured
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


class PushCoordinator:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.snapshots = {}
        self.pending = {}
        self.tasks = {}
        self.apns = APNs(hass)
        self.closed = False

    @property
    def settings(self):
        return self.runtime.get("push_settings", {})

    @property
    def enabled(self):
        return configured(self.settings)

    def updated(self):
        """Called after persisted configuration changes, including Stop selection."""
        watches = self.runtime["manager"].watches
        for watch_id in set(self.snapshots) - set(watches):
            self.snapshots.pop(watch_id, None)
            self.pending.pop(watch_id, None)
        for watch_id, watch in watches.items():
            if watch.get("platform") != "watch_os":
                continue
            try:
                config = configuration(watch)
                snapshot = (config, observation(self.hass, watch), dict(watch.get("push_registration", {})))
            except (ValueError, KeyError, TypeError):
                continue
            old = self.snapshots.get(watch_id)
            self.snapshots[watch_id] = snapshot
            if old and old[0] != config:
                reason = "selection" if old[0]["entity"] != config["entity"] else "configuration"
                self.queue(watch_id, reason)
            elif watch.get("push_registration"):
                # Startup and a new device token each need one passive refresh.
                if not old or old[2] != watch["push_registration"]:
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
                config, old, target = self.snapshots[watch_id]
                self.snapshots[watch_id] = (config, value, target)
                if old != value:
                    self.queue(watch_id, "active_panel_state")
            except (ValueError, KeyError, TypeError):
                continue

    def queue(self, watch_id, reason):
        if self.closed or not self.enabled:
            return
        # Prefer selection/configuration over ordinary state bursts.
        if reason != "active_panel_state" or watch_id not in self.pending:
            self.pending[watch_id] = reason
        if watch_id not in self.tasks:
            self.tasks[watch_id] = self.hass.async_create_background_task(self.flush(watch_id), "FanControl push hint")

    async def send(self, target, payload, *, update_id, push_type, collapse_id, current):
        settings = dict(self.settings)
        result = await self.apns.send(target, payload, settings=settings, update_id=update_id,
                                      push_type=push_type, collapse_id=collapse_id,
                                      current=lambda: not self.closed and not self.runtime.get("unloading")
                                      and self.settings == settings and configured(settings) and current())
        if self.status(result) == 410:
            # Expire only this exact registration, never a replacement received in flight.
            async with self.runtime["lock"]:
                for watch in self.runtime["manager"].watches.values():
                    for field in ("push_registration", "command_push_registration"):
                        value = watch.get(field)
                        if value and {**value, "destination": value.get("destination", "watch")} == {**target, "destination": target.get("destination", "watch")}:
                            watch.pop(field)
                from . import save
                await save(self.runtime)
        return result

    async def notify_phone(self, watch_id, title, message):
        """A visible alert, independent of app background execution and snapshots."""
        import uuid
        from homeassistant.exceptions import HomeAssistantError
        from .notifications import notification_text
        notification_text(title, 200)
        notification_text(message, 1500)
        watch = self.runtime["manager"].watches.get(watch_id)
        settings = dict(self.settings)
        if self.closed or self.runtime.get("unloading") or not configured(settings):
            raise HomeAssistantError("Fan Control push notifications are disabled")
        if (watch is None or watch.get("platform") != "watch_os" or not watch.get("companion")
                or watch.get("push_registration", {}).get("destination") != "phone"):
            raise HomeAssistantError("Link the iPhone app and register it for notifications first")
        target = dict(watch["push_registration"])
        companion = dict(watch["companion"])
        owner_id = watch["owner_id"]

        def current():
            return (not self.closed and not self.runtime.get("unloading") and self.settings == settings
                    and self.runtime["manager"].watches.get(watch_id) is watch
                    and watch.get("platform") == "watch_os" and watch.get("owner_id") == owner_id
                    and watch.get("companion") == companion and watch.get("push_registration") == target
                    and owner is not None and owner.is_active and owner.is_admin)

        owner = await self.hass.auth.async_get_user(owner_id)
        if not current() or owner is None or not owner.is_active or not owner.is_admin:
            raise HomeAssistantError("Notification recipient is no longer available")
        notification_id = str(uuid.uuid4())
        try:
            # Token signing may await an executor; the transport rechecks scope afterward.
            owner = await self.hass.auth.async_get_user(owner_id)
            if not current() or owner is None or not owner.is_active or not owner.is_admin:
                raise HomeAssistantError("Notification recipient is no longer available")
            payload = {"aps": {"alert": {"title": title, "body": message}, "sound": "default",
                               "thread-id": "fancontrol-automations", "content-available": 1},
                       "fancontrol": {"notification": True, "update_id": notification_id, "watch_id": watch_id, "sent_at_ms": int(time.time() * 1000)}}
            result = await self.send(target, payload, update_id=notification_id, push_type="alert",
                                     collapse_id="fc-" + notification_id, current=current)
            if self.status(result) != 202:
                raise HomeAssistantError("Notification was not accepted for delivery")
        except HomeAssistantError:
            raise
        except Exception:
            # Neither message contents nor signing secrets belong in HA errors/logs.
            raise HomeAssistantError("Notification delivery is unknown; check connectivity before sending again") from None

    async def command_outcome(self, op):
        """Dedicated watch destination; phone snapshot registration cannot replace it."""
        import uuid
        watch = self.runtime["manager"].watches.get(op["watch_id"])
        if not self.enabled or watch is None or not watch.get("command_push_registration"):
            return {"http_status": 404, "error": "not_registered"}
        target = dict(watch["command_push_registration"])
        target["destination"] = "watch"
        owner_id = watch.get("owner_id")
        settings = dict(self.settings)
        def current():
            latest = self.runtime.get("vehicle_latest", {}).get((op["watch_id"], op["panel"]["id"]))
            registration = dict(watch.get("command_push_registration", {}))
            registration["destination"] = "watch"
            return (self.runtime["manager"].watches.get(op["watch_id"]) is watch
                    and registration == target and watch.get("secret") == op["secret"]
                    and watch.get("owner_id") == op["owner"] and self.settings == settings
                    and owner is not None and owner.is_active and owner.is_admin and op["status"] != "cancelled"
                    and not (latest is not None and latest is not op and latest["issued"] >= op["issued"]))
        owner = await self.hass.auth.async_get_user(owner_id)
        if not current() or owner is None or not owner.is_active or not owner.is_admin:
            return {"http_status": 409, "error": "superseded"}
        failed = op["status"] in ("failed", "unconfirmed")
        aps = {"content-available": 1}
        if failed:
            aps.update(alert={"title": "Fan Control request failed" if op["status"] == "failed" else "Fan Control not confirmed",
                              "body": "Home Assistant could not complete your request." if op["status"] == "failed"
                              else "Home Assistant could not confirm the requested outcome."},
                       sound="default", **{"thread-id": "fancontrol-" + op["id"]})
        return await self.send(target, {"aps": aps, "fancontrol": {"operation_id": op["id"]}},
            update_id=str(uuid.uuid5(uuid.NAMESPACE_URL, op["id"] + ":" + op["status"])),
            push_type="alert" if failed else "background", collapse_id="fc-" + op["id"], current=current)

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
            while watch_id in self.pending and self.enabled and not self.closed:
                reason = self.pending.pop(watch_id)
                watch = self.runtime["manager"].watches.get(watch_id)
                if watch is None:
                    continue
                owner = await self.hass.auth.async_get_user(watch["owner_id"])
                if owner is None or not owner.is_active or not owner.is_admin:
                    continue
                target = watch.get("push_registration")
                attempt = new_attempt(watch, reason)
                _LOGGER.info("FanControl push attempt %s watch=%s revision=%s", attempt["update_id"], watch_id, attempt["revision"])
                await self.persist() # Save the attempt before any APNs network work.
                if not target or watch.get("platform") != "watch_os":
                    attempt["status"] = "not_registered"
                    await self.persist()
                    attempt = None
                    continue
                target = dict(target)
                # Recheck revocation/token replacement after every network await.
                if self.runtime["manager"].watches.get(watch_id) is not watch or watch.get("push_registration") != target:
                    attempt["status"] = "superseded"
                    await self.persist()
                    continue
                owner_id = watch["owner_id"]
                credential = dict(watch.get("companion", {})) if target.get("destination") == "phone" else watch.get("secret")
                def current():
                    latest = self.runtime["manager"].watches.get(watch_id)
                    same_credential = (watch.get("companion", {}) == credential if target.get("destination") == "phone"
                                       else watch.get("secret") == credential)
                    return (latest is watch and watch.get("push_registration") == target
                            and watch.get("owner_id") == owner_id and same_credential
                            and owner is not None and owner.is_active and owner.is_admin)
                payload = {"aps": {"content-available": 1},
                           "fancontrol": {"refresh": True, "update_id": attempt["update_id"], "watch_id": watch_id}}
                status = await self.send(target, payload, update_id=attempt["update_id"], push_type="background",
                                         collapse_id="fancontrol-refresh", current=current)
                attempt.update(status="accepted_by_apns" if self.status(status) == 202 else "apns_failed", apns_result=status,
                               responded_at_ms=int(time.time() * 1000))
                _LOGGER.info("FanControl push result %s status=%s receipt=unknown", attempt["update_id"], attempt["status"])
                await self.persist()
                attempt = None
                if self.status(status) != 202:
                    _LOGGER.warning("FanControl push hint unavailable (HTTP %s)", self.status(status))
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
            _LOGGER.warning("FanControl APNs delivery unavailable; passive polling remains enabled")
        finally:
            self.tasks.pop(watch_id, None)

    async def close(self):
        self.closed = True
        await self.reset_delivery()
        await self.persist()

    async def reset_delivery(self):
        """Cancel passive delivery before changing Apple credentials."""
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.snapshots.clear()
        await self.apns.reset()
