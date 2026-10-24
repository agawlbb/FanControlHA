"""Small HA API doubles; production HA runtime testing is a separate installation check."""
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("homeassistant", "homeassistant.components", "homeassistant.components.http", "homeassistant.components.select", "homeassistant.core", "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.entity", "homeassistant.helpers.storage", "homeassistant.helpers.dispatcher", "homeassistant.config_entries", "homeassistant.helpers.selector", "homeassistant.helpers.network"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["homeassistant.components.http"].HomeAssistantView = type("HomeAssistantView", (), {})
sys.modules["homeassistant.core"].Context = lambda **kwargs: kwargs
sys.modules["homeassistant.core"].callback = lambda function: function
sys.modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
sys.modules["homeassistant.exceptions"].ServiceValidationError = type("ServiceValidationError", (sys.modules["homeassistant.exceptions"].HomeAssistantError,), {})
sys.modules["homeassistant.helpers.storage"].Store = lambda *args: None
sys.modules["homeassistant.helpers.dispatcher"].async_dispatcher_send = lambda *args: None
sys.modules["homeassistant.helpers.dispatcher"].async_dispatcher_connect = lambda *args: lambda: None
sys.modules["homeassistant.components.select"].SelectEntity = type("SelectEntity", (), {"hass": None, "entity_id": None})
sys.modules["homeassistant.helpers.entity"].DeviceInfo = dict

class Flow:
    def __init_subclass__(cls, **kwargs):
        pass
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}
    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}
    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}
    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

sys.modules["homeassistant.config_entries"].ConfigFlow = Flow
sys.modules["homeassistant.config_entries"].OptionsFlow = Flow
for name in ("SelectSelector", "EntitySelector", "TextSelector"):
    setattr(sys.modules["homeassistant.helpers.selector"], name, lambda config: lambda value: value)
    setattr(sys.modules["homeassistant.helpers.selector"], name + "Config", lambda **kwargs: kwargs)
sys.modules["homeassistant.helpers.selector"].TextSelectorType = types.SimpleNamespace(PASSWORD="password")
sys.modules["homeassistant.helpers.selector"].ColorRGBSelector = lambda: lambda value: value
sys.modules["homeassistant.helpers.network"].get_url = lambda *args, **kwargs: "https://ha.example.com" if kwargs.get("require_ssl") else "http://192.168.1.2:8123"
sys.modules["homeassistant.helpers.network"].NoURLAvailableError = type("NoURLAvailableError", (Exception,), {})
