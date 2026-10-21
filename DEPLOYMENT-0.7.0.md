# HA 0.7.0 deployment — October 21, 2026

Deployed the tested integration source at
`c40f5b9eae7f30bb32862c8fa9f66ef3431f2d23` to the existing HA installation.
All 19 integration source files were staged and Python-compiled before replacement.
The former integration files and pairing storage were backed up privately under
`/homeassistant/fancontrol-deploy-backups/20261021-144642/`.
HA was restarted and the existing Fan Control entry returned to `loaded`.
The installed manifest is 0.7.0. Both stored watch identities and their secrets
match the pre-deployment backup; pairing storage was not edited.

Deployed the same commit's public relay entry through the existing cPanel
credentials. Private configuration, provider key and SQLite registrations were
preserved. The previous public entry was copied into the private relay directory.
The hosted entry was read back and verified byte-for-byte:
SHA256 `608dbc521f81c75e204f76ec41be83b1eefc7df4956158fecd9b2a68412772b2`.

Deployment proof:
https://github.com/agawlbb/coffey-table-recipes/actions/runs/39656454779

Live signed requests return 200; replay returns 409 and an invalid signature
returns 401. Those protocol checks send no push or vehicle command.

With explicit user approval, the existing production APNs key was updated in
Apple Developer to retain `dev.fancontrol.watch.watchkitapp` and additionally
authorize `dev.fancontrol.watch`. Apple confirmed the update and lists two topics.
No new private key was generated or distributed.

During the interval before relay deployment, HA recorded `unknown_fields` relay
failures against the old entry. After deployment the Fan Control entry was reloaded
to reset delivery state. Its startup hint was recorded as `accepted_by_apns`,
HTTP 202, at 2026-10-21 18:54:39 UTC. Receipt remains `unknown`: this is evidence
of Apple accepting the request, not phone/watch delivery or physical behavior.

The companion still needs installation/linking on paired hardware before phone
receipt, WatchConnectivity transfer and offline playback can be verified.
Wear OS code and validation remain paused and untouched.
