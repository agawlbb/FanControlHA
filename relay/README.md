# FanControl APNs relay

Public endpoint: `https://coffeybean.com/FanControl/` (the directory URL may redirect from the slashless form; POST directly to the trailing slash).

Supported, security-patched PHP with OpenSSL, PDO SQLite and cURL HTTP/2. No external packages.
As of October 21, 2026, the minimum patched versions are 8.2.35, 8.3.36,
8.4.27 and 8.5.12 within their respective branches, or vendor-confirmed backports.
The [PHP advisory](https://www.php.net/archive/2026.php) for CVE-2026-109618 and
CVE-2026-109619 requires patching the actual serving runtime and reloading PHP
processes. This repository does not install that patch. The hosted relay's
current patch level remains to be verified; no hosting settings were changed.
Deploy only `public/index.php` and `public/.htaccess` into `/home/negaau5/coffeybean.com/FanControl/`.
Private configuration, Apple key, provider JWT cache and SQLite database belong in
`/home/negaau5/fancontrol-private/`, outside all document roots. The private path
is derived from this host's directory layout; adjust it for other hosts.

The public GET response is a static discovery response. `available`
does not reveal or probe the private credential state. POST fails closed until configured.
No HA credentials, state, entity IDs, car names or vehicle commands are accepted.
The relay only sends a silent refresh hint; the watch must fetch authenticated HA
observations. APNs acceptance never establishes watch delivery or command confirmation.

## Configuration still required

Copy the example into the private config location, supply the watch APNs topic,
Apple team/key identifiers and .p8 signing key. Provision a separate random shared
secret for each HA installation through a secure setup flow; never ship it in the
watch binary. Keep enabled false until these values are ready. The HA integration
will authenticate relay calls; the watch sends its token to its already paired HA
integration, which forwards registration. No public unauthenticated enrollment.

## Protocol 1

POST JSON, at most 4096 bytes. Headers:

- `X-FanControl-Installation`: configured installation identifier (1–64 letters/digits/underscore/hyphen).
- `X-FanControl-Timestamp`: ten-digit Unix seconds, within 300 seconds.
- `X-FanControl-Nonce`: 16 random bytes encoded as 32 lowercase hex characters; new for each HTTP attempt.
- `X-FanControl-Signature`: lowercase hex HMAC-SHA256 using the installation secret over `timestamp + "\n" + nonce + "\n" + exact JSON request bytes`.

Bodies:

```json
{"action":"register","watch_id":"paired-watch-id","device_token":"APNs hex token","environment":"production"}
{"action":"unregister","watch_id":"paired-watch-id"}
{"action":"refresh","watch_id":"paired-watch-id","reason":"selection"}
```

Reasons: selection, configuration, active_panel_state. HA must filter events to
the watch's active panel dependencies before sending; the relay has no HA state
with which to establish relevance. Sandbox and production tokens are distinct.
Each installation can only access its own registered watches. Replayed nonces are
rejected. Limit: 60 authenticated requests/minute/installation. Duplicate refreshes
use the same APNs collapse ID, with five-minute expiry. This is APNs coalescing,
not a server-side debounce queue. No automatic retries or physical commands.

202 means APNs accepted the hint, not that the watch woke. 410 requires fresh APNs
registration. 502 may be ambiguous; never translate it into command success.
Watch push handling must refresh passively without entering the foreground wake
path. Foreground swipe wake delay is a separate watch app change, intentionally
excluded here while another chat is modifying/building that app.

## Validation

Local validation passed: PHP syntax checks and four HTTP/security/signature tests
in `test_relay.py`, using temporary data and synthetic credentials only. The
public endpoint was uploaded through cPanel and its configuration-required GET
response was verified. No live APNs request was sent.

October 20 deployment: the production APNs key and enabled configuration are
installed in `/home/negaau5/fancontrol-private/` (directory 0700; key, config and
SQLite database 0600). Signed requests succeeded; replay and bad-signature
requests were rejected. The public GET is static and does not disclose private
configuration readiness. The key is production-only and topic-restricted to the
watch app. Real APNs delivery awaits a signed watch update and registration.

Run PHP syntax checks on source and configuration template. Before enabling, test
signed registration/refresh/unregister, bad signatures, replay, stale timestamps,
oversized/invalid requests, installation isolation, token replacement and APNs
responses using sandbox credentials. Verify HTTP/2 support on the host and a real
watch receiving a hint without issuing a vehicle wake. No live push can be tested
until credentials and a watch token exist.

## iPhone companion routing

Registration optionally includes destination=phone (default watch). Existing
SQLite registrations migrate to destination=watch. The fixed phone topic is
dev.fancontrol.watch; watch registrations retain the configured watch topic.
Arbitrary client topics are rejected. Deploy this public entry with HA 0.7.0.
