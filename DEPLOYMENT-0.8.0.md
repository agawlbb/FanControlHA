# HA 0.8.0 — October 24, 2026

Deployed source `82183d60af4cc96b4f7e502df027b915c55695a6` through the
HomeAssistant plugin's File editor API with explicit deployment/restart approval.
All 20 source files were staged, Python-compiled and read back before HA restart.
The Fan Control entry returned to `loaded` on Home Assistant 2026.10.3; its
system-log query contained no Fan Control errors. Both watch identities and pairing
secrets match the private pre-deployment backup. No physical commands were tested.

Private rollback copy: `/homeassistant/fancontrol-deploy-backups/20261024-091957/`.
Restore its integration source if needed and restart HA; retain the current pairing
storage unless independently damaged. The backup includes a private pairing copy.
Staged files remain under `/homeassistant/fancontrol-deploy-staging/20261024-091957/`.

The existing morning Exercise automation was updated through HA's configuration
API and read back after restart: only the person's arrival triggers at either
exercise location, with the original 06:00–09:00 window and selection action.
The previous automation configuration is retained locally in ignored artifacts.

Validation: 223 pytest scenarios, Python compilation and integration packaging
passed. Final-source CI additionally passed real HA 2026.10.2 startup/runtime and
PHP checks: https://github.com/agawlbb/FanControlHA/actions/runs/40097430298.
The pre-existing disabled CI state was restored after the manual validation run.

HA ZIP SHA256: `1dc84b159c9de77f8f2f1cc3ddcef132dd894d7891c050c59679fc7f3cf9b21d`.

Apple 0.7.0 (16) uses the new operations; existing clients retain their APIs and
deadlines. Vehicle readiness, first-tap delivery, cancellation/superseding intents,
and screen-off Stop continuation still require paired-device and vehicle checks.
HA observations do not independently prove physical vehicle outcomes.
