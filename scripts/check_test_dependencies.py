"""Verify the CI-only overrides; no deployed HA environment is modified."""
from importlib.metadata import version
from io import BytesIO
import subprocess
import sys

from PIL import Image

assert version("uv") == "0.13.4", version("uv")
assert version("Pillow") == "13.0.0", version("Pillow")
assert version("homeassistant") == "2026.10.2", version("homeassistant")
# HA's exact pins are the only permitted metadata discrepancies. Validate all
# other dependencies through pip, and fail on any additional conflict.
result = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
expected = {
    "homeassistant 2026.10.2 has requirement pillow==12.3.0, but you have pillow 13.0.0.",
    "homeassistant 2026.10.2 has requirement uv==0.12.23, but you have uv 0.13.4.",
}
lines = {line.lower() for line in result.stdout.splitlines() if line.strip()}
if result.returncode != 1 or lines != expected or result.stderr.strip():
    raise RuntimeError(f"Unexpected dependency consistency result: {result.stdout} {result.stderr}")
for format in ("PNG", "TIFF", "JPEG"):
    stream = BytesIO()
    Image.new("RGB", (16, 16), (20, 40, 60)).save(stream, format=format)
    stream.seek(0)
    with Image.open(stream) as image:
        image.load()
        assert image.size == (16, 16)
print("HA 2026.10.2 test harness uses uv 0.13.4 and Pillow 13.0.0; image codecs load.")
