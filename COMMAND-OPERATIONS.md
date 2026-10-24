# Durable Apple command operations — HA 0.9.0

Apple 0.8.0 submits `operation_submit` for every HA control (vehicle aliases remain
compatible). Required fields are operation_id (UUID), issued_at_ms, absolute command,
and either approved entity or panel_id plus captured panel_config. Optional
not_before_ms is bounded to one second for fan-speed debounce. Exercise's local
music wire shape may omit legacy spotify fields without changing vehicle roles.

An authenticated operation is persisted in private `fan_control_operations` storage
before acknowledgment or physical dispatch. Workers run outside the runtime lock;
legacy simple service calls also release that lock. `operation_status` resumes the
same result; `operation_cancel` persists cancellation, including before a delayed
submission arrives. Duplicate submissions never extend deadlines or replay a one-
shot. Credential/owner/role changes invalidate old work; HA selection is independent.

HA restores active operations after restart within the original 180-second delivery
and 15-second read-only grace period. It persists a one-shot dispatch boundary
before calling covers/horn/scripts/scenes; a crash at that boundary can leave an
unconfirmed outcome but cannot safely be retried without target-side deduplication.
Desired-state operations retry with backoff and confirm available matching state.
Fan-speed drafts supersede within their one-second quiet window. Wake attempts are
shared by vehicle, and a superseded command cannot poison an unstarted wake.

Script/scene/horn/music results acknowledge dispatch only. Exercise failure notices
remain silent through normal wake/retry delays and occur only at a terminal result.
A matching trunk observation now completes, climate OFF does not require ON support,
and Work Morning battery telemetry cannot block healthy climate/script actions.

The watch separately saves intent in its keychain and schedules a watchOS background
upload before foreground monitoring. Operating-system scheduling and physical target
behavior require paired-device testing; exactly-once physical effects cannot be
promised when a target provides no deduplication or definitive acknowledgment.

No deployment or live physical commands were used for automated validation.
