# Fan Control for Home Assistant

> **Wear OS maintenance paused — October 20, 2026.** The Wear OS app is no longer actively maintained as of this date. Wear OS code changes and builds are paused unless explicitly requested by the maintainer. Existing Wear OS source and releases remain available, but updates and fixes should not be expected during the pause. This pause applies only to Wear OS; Apple Watch and the shared Home Assistant integration are outside its scope.

Licensed under GNU AGPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).

Pair and configure a standalone Wear OS watch entirely inside Home Assistant. The watch discovers HA on home Wi-Fi, displays an eight-digit code and fingerprint, and receives its selected devices and local/remote addresses after you approve pairing. No password or HA access token needs to be typed on the watch. No hosted website or phone companion is required.

Requires Home Assistant 2026.10 or newer and the integration-enabled Fan Control watch app. This is an initial custom integration, not an official Home Assistant component.

## Configurable watch panels

Pending source update: Apple Watch Driving panels support an optional **Podcast script** selector in the native panel editor. Select `script.baby_blue_podcast_play` to start the first unfinished episode from the selected queue on Spotify Joe's current device. The role is stored per panel as `podcast_entity`; only that approved script can be dispatched by the `podcast` command. Missing, running, unknown or unavailable scripts cannot dispatch. Podcast availability is independent of vehicle wake state. Existing Driving panels remain compatible without this optional role. Source only: not built, tested or deployed.

In **Settings → Devices & services → Fan Control → Configure → Manage watch panels**, choose a watch and **Add Work Morning panel**. Select a battery percentage sensor, a climate power entity (or switch/input boolean), and a script using HA entity pickers. Panel names and button labels are editable; no personal entity IDs are compiled into the watch. Battery sensors must use `%`; climate entities must support HA power on/off. Missing sensor values show `?%`, unavailable climate controls are disabled, and running scripts cannot be started again from the panel.

The Work Morning layout places battery percentage on the left and the climate toggle on the right of its top row, with a full-width script button in the bottom row. A script response confirms dispatch, not that the entire script completed. Existing fan controls and their speed mapping remain unchanged. The app shows only the HA-selected panel.

Choose **Select active panel** in the panel manager, or use the watch's new **Tile panel** select entity in an automation with `select.select_option`. Its options are `devices` and your stable panel IDs (default `work_morning`). Alternatively call **Fan Control: Set active watch panel** (`fan_control.set_active_panel`) with the Watch ID and Panel ID. `devices` restores the active device controls. The existing **Tile device** selector and `fan_control.set_active_device` also restore device controls when selecting a device.

Panels are stored per watch. Up to eight panels and sixteen total device/panel pages are supported. Their configured roles authorize only percentage reads, climate power, and their selected script; the watch cannot substitute another entity or an arbitrary service. Panel ID stays fixed when editing so automations do not break. Changing panel entities invalidates existing tile buttons until refresh. Removing the active panel falls back to device controls. Changes arrive on visible refreshes or the next periodic background configuration check. Background checks read selection/configuration only; they do not read device state, wake a vehicle, or run commands, and they do not hold Wi-Fi on.


## Install

Copy `custom_components/fan_control` into the same directory under your Home Assistant configuration directory. Restart Home Assistant, then choose **Settings → Devices & services → Add integration → Fan Control**. No changes to `configuration.yaml` are needed.

HACS can also install it as a custom Integration repository when the repository is accessible to HACS. Private repositories may require access that HACS's normal GitHub authorization does not provide; manual installation works without giving HACS additional GitHub permissions.

## Pair and configure

1. Connect the watch directly to your home Wi-Fi. In the watch app, choose **Find Home Assistant**, then select the discovered instance. Manual address entry is available when multicast discovery is blocked.
2. Keep the watch's pairing screen visible. On a phone or computer, open **Settings → Devices & services → Fan Control → Configure → Pair a watch**.
3. Enter the eight-digit code. Compare **all 12 fingerprint characters** with the watch and confirm only when they match.
4. Name the watch, select an administrator who owns its access, choose 1–16 devices and an active tile device from that list. Local and remote addresses default from HA's network settings; review them before approving. The remote address must use HTTPS and already provide working remote access (including Home Assistant Cloud).
5. The watch receives and encrypts its connection settings locally. Add the Fan Control tile. Tap the tile title to open controls for the HA-selected device/panel.

