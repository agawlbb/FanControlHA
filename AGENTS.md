# Home Assistant integration development

## Repository ownership and offline work

The canonical repositories are `C:/Users/mailb/StudioProjects/FanControl/apple-watch`
(agawlbb/FanControlWatch) and `C:/Users/mailb/StudioProjects/FanControl/ha-integration`
(agawlbb/FanControlHA). Work only in the repository assigned to the task.
The connected laptop owner is the chat "FanControl owner" (01a15b0b-d82d-7dd2-b5ce-4e3f9bf236b8);
coordinate handoff before another chat writes. Verify current heads and local
changes before editing; preserve unexpected work. Labelled recovery copies remain
archives and must not become active checkouts.

When explicitly asked to work offline, use a named `codex/` branch in the same
repository. On laptop return, the owner reviews, merges, validates and synchronizes
that branch. Delete it only after its work is preserved. Complete work with commits
and pushes; verify clean local state and matching GitHub heads before reporting done.
Inspect push workflows first. Do not trigger releases, deployment, device installation
or distributable builds without authorization. Never change branch protections
without specific approval. Back up before deletion/history rewrite, exclude ignored
credentials/runtime/signing material, preserve release tags, and use exact-SHA
force-with-lease for authorized rewrites. Never purge recovery archives.

## Required design for future commands

Every new or changed Apple watch HA action must use the shared durable operation
pipeline, including generic devices and new panel types. First-tap completion is
the design goal for valid, authorized targets; ordinary wake and transient delivery
delays must recover without another watch tap. Follow
[COMMAND-OPERATIONS.md](COMMAND-OPERATIONS.md) and the watch repository's
`COMMAND-DELIVERY.md`; do not add a synchronous watch-dependent command path.

- Validate the captured watch/owner/credential, target roles and absolute intent.
  Persist the operation before acknowledging acceptance or starting physical work.
  The same ID and payload return the same operation; reject changed payloads and
  stale/conflicting intents. Selection and unrelated configuration edits must not
  redirect or block a still-valid approved target.
- After durable acceptance, HA owns readiness, bounded wake, delivery, retry and
  confirmation independently of watch connectivity, navigation and polling. Save
  enough state to recover after HA restart within the original deadline. Preserve
  explicit cancellation (including cancellation arriving before submission),
  supersession and credential/role revocation across recovery.
- Classify every command as a retryable desired state or an at-most-once action.
  Persist the dispatch boundary before calling one-shot services. Never replay an
  ambiguous cover, horn, script, scene or equivalent action after a lost response
  or restart. Preserve shared wake ownership and fan-speed debounce across both
  cancellation and restart. Service/provider waits must not hold the global lock
  and prevent other commands from being accepted.
- Keep durable queue acceptance, dispatch acknowledgment and observed completion
  distinct in API results. Confirm desired states only from available matching
  observations; scripts/scenes and similar actions acknowledge dispatch unless
  separately configured completion evidence exists. Vehicle state cannot confirm
  independent music playback. Report ambiguous outcomes as unconfirmed.
- Use the shared 180-second delivery deadline plus 15-second read-only grace;
  retries and duplicate requests must not extend it. Expected wake, retry, rate-
  limit and temporary provider delays stay silent. Reconcile final evidence before
  failure notifications; avoid duplicate/stale alerts and never promote timeout,
  cancellation or unknown state into success. Do not add passive/background wakes.

For each new action, add meaningful regression coverage for durable acceptance,
lost responses and duplicate IDs, retryable delays, observed confirmation,
unavailable/mismatched evidence, expiry, explicit cancellation, supersession,
target/revocation changes, restart recovery and one-shot ambiguity. Exercise its
watch surfaces too, including immediate navigation/wrist-down and stale callbacks.
Document required API capabilities, compatible app/integration versions and
remaining physical checks. Do not claim guaranteed exactly-once physical completion
without target-side support. Deployment and physical validation still require their
existing separate authorization.

## Integration invariants

Keep approval, credentials, entities, panel roles and operations scoped to the
same watch/owner/configuration. Companion keys authorize observations/preferences,
never physical commands. Unlink phone revokes only that companion; watch revocation
revokes both. Revision-check preference writes and keep selection independent of
swipe order. Configuration, snapshots, push and diagnostics never wake vehicles.
Explicit settled visible watch entry and a user-tapped vehicle command may request or join one bounded wake attempt. Passive updates and command retries never initiate additional wakes. Apple command operations expire after 180 seconds plus a 15-second read-only confirmation grace period; expected delays do not trigger one-minute alerts.

All new Apple commands use durable HA-owned operations: persist before acceptance,
continue independently of watch navigation, and restore after restart within the
original deadline. Persist one-shot dispatch boundaries before service calls;
never replay ambiguous horn/cover/script/scene actions. The watch owns durable
handoff until HA accepts; no preflight reads are required for command submission.
Fan speed requests debounce for one second on HA, so wrist-down cannot lose drafts.
Preserve at-most-once one-shot dispatch and bounded Stop continuation. Completed
Stop selects a uniquely matching Driving panel once, without physical commands;
duplicate polls cannot overwrite later manual selection. Local music Stop confirms
available unlocked/climate-on evidence and treats temperature as best effort.
Do not change legacy client semantics incidentally. An HA observation is not
independent physical vehicle proof; APNs acceptance is not device receipt.

Keep private HA storage, relay config/database/APNs keys, diagnostics and music
out of Git and backups of tracked source. Use synthetic test credentials. Preserve
all integration and relay regression tests. Run pytest and Python compilation for
code changes; PHP checks require a supported runtime. Do not deploy to HA/hosting
or send physical commands as validation without explicit authorization.
