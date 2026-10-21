"""Bounded pairing sessions and per-watch configuration."""
import secrets
import time

from .const import PAIR_TTL, REQUEST_WINDOW
from .protocol import aad, digest, envelope, open_request, public_key


class Manager:
    def __init__(self, watches=None, clock=time.time, nonces=None):
        self.watches = watches or {}
        self.sessions = {}
        self.nonces = nonces or {}
        self.clock = clock

    def prune(self):
        now = self.clock()
        self.sessions = {code: session for code, session in self.sessions.items() if session["expires_at"] > now}
        self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry > now}

    def create(self, encoded_key, platform="wear_os"):
        self.prune()
        if len(self.sessions) >= 32:
            raise ValueError("Too many pending watches")
        if platform not in ("wear_os", "watch_os"):
            raise ValueError("Unsupported watch platform")
        key, fingerprint = public_key(encoded_key)
        code = f"{secrets.randbelow(100000000):08d}"
        while code in self.sessions:
            code = f"{secrets.randbelow(100000000):08d}"
        token = secrets.token_hex(32)
        expires_at = int(self.clock()) + PAIR_TTL
        self.sessions[code] = {"key": key, "fingerprint": fingerprint, "token_hash": digest(token), "expires_at": expires_at, "platform": platform}
        return {"code": code, "device_token": token, "fingerprint": fingerprint, "expires_at": expires_at}

    def lookup(self, code):
        self.prune()
        session = self.sessions.get(code)
        if session is None or "envelope" in session:
            raise ValueError("Pairing expired or already approved")
        return session

    def approve(self, code, config):
        session = self.lookup(code)
        if len(self.watches) >= 32:
            raise ValueError("Too many paired watches")
        watch_id, secret = secrets.token_hex(16), secrets.token_hex(32)
        payload = {**config, "version": 1, "integration": True, "watch_id": watch_id, "token": secret}
        session["envelope"] = envelope(session["key"], code, payload)
        session["watch_id"] = watch_id
        self.watches[watch_id] = {**config, "secret": secret, "revision": 1}
        return watch_id

    def poll(self, token, acknowledge=False):
        self.prune()
        for code, session in self.sessions.items():
            if secrets.compare_digest(session["token_hash"], digest(token)):
                if acknowledge:
                    if "envelope" not in session:
                        raise ValueError("Not approved")
                    del self.sessions[code]
                    return {"status": "acknowledged"}
                return {"status": "ready", "envelope": session["envelope"]} if "envelope" in session else {"status": "pending"}
        raise ValueError("Pairing expired")

    def authenticate(self, packet):
        self.prune()
        aad(packet)
        if abs(self.clock() - packet["timestamp"]) > REQUEST_WINDOW:
            raise ValueError("Watch clock is out of sync")
        watch = self.watches.get(packet["watch_id"])
        nonce_key = packet["watch_id"] + ":" + packet["nonce"]
        if watch is None or nonce_key in self.nonces or len(self.nonces) >= 4096:
            raise ValueError("Invalid request")
        credential = packet.get("credential", "watch")
        if credential not in ("watch", "companion"):
            raise ValueError("Invalid credential")
        secret = watch.get("companion", {}).get("secret") if credential == "companion" else watch["secret"]
        if not secret:
            raise ValueError("Companion not linked")
        body = open_request(secret, packet)
        if credential == "companion" and (not isinstance(body, dict) or body.get("action") not in (
                "companion_snapshot", "companion_settings", "companion_push_register", "push_receipts")):
            raise ValueError("Companion action not permitted")
        if not isinstance(body, dict):
            raise ValueError("Invalid request")
        self.nonces[nonce_key] = self.clock() + REQUEST_WINDOW * 2
        return watch, body
