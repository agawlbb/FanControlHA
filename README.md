# Fan Control for Home Assistant

Pair and configure a standalone Wear OS watch entirely inside Home Assistant. The watch discovers HA on home Wi-Fi, displays an eight-digit code and fingerprint, and receives its selected devices and local/remote addresses after you approve pairing. No password or HA access token needs to be typed on the watch. No hosted website or phone companion is required.

Requires Home Assistant 2026.10 or newer and the integration-enabled Fan Control watch app. This is an initial custom integration, not an official Home Assistant component.

## Install

Copy `custom_components/fan_control` into the same directory under your Home Assistant configuration directory. Restart Home Assistant, then choose **Settings → Devices & services → Add integration → Fan Control**. No changes to `configuration.yaml` are needed.

HACS can also install it as a custom Integration repository when the repository is accessible to HACS. Private repositories may require access that HACS's normal GitHub authorization does not provide; manual installation works without giving HACS additional GitHub permissions.

## Pair and configure

1. Connect the watch directly to your home Wi-Fi. In the watch app, choose **Find Home Assistant**, then select the discovered instance. Manual address entry is available when multicast discovery is blocked.
2. Keep the watch's pairing screen visible. On a phone or computer, open **Settings → Devices & services → Fan Control → Configure → Pair a watch**.
3. Enter the eight-digit code. Compare **all 12 fingerprint characters** with the watch and confirm only when they match.
4. Name the watch, select an administrator who owns its access, choose 1–16 devices and an active tile device from that list. Local and remote addresses default from HA's network settings; review them before approving. The remote address must use HTTPS and already provide working remote access (including Home Assistant Cloud).
5. The watch receives and encrypts its connection settings locally. Add the Fan Control tile. Tap the tile title to open vertical swipe pages in the watch app.

To change devices, use **Configure → Configure watch devices and addresses**. To stop a watch connecting, use **Configure → Revoke a watch**. Removing the integration deletes its stored credentials. Removing an owning administrator or disabling that account also prevents its watches from connecting.

## Controls and automations

Fans retain power controls and six speeds mapped to **16, 33, 50, 66, 83, 100 percent**. Lights, switches and input booleans support power controls; scripts and scenes have a Run button. Additional device types can be added with explicit control adapters and server allowlists.

Use the **Fan Control: Set active watch device** action (`fan_control.set_active_device`) in any automation or script. Select an entity already approved for that watch and supply its full Watch ID, shown on the watch's setup screen. This changes the desired tile device without turning the physical device on or off. Changes appear on the next visible tile refresh or controls opening. A tap on a stale tile is converted into a refresh if the active device changed, rather than controlling the newly selected device.

Each paired watch also has a **Tile device** select entity in HA. You can choose its active device from that dropdown or use the standard **Select: Select option** action in automations, without entering a watch ID. A revoked watch's selector becomes unavailable.

The selected tile device appears as the first watch page; other approved devices follow their configured order. Native Wear OS tiles are part of the system's horizontal carousel; vertical device paging is in the full-screen watch controls opened from the tile.

## Battery and connections

Discovery is bounded to 20 seconds and only runs on the setup screen. Pairing polls every five seconds only while its screen is visible, with a ten-minute expiry. Normal configuration and state reads occur only on tile entry, explicit refresh/tap, or a visible settled watch page. There are no background subscriptions, periodic jobs, or push notifications. The watch cancels network work when leave/pause callbacks arrive.

The watch tries its saved local address and falls back to HTTPS remote access for reads when the local address cannot be reached. Commands follow the endpoint selected by a preceding read. Commands are never automatically replayed after ambiguous failures. The watch needs internet access to control HA away from home. Changing every reachable address at once requires pairing again; address changes can otherwise be delivered over an existing working connection.

Wear OS can delay tile visibility events. An already-started bounded request may finish before the platform delivers the leave event, and some watches may require tapping Refresh to show the newest state.

## Security and privacy

Pairing uses ephemeral watch RSA-2048 keys, OAEP-SHA256/MGF1-SHA256 key wrapping, and AES-256-GCM with the pairing code as authenticated data. Codes expire in ten minutes, and polling needs an independently generated 256-bit session token. Verify the fingerprint in the authenticated HA interface to prevent approving a substituted watch key. Pairing sessions are in memory and disappear on restart; an approved watch that did not finish pairing can be revoked in Configure.

Paired watch traffic uses AES-256-GCM request and response envelopes, a per-watch random 256-bit key, authenticated watch ID/timestamp/nonce, fresh IVs, and a 60-second clock window. Consumed nonces are persisted **before** command dispatch, preventing replay across restarts. This protects credentials, device IDs, configuration and state even over a local HTTP connection. Local HTTP is restricted to private network addresses and `.local` names; remote addresses require HTTPS with normal certificate validation. Network observers can still see host addresses, opaque watch IDs, message sizes, and timing.

The watch credential is **not** an HA bearer token. It authorizes only this integration's approved devices and fixed command allowlist, not arbitrary HA APIs/services. Scripts and scenes can themselves perform broader actions, so approve only the ones you intend the watch to run. Requests execute with the configured administrator's context. HA administrators can configure and revoke watches. Watch secrets are stored through HA's supported Store API under `.storage/fan_control` and must be protected with your HA configuration and backups; the integration never logs them.

The repositories contain no installed addresses, device IDs, access tokens, pairing keys, or personal watch configuration. Example addresses and credentials in tests are synthetic. Watch settings are encrypted using Android Keystore and excluded from backups. New watch setup uses local Home Assistant discovery and integration approval only. The retired hosted website and manual token-entry setup are no longer offered.

## Development

Install `cryptography aiohttp probatio voluptuous pytest pytest-asyncio` in an isolated environment and run `python -m pytest tests -q`. Tests exercise pairing encryption/expiry, request integrity/replay/revocation, device/command restrictions, original speed mappings, chunked-body limits, rate limiting, and native options-flow behavior using HA API doubles. These tests do not substitute for installation and a real-watch network/discovery check.

The second CI job loads the integration into an isolated real Home Assistant 2026.10.1 instance under Python 3.14 and checks its actual options flow, selector, actions, and unload. It uses synthetic devices and never connects to your home.

Live verification on Home Assistant 2026.10.1 also confirmed integration loading, native pairing approval, encrypted local/remote configuration delivery, encrypted device-state reads, the per-watch select entity, the active-device action, and rejection after revocation. Physical watch discovery and tile lifecycle behavior still require a hardware check.