To change devices, use **Configure → Configure watch devices and addresses**. To stop a watch connecting, use **Configure → Revoke a watch**. Removing the integration deletes its stored credentials. Removing an owning administrator or disabling that account also prevents its watches from connecting.

## Controls and automations

Fans retain power controls and six speeds mapped to **16, 33, 50, 66, 83, 100 percent**. Lights, switches and input booleans support power controls; scripts and scenes have a Run button. Additional device types can be added with explicit control adapters and server allowlists.

Use the **Fan Control: Set active watch device** action (`fan_control.set_active_device`) in any automation or script. Select an entity already approved for that watch and supply its full Watch ID, shown on the watch's setup screen. This changes the desired tile device without turning the physical device on or off. Changes appear on the next visible tile refresh, controls opening, or periodic background configuration check. A tap on a stale tile is converted into a refresh if the active device changed, rather than controlling the newly selected device.

Each paired watch also has a **Tile device** select entity in HA. You can choose its active device from that dropdown or use the standard **Select: Select option** action in automations, without entering a watch ID. A revoked watch's selector becomes unavailable.

The selected tile device appears as the first watch page; other approved devices follow their configured order. Native Wear OS tiles are part of the system's horizontal carousel; vertical device paging is in the full-screen watch controls opened from the tile.

## Battery and connections

Discovery is bounded to 20 seconds and only runs on the setup screen. Pairing polls every five seconds only while its screen is visible, with a ten-minute expiry. Ordinary device-state reads occur on tile entry, explicit refresh/tap, or a visible settled watch page. While the main tile is visible, configuration selection checks run independently every two seconds and device state refreshes five seconds after the preceding state operation finishes. Leaving the tile or turning the display off cancels visible polling and network calls. There are no background subscriptions or push messages from HA.

When no app screen or tile is visible, a persisted Android JobScheduler job checks the selected panel/device configuration approximately every fifteen minutes, with a five-minute scheduling window and battery-not-low/network constraints. It uses the default available connection, including the phone Bluetooth proxy, Wi-Fi, or LTE, without requesting or holding Wi-Fi. Android may delay checks during Doze, battery saving, low battery, or unavailable connectivity. These checks never read device state or dispatch controls. Each endpoint attempt has a four-second total timeout; safe read fallback can require two attempts. Failures wait for the next periodic window. Opening the tile/app cancels an in-progress check. Changed configuration is saved only if the original connection still matches; unchanged configuration causes no write or tile update. Forgetting the connection cancels the job. Access rejection (401/403) clears only the still-matching connection and cancels future checks; other HTTP/network failures retain pairing. Real-watch scheduling, LTE reachability, and battery impact still need measurement.

Explicit Work Morning and Exercise button taps use a separate user-started foreground delivery service for up to one minute, including screen timeout. It uses a bounded partial wake lock, inline requested-state feedback, and progress/Cancel notifications. An unconfirmed action produces a failure notification and vibration at the watch deadline. This is separate from passive background configuration checks and from the HA-side two-minute expiry for an accepted Exercise Stop operation.

The watch tries its saved local address and falls back to HTTPS remote access for reads when the local address cannot be reached. Each transport command follows the endpoint selected by a preceding read and is never automatically replayed through another endpoint after an ambiguous failure. Explicit panel actions use the bounded delivery policy described below: desired-state vehicle commands can retry, scripts are dispatched at most once, and music is requested separately at most once per Start tap. The watch needs internet access to control HA away from home. Changing every reachable address at once requires pairing again; address changes can otherwise be delivered over an existing working connection.

Wear OS can delay tile visibility events. An already-started bounded request may finish before the platform delivers the leave event, and some watches may require tapping Refresh to show the newest state.

## Security and privacy

Pairing uses ephemeral watch RSA-2048 keys, OAEP-SHA256/MGF1-SHA256 key wrapping, and AES-256-GCM with the pairing code as authenticated data. Codes expire in ten minutes, and polling needs an independently generated 256-bit session token. Verify the fingerprint in the authenticated HA interface to prevent approving a substituted watch key. Pairing sessions are in memory and disappear on restart; an approved watch that did not finish pairing can be revoked in Configure.

