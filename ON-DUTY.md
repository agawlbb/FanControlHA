# On Duty tile and iPhone messages

Task `feature/on-duty`, based on canonical main
`8ec6de4369788858f33016453ac83bea7ffccc59` (HA) and
`d21ffd123048f34547e5ccaab7af12aecbe2a025` (Apple).

HA 0.12.0 adds an Apple-only, read-only On Duty panel. Configure the city pair
and departure sensors in the native panel manager. FlightSync selects the flight;
FanControl copies those values without choosing a flight or showing its number.
The panel uses existing active-panel change hints and companion snapshots.
It never wakes or controls a device. On watch opening, saved data is gray;
a fresh authenticated read or newly received available snapshot turns it teal.
Departure uses the watch's local time, and time to go counts down locally to zero.
Unknown or unavailable values remain gray.

Automation notifications now retain title and full message with the same push ID
in private per-watch HA storage. Companion snapshots deliver messages to the
linked iPhone. Tapping an alert opens its full text even on a cold app launch;
messages also appear in the phone's Messages section. No messages go in Git,
diagnostics, or relay storage. Unlinking the companion clears the messages.
`fan_control.clear_messages` clears all messages for the selected watch's iPhone,
persists the empty list, and queues an ordinary refresh hint. The next successful
snapshot clears the list and any open message. Notification Center is unchanged.
A suspended/offline phone reconciles at its next allowed refresh/opening.
No relay changes or FlightSync changes are required.

## Prepared automations

The following are configured and enabled in HA. The existing Work Morning and
other automations are unchanged.

- `automation.joe_watch_on_duty_tile`: On duty becomes on → select `on_duty`
  on `select.watch_tile_panel`.
- `automation.joe_watch_flightsync_messages`: listen for FlightSync's
  `next_flight_delayed`, `schedule_needs_attention`, `flight_route_changed`, and
  `flight_arrived` bus events, filtered to Joe's FlightSync config entry.
  The first three call `fan_control.send_notification`; arrival calls
  `fan_control.clear_messages`. Queued execution preserves event order.
  FlightSync's title/message are forwarded directly where supplied; route
  changes include the city pair and new route.

## Activation

Install the HA integration and matching iPhone/watch update through the existing
release process. Under Fan Control → Manage watch panels, add On Duty with ID
`on_duty`, city pair `sensor.joe_s_work_schedule_flight_airports` and departure
`sensor.joe_s_work_schedule_flight_start`. Enable the two prepared automations.
Deployment completed November 5, 2026 from source
`f4bff5e215f83e2912029cee9019c25d7f4bc96e`. All ten changed existing files
matched the installed 0.11.0 source before copying. Eleven updated/new files
read back exactly. HA configuration validation passed; one Core restart loaded
0.12.0, verified through the integration manifest and new clear_messages action.
The On Duty panel is configured with the entities above and both automations
are enabled. Pairing and existing panels/selection were preserved. FanControl
0.10.0 (24) is available to the existing Internal TestFlight group, Apple build
`28b6c495-b6b1-4d38-a274-860da576692b`, VALID and IN_BETA_TESTING.
No physical commands or synthetic flight notifications were sent as validation.
User/device installation and physical checks remain.

## Validation

297 Python integration tests and eight relay tests passed; Python compilation passed.
All 78 Swift tests and iPhone/watch source typechecks passed on Mac run
https://github.com/agawlbb/FanControlWatch/actions/runs/42039898195.
Physical checks: gray cache then teal refreshed flight data, background phone
updates, departure/countdown across timezones, full text on cold/warm notification
taps, and arrival clearing while the phone is suspended then reopened.
