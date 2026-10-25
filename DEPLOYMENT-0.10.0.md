# HA 0.10.0 and command outcome relay - October 25, 2026

Deployed source `f1ec7314bdf2b21333671b81e386da0a6b473957` after explicit
build/release authorization. The HomeAssistant plugin staged and compiled all
20 integration files, preserved private rollback/pairing/operation copies, verified
installed bytes and validated configuration before restarting HA. On HA 2026.10.4,
Fan Control returned to `loaded` and runtime diagnostics reported version 0.10.0.
Post-startup source hashes and both existing pairing identities/owners/secrets
matched the backup. The Fan Control system-log query returned no entries.
No physical command was issued as a deployment test.

Private rollback: `/homeassistant/fancontrol-deploy-backups/20261025-185532/`.
Staged source: `/homeassistant/fancontrol-deploy-staging/20261025-185532/`.
The private deployment record contains all source hashes. To roll source back,
restore the backup's `fan_control` directory and restart HA; preserve current
pairing/operation storage unless independently damaged. Never commit private copies.

Relay deployment succeeded with byte-for-byte readback and public discovery:
https://github.com/agawlbb/coffey-table-recipes/actions/runs/40242258723

Only the public PHP entry was replaced. Private relay configuration, APNs provider
key and database were untouched. Private source backup:
`/home/negaau5/fancontrol-private/index-before-outcomes-20261025T225325Z.php`.
Relay SHA256: `719740ac39368307908a14346731931161ed7f5c4e8d077e8702b6b74bded501`.
Restore that source to the public entry for rollback; retain private data.

257 regression tests passed locally, including four deployment/rollback tests.
Real-HA runtime, regression, compilation, PHP and packaging CI passed at
`77d5c628da561cf007c3299bfffa8793fe23a723`, whose integration and relay runtime
bytes match the deployed pin:
https://github.com/agawlbb/FanControlHA/actions/runs/40242073803

Integration package SHA256:
`61635a58805f9bb197d9b879b8849825350ba3cfee567d95f14b63d5466acdba`.

Apple 0.9.0 (18) uses command capability 2 and the dedicated watch outcome route.
All controls turn teal on authenticated HA ownership; physical completion remains
separate. Generic final failure alerts, fresh vehicle observations and bounded
timing diagnostics accompany the durable command pipeline. Ordinary controls
have no separate status messages. Physical watch/car behavior, cellular delivery,
background scheduling and actual notification receipt require paired-device checks.
