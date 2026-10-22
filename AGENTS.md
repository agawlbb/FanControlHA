# Home Assistant integration development

## Repository ownership and offline work

The canonical repositories are `C:/Users/mailb/StudioProjects/FanControl/apple-watch`
(agawlbb/FanControlWatch) and `C:/Users/mailb/StudioProjects/FanControl/ha-integration`
(agawlbb/FanControlHA). Work only in the repository assigned to the task.
The connected laptop owner is the chat "Explore push wakeups for Apple Watch";
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

## Integration invariants

Keep approval, credentials, entities, panel roles and operations scoped to the
same watch/owner/configuration. Companion keys authorize observations/preferences,
never physical commands. Unlink phone revokes only that companion; watch revocation
revokes both. Revision-check preference writes and keep selection independent of
swipe order. Configuration, snapshots, push and diagnostics never wake vehicles.
Only explicit settled visible watch entry may request wake.

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
