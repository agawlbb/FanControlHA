# Direct APNs deployment — November 6, 2026

HA integration **0.13.1**, source
`09b54d59b6acc3eb7649ee52061cee95ce6b20d2`, is installed and loaded.
All 23 installed source files matched their pinned GitHub hashes. HA configuration
validation passed before the authorized restarts. Pairing owners, secrets,
companion grants, approved devices, panel roles and selections matched the
pre-migration fingerprint afterward. Existing phone/watch apps remain compatible.

HA now sends directly to Apple's production/sandbox APNs endpoints. Its native
push settings contain only enabled, team_id and key_id; the old relay installation
ID/shared secret were removed through the native options flow. The P-256 key was
transferred encrypted to HA and installed at `/config/fancontrol-apns.p8`, mode
0600. It was not printed or committed. Temporary private copies and migration
libraries were removed. A dedicated HTTP/2 connection pool is reused and closed
on key changes/unload. HTTPX request logging redacts APNs device tokens.

Apple accepted direct production checks for both the Fan Control iPhone and watch
topics. Checks were background-only: no visible alert or physical command was sent.
After final startup, HA's own persisted push evidence reported accepted_by_apns;
the watch subsequently reported received_by_watch. APNs acceptance and passive
receipt do not prove visible alert rendering, user attention or physical outcomes.

The former `https://coffeybean.com/FanControl/` endpoint now returns HTTP 410 with
status retired. It performs no APNs requests or SQLite work. Private relay config,
key, database and source rollback evidence remain protected on the old host;
no other website was changed. There is no relay fallback in the HA integration.

Private HA source/state rollback:
`/homeassistant/fancontrol-deploy-backups/direct-apns-20261106-135521/`.
Its deployment.json records source hashes, verification and the two authorized
Core restarts. Preserve current pairing/operation state when restoring source;
do not overwrite newer state with the saved snapshot as routine rollback.
The original relay PHP source is protected at
`/home/negaau5/fancontrol-private/index-before-direct-apns-20261106T140045Z.php`.
Restoring a historical relay-based version also requires deliberately restoring
that endpoint and its previous native push settings. The retained key must not
be revoked while HA uses it.

Validation: all 329 integration/historical relay regressions passed before the
logging-only correction; all 25 focused APNs tests passed afterward. Python
compilation and diff checks passed. Coverage includes real ES256 signature
verification, fixed hosts/topics, HTTP/2 routing, provider-token reuse/rotation,
connection lifecycle, payload limits, deadline/cancellation, invalid-token expiry,
stale recipient/owner/settings checks and persisted ambiguous-dispatch suppression.
On-device visible alert/permission/Focus behavior remains a user/device check.
