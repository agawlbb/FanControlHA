"""Bounded per-watch push evidence; never contains tokens or sends controls."""
import time
import uuid
import re

PHASES = {"received", "skipped_active", "connection_unavailable", "refresh_started",
          "refresh_superseded", "config_fetched", "selection_saved", "refresh_failed", "opened", "registration_failed"}


def new_attempt(watch, reason):
    from .panels import configuration
    config = configuration(watch)
    attempt = {"update_id": str(uuid.uuid4()), "attempted_at_ms": int(time.time() * 1000),
               "panel": config["entity"], "revision": config["revision"], "reason": reason,
               "status": "attempted", "receipt": "unknown", "events": []}
    history = watch.setdefault("push_history", [])
    history.append(attempt)
    del history[:-120]
    return attempt


def identifier(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Invalid diagnostic ID")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError("Invalid diagnostic ID") from None


def record_events(watch, watch_id, events):
    if not isinstance(events, list) or not 1 <= len(events) <= 12:
        raise ValueError("Invalid receipt batch")
    clean = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Invalid receipt")
        phase, at, panel, revision = (event.get(k) for k in ("phase", "at_ms", "panel", "revision"))
        if (not isinstance(phase, str) or phase not in PHASES or type(at) is not int or not 0 <= at <= 2**53
                or not isinstance(panel, str) or len(panel) > 160
                or (panel and not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", panel))
                or (revision is not None and (type(revision) is not int or not 0 <= revision <= 2**53))
                or event.get("watch_id") != watch_id):
            raise ValueError("Invalid receipt")
        clean.append({"id": identifier(event.get("id")), "update_id": identifier(event["update_id"]) if event.get("update_id") is not None else None,
                      "phase": phase, "at_ms": at, "panel": panel, "revision": revision})
    attempts = {row["update_id"]: row for row in watch.get("push_history", [])}
    recorded, discarded = [], []
    for event in clean:
        if event["update_id"] is None:
            history = watch.setdefault("push_client_events", [])
            if not any(old["id"] == event["id"] for old in history):
                event["reported_at_ms"] = int(time.time() * 1000)
                history.append(event)
                del history[:-120]
            recorded.append(event["id"])
            continue
        attempt = attempts.get(event["update_id"])
        if attempt is None:
            # Another watch's ID or an expired history entry supplies no evidence.
            discarded.append(event["id"])
            continue
        if not any(old["id"] == event["id"] for old in attempt["events"]):
            event["reported_at_ms"] = int(time.time() * 1000)
            attempt["events"].append(event)
            del attempt["events"][:-60]
            if event["phase"] == "received":
                attempt["receipt"] = "received"
                attempt.setdefault("received_at_ms", event["at_ms"])
            if event["phase"] == "selection_saved":
                attempt["selection_saved"] = {"panel": event["panel"], "revision": event["revision"],
                    "at_ms": event["at_ms"], "matches_attempt": event["panel"] == attempt["panel"] and event["revision"] == attempt["revision"]}
        recorded.append(event["id"])
    return {"recorded": recorded, "discarded": discarded}
