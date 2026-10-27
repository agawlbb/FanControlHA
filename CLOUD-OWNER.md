# Cloud development while the laptop is offline

Cloud development is authorized for the Apple app and shared Home Assistant
integration, subject to each repository's AGENTS.md.

Use `codex/cloud-owner-2026-10-27` for this cloud work. Inspect workflow triggers
before pushing, commit and push completed changes, and verify the remote head.
Cloud checkouts cannot establish whether the laptop has unpushed work.
On laptop return, its owner reviews, merges, validates and synchronizes the cloud
branches, preserving unexpected local work. Delete a branch only after preserving
its work. Keep the existing canonical checkout and recovery archive rules.

## Builds

Apple builds run on GitHub-hosted macOS with Xcode. The Linux cloud workspace
can edit source but cannot compile iOS/watchOS targets locally.
The Apple repository's `watchos.yml` supports manual dispatch on the cloud branch.
Select `validation_only: true` for Swift tests and unsigned phone/watch simulator
builds. Main pushes and pull requests also trigger its build workflow.

The HA repository's `checks.yml` supports push, pull request and manual dispatch,
covering regression tests, Python/PHP checks, integration packaging and an HA
runtime job. Verify that the workflow is enabled before relying on it.

The Apple `testflight.yml` is manual and needs existing signing credentials.
Its upload option defaults to true: a request to build alone must not accidentally
publish. Verify credential readiness and coordinate any shared signing runner
before a signed build. Never commit signing material or runtime credentials.

App modifications and builds may proceed in the cloud for requested work.
TestFlight publication, App Store release, HA/relay deployment, device installation
and physical commands require an explicit request covering the action.
Record the exact tested revision, results and remaining physical-device checks.

This setup record makes no source changes and initiates no build or release.
