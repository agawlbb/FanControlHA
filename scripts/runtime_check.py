"""Load this integration in an isolated real HA instance; never connects to a home."""
import asyncio
import shutil
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from homeassistant.core import HomeAssistant
from homeassistant.bootstrap import async_setup_hass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.auth.const import GROUP_ID_ADMIN


async def main():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(__file__).resolve().parents[1] / "custom_components"
        shutil.copytree(source, Path(directory) / "custom_components", ignore=shutil.ignore_patterns("__pycache__"))
        hass = HomeAssistant(directory)
        try:
            # Bootstrap initializes auth, loaders, and the config-entry manager as on HA startup.
            await async_setup_hass(hass, {"http": {"server_host": "127.0.0.1", "server_port": 0}, "fan_control": {}})
            assert "fan_control" in hass.config.components
            result = await hass.config_entries.flow.async_init("fan_control", context={"source": "user"}, data={})
            assert result["type"] == "create_entry", result
            entry = result["result"]
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED, entry.state
            owner = await hass.auth.async_create_user("Test administrator", group_ids=[GROUP_ID_ADMIN])
            # Synthetic states are sufficient: this check never calls a device service.
            hass.states.async_set("fan.example", "off", {"friendly_name": "Example fan", "supported_features": 1, "percentage": 0})
            hass.states.async_set("light.example", "off", {"friendly_name": "Example light"})
            from custom_components.fan_control.protocol import encode
            keys = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            manager = hass.data["fan_control"]["manager"]
            pairing = manager.create(encode(keys.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)))
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            assert flow["type"] == "menu", flow
            flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "pair"})
            assert flow["step_id"] == "pair", flow
            flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"code": pairing["code"]})
            assert flow["step_id"] == "confirm", flow
            flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"matches": True})
            assert flow["step_id"] == "devices", flow
            flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"name": "Example watch", "owner_id": owner.id, "entities": ["fan.example", "light.example"], "active_entity": "fan.example", "local_url": "http://127.0.0.1:8123", "remote_url": "https://ha.example.com"})
            assert flow["type"] == "create_entry", flow
            await hass.async_block_till_done()
            watch_id = next(iter(manager.watches))
            selects = hass.states.async_all("select")
            assert len(selects) == 1 and selects[0].state == "fan.example", selects
            await hass.services.async_call("fan_control", "set_active_device", {"watch_id": watch_id, "entity_id": "light.example"}, blocking=True)
            await hass.async_block_till_done()
            assert hass.states.get(selects[0].entity_id).state == "light.example"
            await hass.services.async_call("select", "select_option", {"entity_id": selects[0].entity_id, "option": "fan.example"}, blocking=True)
            assert manager.watches[watch_id]["entity"] == "fan.example"
            assert await hass.config_entries.async_unload(entry.entry_id)
            print("Real Home Assistant load, pairing/options flow, selector, actions, and unload passed.")
        finally:
            await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
