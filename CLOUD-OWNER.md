# Development environments and workflow

This file is retained for existing links. [AGENTS.md](AGENTS.md) governs repository workflow.

## GitHub, branches and workspaces

- GitHub `agawlbb/FanControlHA`, default branch `main`, is the canonical copy of accepted code and history. Laptop, VPS and cloud checkouts are working copies. The default branch is for integration of completed work, never a working branch for a task.
- Prefer the VPS for most development. Development may also take place on the laptop or another authorized environment, including emulator, webview and device testing. Independent tasks use separate, clearly named temporary branches and separate working directories (Git worktrees or clones). Do not share a writable checkout between concurrent tasks. There is no permanent designated chat owner or owner-chat approval requirement.
- Before editing, read applicable instructions and inspect current branches, worktrees, local edits, live GitHub heads and active writers/builds. Preserve unrelated and unpushed work; a clean tree alone does not prove inactivity. Coordinate overlapping changes and shared resources with the tasks involved.
- Start each task branch from the latest canonical default and record its purpose and base commit. To move the same task between machines, commit and push that branch, then fetch and continue it on the other machine after coordinating the handoff. Never overwrite another task's work or use recovery archives as active workspaces.
- Before merging, fetch and integrate the latest canonical default into the task branch, resolve conflicts without discarding others' changes, review the diff and rerun relevant checks against the integrated result. Serialize merges into the default branch and recheck its live head before publishing. Respect existing protections; do not bypass them or rewrite published history.
- Commit and push completed work to the existing GitHub origin, merge accepted work into `main`, and verify the remote commit and a clean task working tree before reporting completion. Report blockers or unsynchronized work explicitly. Inspect push triggers before every push; GitHub publication does not itself authorize builds, releases, deployment or installation.
- Temporary task branches must be removed both locally and remotely once their purpose is fulfilled and their changes are merged into the canonical default branch, or their work is explicitly abandoned by the user or the person responsible for that task. Verify the merge or abandonment decision first; preserve commit history, required recovery evidence and recoverable unique work before deletion. Stale or paused work is not abandoned. Never discard unmerged work without that decision, delete another task's branches, or purge recovery archives as routine cleanup.

## Builds

Apple builds run on GitHub-hosted macOS with Xcode. The Linux cloud workspace
can edit source but cannot compile iOS/watchOS targets locally.
The Apple repository's `watchos.yml` supports manual dispatch on main.
Select `validation_only: true` for Swift tests and unsigned phone/watch simulator
builds. Main pushes and pull requests also trigger its build workflow.

The HA repository's `checks.yml` supports push, pull request and manual dispatch,
covering regression tests, Python/PHP checks, integration packaging and an HA
runtime job. Verify that the workflow is enabled before relying on it.

The Apple repository's `testflight.yml` is manual, defaults upload to false, and needs existing signing credentials.
A request to build alone must not accidentally publish. Verify credential readiness and coordinate any shared signing runner
before a signed build. Never commit signing material or runtime credentials.

App modifications and builds may proceed in the cloud for requested work.
TestFlight publication, App Store release, HA/relay deployment, device installation
and physical commands require an explicit request covering the action.
Record the exact tested revision, results and remaining physical-device checks.

This workflow documentation does not authorize a build or release.