Paired watch traffic uses AES-256-GCM request and response envelopes, a per-watch random 256-bit key, authenticated watch ID/timestamp/nonce, fresh IVs, and a 60-second clock window. Consumed nonces are persisted **before** command dispatch, preventing replay across restarts. This protects credentials, device IDs, configuration and state even over a local HTTP connection. Local HTTP is restricted to private network addresses and `.local` names; remote addresses require HTTPS with normal certificate validation. Network observers can still see host addresses, opaque watch IDs, message sizes, and timing.

The watch credential is **not** an HA bearer token. It authorizes only this integration's approved devices and fixed command allowlist, not arbitrary HA APIs/services. Scripts and scenes can themselves perform broader actions, so approve only the ones you intend the watch to run. Requests execute with the configured administrator's context. HA administrators can configure and revoke watches. Watch secrets are stored through HA's supported Store API under `.storage/fan_control` and must be protected with your HA configuration and backups; the integration never logs them.

Example addresses and credentials in tests are synthetic. See [Security reports and publication](SECURITY.md) before sharing source or workspace files; ignored HA configuration and backups can contain live credentials. Watch settings are encrypted using Android Keystore and excluded from backups. New watch setup uses local Home Assistant discovery and integration approval only. The retired hosted website and manual token-entry setup are no longer offered.

## Development

Install `cryptography aiohttp probatio voluptuous pytest pytest-asyncio` in an isolated environment and run `python -m pytest tests -q`. Tests exercise pairing encryption/expiry, request integrity/replay/revocation, device/command restrictions, original speed mappings, chunked-body limits, rate limiting, and native options-flow behavior using HA API doubles. These tests do not substitute for installation and a real-watch network/discovery check.

The second CI job loads the integration into an isolated real Home Assistant 2026.10.1 instance under Python 3.14 and checks its actual options flow, selector, actions, and unload. It uses synthetic devices and never connects to your home.

Live verification on Home Assistant 2026.10.1 also confirmed integration loading, native pairing approval, encrypted local/remote configuration delivery, encrypted device-state reads, the per-watch select entity, the active-device action, and rejection after revocation. Physical watch discovery and tile lifecycle behavior still require a hardware check.

## Removing watch devices

Open a Fan Control watch device in **Settings → Devices & services → Devices**, then choose **Delete** from its menu. HA removes its registry device and associated entities; the integration first revokes its watch credential and pending pairing handoff. This also works for previously revoked, unavailable watch devices. Removing the active watch requires pairing it again before use.


## Optional vehicle wake on visible entry

Edit a Work Morning panel and select both **Vehicle wake button** and **Vehicle online binary sensor** for the same vehicle. Leave both unset for an ordinary panel. The online binary sensor must be `on` only when the vehicle is online. The watch presses the approved wake button once on settled visible entry, then makes read-only readiness checks for up to 30 seconds (an in-flight bounded network call may finish later). Leaving cancels further work; an already-dispatched wake cannot be recalled. Refresh and normal visible polls never repeat wake; leave and re-enter to start another attempt. No configuration/background/prefetch request wakes a vehicle.

Vehicle readings remain unknown until a subsequent HA read reports online status and valid battery/climate fields. Current watch versions accept panel button taps immediately using saved approved roles, display the requested state in gray, and disable pending controls while the bounded delivery service waits for readiness and confirmation. Actions are revalidated by HA before dispatch. Tessie may supply cached values: this confirms online status plus valid HA fields, **not fresh vehicle measurements**. No source timestamp is inferred from HA `last_updated` or `last_reported`. An unknown wake-button state is normal. Existing panels need the optional roles configured to enable this behavior; no personal entity IDs are compiled into either project.


## Exercise panel (development)

Configure an Exercise panel with the same vehicle's explicit lock, climate, wake-button and online-sensor roles. The separate Exercise watch tile requires exactly one Exercise panel; ambiguous configurations remain disabled. The panel also appears in the watch app and can be selected as the main tile panel. No vehicle names or personal entity IDs are built in.

