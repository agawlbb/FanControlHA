# Cloud repository ownership

Cloud development is authorized for the Apple app and shared Home Assistant
integration, subject to each repository's AGENTS.md.

The primary repository owner is the cloud chat "FanControl cloud owner".
Ownership remains with the cloud when the laptop reconnects.
Use `main` for cloud work; create another branch only when the user requests it.
Inspect workflow triggers
before pushing, commit and push completed changes, and verify the remote head.
Cloud checkouts cannot establish whether the laptop has unpushed work.
The cloud owner reviews, validates and synchronizes changes on main.
Laptop-owner review or merging is not required. On laptop return, preserve
unexpected local work when synchronizing its checkout. Delete a branch only after preserving
its work. The canonical working copies are `/workspace/FanControlWatch` and
`/workspace/FanControlHA` in the cloud workspace. Laptop checkouts are secondary.
Preserve existing recovery archives.

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

This setup record makes no source changes and initiates no build or release.
