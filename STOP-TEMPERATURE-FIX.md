# Stop Exercise temperature — November 2, 2026

Task `fix/stop-exercise-temperature`, based on canonical main
`cfc30cfa711f76cf8e0764e38a63f9f32e4156db`, fixes the HA-owned Stop temperature
request. Corrupted unit literals caused normal Fahrenheit and Celsius units to
skip `set_temperature`. Stop also returned early when unlock/climate already
matched, skipping temperature entirely. Both paths now attempt 63°F (converted
to Celsius when required), including HA's configured-unit fallback.

Temperature retains its existing best-effort semantics; unlock/climate confirmation,
delivery deadlines, authorization and watch behavior are unchanged. Duplicate
handoffs reuse the same operation without another temperature request.

Eight regression cases cover Fahrenheit/Celsius, entity/configuration units,
initially locked/off and unlocked/on states, and duplicate handoffs. All eight
failed against the original implementation before the correction.

HA 0.10.1 requires no watch update. Physical temperature behavior still requires
a user-initiated Stop on the paired watch; deployment checks send no car commands.