Start requests a door lock and opens the installed watch Spotify app immediately. Integration 0.3.1 adds optional Spotify media player, playlist URL/URI, and playback source fields to the Exercise form. A separate one-time Start music request uses existing HA authorization to disable shuffle, request playlist playback, then disable shuffle again. Vehicle retries never replay music. Blank source uses Spotify's active device, which may not be the watch; an explicitly configured unavailable source never falls back. Music failure does not prevent vehicle locking. Playlist sharing parameters are discarded. Stop preflights climate power/temperature support before requesting unlock, climate on, then 63°F (17.222…°C). An advertised step incompatible with that exact target disables Stop, rather than silently rounding. No rounding policy is currently configured. Stop optionally requests pause through exactly one local Spotify media session on the watch. User-granted notification-listener access is required; the app offers a settings button but never grants access. Remote sessions, other players, ambiguous sessions, and sessions without pause support are never controlled. There is no broad media-key or Spotify Connect fallback. Pause is reported as requested, not confirmed.

Service completion is reported as requested, not proof of physical completion. For the original one-shot flow an ambiguous error stops later steps. The bounded Stop operation described below explicitly retries only unfinished desired-state steps. Current watch versions also retry the idempotent Exercise Start lock and Work Morning climate intent within their one-minute delivery window; script and music requests are not blindly replayed. No rollback occurs. Leaving cancels passive visible work; an explicit pending panel action can continue with the display off until its one-minute deadline or cancellation. Neither cancellation nor leaving can recall already dispatched HA services. Reopen the tile after a readiness timeout; the configured Stop button can still acknowledge a pending request while offline. Setup, hidden rendering and background configuration checks never wake a car. Old one-use button grants expire, cannot be replayed, and are rejected if the watch or panel target changed.

Integration 0.3.0 packages Exercise and vehicle-role setup in the native HA options flow. The companion watch changes have passed local Android build, tests, and lint; real-watch behavior still needs verification.


## Bounded Stop retries with screen off

Stop acknowledges the button immediately with visible feedback and a haptic. A user-started connected-device foreground service keeps one Stop operation pending for up to one minute from the tap, including screen timeout, with a bounded partial wake lock and an ongoing progress/Cancel notification. Notification permission is requested on Android 13+; enable it for notification progress and cancellation. Progress stays on the existing tile or controls page with inline pending feedback; the foreground notification provides Cancel. Spotify pause is requested once per tap, outside the car retry loop.

Before HA acceptance the watch retries delivery with backoff using the same operation ID. After acceptance HA can retry independently if the watch disconnects. Each retry reconciles unlocked/climate-on/temperature state, skips confirmed steps, and retries only unfinished desired-state commands. Requests are throttled and deduplicated. No background task is persisted across HA/watch restart; a two-minute expiry rejects delayed requests. New Start cancels pending local Stop and invalidates its HA operation when Start reaches HA. Target changes, revoked pairing, inactive owners and integration unload stop HA retries.

Cancellation over a broken connection is best effort: an already-dispatched command can finish, and HA may continue until cancellation arrives or the original two-minute expiry. Android may stop the process, so exact delivery timing is not guaranteed. The operation is never silently restored after reboot. Watch 1.4.2 also accepts Work Morning and Exercise Start taps immediately while the vehicle wakes. Pending controls show the requested state in gray and disable until confirmation. Climate retries keep one absolute on/off intent; scripts are never blindly repeated after an ambiguous response. Watch delivery alerts after one minute without confirmation; this does not change the HA-side two-minute maximum expiry for an already accepted Stop, whose cancellation remains best effort over a broken connection. Local Android build, tests, and lint passed.

Integration 0.3.3 schedules the one-time Exercise music request five seconds after receipt, giving the user time to start watch playback. The delay runs separately from car commands and adds no watch countdown or status display. Before music dispatch, HA rechecks the watch, owner, and exact panel configuration. A newer music request replaces an older pending one; Stop cancels pending music. Reload/restart cancels delayed work rather than replaying it.


## Driving tile (watch 1.5.0 / integration 0.4.0)

In Fan Control's native HA Configure flow, choose Manage watch panels, select
the watch and Add Driving panel. Select distinct garage and trunk `cover`
entities with explicit open/close support, the vehicle's climate entity, wake
button and online binary sensor. Add the separate **Driving** tile on the watch.
It requires exactly one Driving panel; multiple candidates remain disabled.
The panel can be selected in HA as the
main tile's active panel. Entity choices stay in HA; none are compiled in.

