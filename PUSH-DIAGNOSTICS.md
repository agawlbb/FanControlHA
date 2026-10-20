# Push reconciliation — HA 0.6.3 / watch 0.5.2 (14)

Validation: [CI run 39514438444](https://github.com/agawlbb/FanControlHA/actions/runs/39514438444) passed on source `4321fd2e0b7bebc9255f2bc7a828d0cd7a5ca128`: 205 integration/relay tests, Python compilation, PHP syntax checks, integration ZIP packaging, patched package/version and image checks, and real HA 2026.10.2 startup/options/actions/unload. Production HA/relay were not deployed and physical push delivery remains to be checked. The workflow's previous disabled state was restored after this validation run.

HA persists a unique update ID, watch, intended panel, configuration revision, reason and attempt timestamp before relay network work. Bounded results record registration failures, relay/APNs acceptance/rejection, transport ambiguity and cancellation. APNs acceptance is never labeled watch delivery. New hint payloads carry only a refresh flag and correlation/watch IDs; no selected panel, observation or credential is trusted from a push.

The watch records receipt before starting refresh, then separate refresh-start, skip, fetch, save and failure events. Original event times survive delayed uploads; HA adds report times and deduplicates by event ID. Only the authenticated watch can report against its own attempts. Unknown/expired/other-watch update IDs are discarded without creating receipt evidence. First local selection on opening is recorded before foreground refresh. Uncorrelated opens and registration failures remain visible separately.

In HA, open Settings → Devices & services → Fan Control → Download diagnostics. The `watches` list includes `registered_with_ha`, `push_history` and `uncorrelated_watch_events`. Each attempt has a submission `status` and independent `receipt` (`unknown` or `received`). `selection_saved.matches_attempt` distinguishes saving the attempted selection/revision from a newer or mismatched selection. A save report does not by itself establish receipt. The watch's Settings → Push diagnostics retains local history, including unreported events, and offers reporting on demand.

Retention is bounded: 120 attempts per HA watch, up to 60 events per attempt and 120 uncorrelated events; watch histories retain 120 per credential/server scope. The relay privately retains 1000 attempts per installation. APNs can collapse, defer or discard hints; an unreported receipt is unknown, not proof of nondelivery. Downloaded diagnostics contain panel identifiers; review before sharing. No watch credential, owner credential, relay secret or APNs device token is included in these diagnostic exports.

To reproduce: change HA's active panel while the app is inactive, wait two minutes, open the app, then compare the attempt's submission result, received time, saved panel/revision and opened selection. Distinguish the watch's original times from HA reporting times if offline or clock-skewed. WatchOS launch snapshots require physical inspection.

Install the updated relay endpoint and HA integration as well as the watch build to obtain end-to-end correlation. Building/pushing source does not update a deployed relay or HA installation. The new watch tolerates legacy hints without IDs (uncorrelated), and the new relay still accepts older refresh requests. Diagnostics never invoke vehicle controls or wake.

## CI-only dependency remediation

Home Assistant 2026.10.2 still declares exact uv 0.12.23 and Pillow 12.3.0 pins. The isolated CI harness uses `scripts/ha-test-overrides.txt` to resolve uv 0.13.4 and Pillow 13.0.0 explicitly. CI checks installed versions, basic image codecs and real HA startup/options/actions/unload. Upstream metadata retains its older requirements; these overrides are deliberate test-harness exceptions, not a claim of support for every HA image/uv use. Remove overrides when the harness's HA release pins patched versions. Production dependency changes are outside this remediation.

Advisories: https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62 and https://github.com/python-pillow/Pillow/security/advisories/GHSA-q45j-58j2-45rg. No exploitable integration path or affected deployed version has been established.
