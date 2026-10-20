"""Verify the CI-only overrides; no deployed HA environment is modified."""
from importlib.metadata import version
from io import BytesIO

from PIL import Image

assert version("uv") == "0.13.4", version("uv")
assert version("Pillow") == "13.0.0", version("Pillow")
assert version("homeassistant") == "2026.10.2", version("homeassistant")
for format in ("PNG", "TIFF", "JPEG"):
    stream = BytesIO()
    Image.new("RGB", (16, 16), (20, 40, 60)).save(stream, format=format)
    stream.seek(0)
    with Image.open(stream) as image:
        image.load()
        assert image.size == (16, 16)
print("HA 2026.10.2 test harness uses uv 0.13.4 and Pillow 13.0.0; image codecs load.")