Garage and trunk are open/close toggles; Climate ON always requests ON. On each
visible entry the watch requests vehicle wake once; refresh, prefetch, background
configuration checks and hidden rendering never wake a vehicle. Command delivery and retries never send wake; only a currently displayed vehicle page does.

Before a current trunk state is available, the trunk displays **CLOSED in gray**
and is immediately tappable: its first tap fixes an **OPEN** request. This is a
presentation assumption, never a confirmed or cached closed state. A later read
cannot flip that tap into Close. A confirmed OPEN trunk offers Close next; a
confirmed CLOSED trunk offers Open. Moving states remain gray. Garage also begins CLOSED in gray and accepts an immediate explicit Open tap;
it can operate while the vehicle is offline. Vehicle trunk/climate dispatch
waits for the selected online sensor. HA observations can still be cached by
the upstream integration and do not independently prove physical freshness.

Every command immediately displays its requested outcome in gray and disables
conflicting controls. Matching available HA state changes it to confirmed
colors. Delivery is bounded to one minute with progress/Cancel and an unconfirmed
timeout alert. Garage/trunk commands are sent at most once per tap, including
after an ambiguous response; subsequent attempts only read for confirmation.
Climate keeps its absolute ON intent. No `cover.toggle` is used. Cancel cannot
recall an already-dispatched action. Role changes and expired one-use buttons
cannot redirect old commands to another cover or vehicle. No operation resumes
after reboot. Install both updated components before configuring this panel.

## Panel colors and vertical navigation (watch 1.5.1 / integration 0.4.1)

Garage and trunk both start CLOSED in gray when no current state is known;
the first tap fixes an OPEN request immediately. Neither assumption is cached
or treated as a confirmed observation. Select **Car identification color** in
HA when adding/editing Work Morning, Exercise or Driving panels. Each vehicle
panel shows that RGB color in its top bar; it never replaces command feedback
colors. Existing panels use mint until a color is saved.

The bar only identifies the car. Watch version 1.5.2 shows only the HA-selected device/panel and disables watch-side page switching. Change the active selection in HA.

Only a currently displayed, settled vehicle page sends its wake request on
entry. Hidden pages, page prefetch, refreshes, configuration/background reads,
command delivery and screen-off retries never send wake. A previously dispatched
wake cannot be recalled if the user leaves before its network response arrives.


Confirmed Stop Exercise selects Driving (HA 0.4.2): after the authenticated
Stop operation confirms unlock, climate on and target temperature, HA selects
this watch's unique Driving panel matching climate, wake and online roles.
This works on the immediate response or background continuation and does not
depend on location. Pending, failed, expired, cancelled, stale and ambiguous
operations must not select another car. Duplicate completed polls must not
reapply selection over a later manual choice. Selection sends no vehicle wake
or other physical command. All watch surfaces share the HA completion path.
Deployment validation: 163 integration tests and Python compilation passed.

## Apple Watch setup (0.5.0)

Apple Watch pairing accepts the watch name, owner and HA addresses without requiring a fan. Add Fan Control as an optional panel in Manage watch panels, alongside Exercise, Driving and Work Morning. The first panel on a watch without devices becomes active. Existing Android and Apple Watch device approvals remain supported.

Exercise explicitly requires a playlist when its optional Spotify player is selected. Leave both blank to skip Spotify. Invalid submissions retain their field values, including the watch name. Only Driving has a car identification color picker. Device registry names update from the saved watch configuration, and authenticated watchOS identification repairs legacy Apple Watch records on first connection from watch app 0.2.0.

Validation: 169 integration tests, Python compilation, and isolated Home Assistant 2026.10.1 startup, pairing, panel, selection and device metadata checks passed. These tests send no physical device commands.

## Apple Watch Driving controls (integration 0.5.1)

Driving panels optionally approve a distinct horn button in the native panel manager. Apple Watch 0.3.0 displays garage, trunk, climate ON/OFF and horn in a 2×2 grid. Climate OFF checks the turn-off capability. Horn presses dispatch once and do not claim physical confirmation. Existing panels and Android controls remain supported; only approved panel roles can dispatch commands.

Apple Watch can swipe through approved controls for the foreground session without changing HA selection. Reopening restores the HA-selected control. Only a displayed, settled vehicle entry may wake; swiping past a page before it settles cancels that entry work.
