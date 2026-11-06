# Phone-only message deployment — November 6, 2026

HA **0.14.0** is installed and loaded from accepted source
`477478ae8b6fb02682e43ab564c144e58572f293`. All 23 installed runtime
files match their pinned GitHub hashes. Configuration validation passed and
one authorized HA restart activated the integration.

The old live message field was removed during integration setup. Pairing owners,
secrets, companion grants, approved devices, panel roles and selections matched
the pre-deployment fingerprint afterward. Direct APNs remains enabled with only
enabled/team_id/key_id settings; the private key remains mode 0600.
Startup background push evidence records Apple's HTTP 200 acceptance. This does
not prove visible notification display or user receipt. No visible test alert or
physical command was sent for this deployment.

The loaded services include `fan_control.send_notification` and exclude
`fan_control.clear_messages`. The existing FlightSync forwarding automation
loaded with delay, schedule-attention and route-change triggers, without arrival
clearing. Messages now live only on the iPhone; a missed push has no HA message
history to recover.

Swipe dismissal, adaptive text and 12-hour expiry require iPhone **0.11.0 (26)**.
Publishing and installing that TestFlight update are separate from this HA
deployment. On-device rendering, swipe handling and Notification Center cleanup
remain paired-device checks.

Private source/state rollback evidence:
`/homeassistant/fancontrol-deploy-backups/phone-messages-20261106-151630/`.
Its deployment.json contains source hashes and verification. Backups retain the
previous state for recovery; preserve newer pairing/operation state rather than
blindly restoring a saved state snapshot.

Source validation: 330 integration and historical relay regression tests and
Python compilation passed before merge. This deployment changes no source beyond
that accepted commit. Deployment task `deploy/phone-messages-0.14` started from
that canonical commit.
