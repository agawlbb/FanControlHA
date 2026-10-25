# Unreleased HA/relay corrections - October 25, 2026

Source version HA 0.10.0; command_operations_version 2. Live HA remains 0.9.0,
and the installed/TestFlight app remains 0.8.0 (17). No deployment, live service
command, relay publication or release is authorized by this implementation.

The shared operation pipeline now returns authoritative request-arrival,
registration, acceptance, wake/online, dispatch/return and terminal timestamps,
plus bounded event history and fresh observation metadata. Duplicate submissions
keep original times; client-provided timestamps cannot replace server evidence.
HA last_reported/last_updated freshness (120 seconds) prevents old online/climate
state suppressing wake or confirming success. Role comparisons ignore cosmetic
names/colors while still rejecting changed targets or revoked authorization.

The watch turns every requested control teal on authenticated HA ownership;
physical execution and matching-state confirmation continue independently. The
watch entry coordinator performs bounded user-visible wake/readiness observation,
and cached climate ON cannot invert the initial cold-entry ON intent.

User-command outcomes now use a dedicated watch-only registration/relay namespace,
separate from the phone's passive snapshot destination. The new relay `outcome`
action accepts only operation IDs and fixed terminal statuses, never arbitrary
messages or physical commands. Failed/unconfirmed operations generate generic
visible alerts; complete/accepted operations send silent hints. The watch verifies
operation status through its authenticated API before changing feedback. Entry-only
wake operations do not generate extra command alerts.

HA persists notification evidence, retries bounded transport failures, rechecks
newer intents and revocation after awaits, restores unsent outcomes after reload
and backfills results after watch registration. The relay deduplicates APNs-
accepted update IDs and uses a per-operation collapse ID and bounded expiration.
APNs acceptance is not watch receipt. Cross-channel exactly-once display cannot
be guaranteed during disconnection; local missing-handoff fallback uses honest
not-confirmed wording and 30 seconds of notification grace after the 195-second
operation deadline. Normal accepted wake/retry delays remain silent.

A later explicitly approved rollout must update **HA, relay and watch together**.
Preserve HA pairing/operation stores and relay private config/key/SQLite data;
never put those files in Git. Validate the new relay before depending on HA outcome
alerts. No new Apple key/topic is introduced: command alerts use the existing watch
topic, with a registration separate from phone snapshot delivery. Watch notification
permission, registration, relay connectivity and OS delivery remain prerequisites.

253 local synthetic regression tests and Python compilation passed, including
closed-watch climate continuation, shared wake, stale reports, server-owned timing,
notification isolation/deduplication/supersession and strict relay outcome payloads.
Physical watch/cellular/car, notification permission, offline delivery and OS
background behavior remain device checks; no live target was commanded for tests.
