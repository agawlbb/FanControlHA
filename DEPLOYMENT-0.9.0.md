# HA 0.9.0 — October 24, 2026

Deployed source `018eeffabbc7793d4303bfd66357246ad1f3c08b` using the
HomeAssistant plugin File editor API after explicit deployment/publication approval.
All 20 source files were staged, Python-compiled and read back before restart.
HA configuration validation passed. After the restart, the Fan Control config
entry returned to `loaded`; runtime diagnostics report integration version 0.9.0
on Home Assistant 2026.10.3. The Fan Control system-log query returned no entries.
All source SHA256 values matched again after startup. Both existing watch
identities, owners, watch secrets and companion secrets match the private backup.
No physical command was used as a deployment test.

Private rollback source and pairing copy:
`/homeassistant/fancontrol-deploy-backups/20261024-161612/`.
Staged source:
`/homeassistant/fancontrol-deploy-staging/20261024-161612/`.
The private `deployment.json` contains the source pin and all per-file hashes.
To roll source back, restore that directory's `fan_control` source and restart HA;
retain current pairing/operation storage unless independently damaged. Do not
restore pairing storage over a running instance or expose private backups in Git.

Package SHA256: `541d286408e652eeec7a54217fbd49411ed0a6cec3d91f69f6b0d7f8cc44779d`.
242 regression tests, Python compilation, packaging/PHP checks and isolated real-HA
durable storage/reload checks passed:
https://github.com/agawlbb/FanControlHA/actions/runs/40128342770

Apple 0.8.0/build 17 uses the new command API. HA owns accepted operations through
bounded delivery, confirmation and restart recovery; the watch outbox preserves
handoff when leaving its screen. Existing client APIs remain available. Cellular,
immediate wrist-down/navigation, sleeping-vehicle response and physical outcomes
still require paired-device checks. HA observations do not independently prove
physical vehicle outcomes.

Apple 0.8.0/build 17 is now VALID and IN_BETA_TESTING for the existing Internal
group; build ID `235e769a-793d-4406-a96c-ca0fd44c7b66`. Signed release and
processing verification passed: https://github.com/agawlbb/coffey-table-recipes/actions/runs/40128954393
