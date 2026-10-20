"""Package installable integration source only; never include HA state or keys."""
import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
source = root / "custom_components" / "fan_control"
version = json.loads((source / "manifest.json").read_text())["version"]
destination = root / "dist" / f"fan_control-{version}.zip"
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".yaml"} and "__pycache__" not in path.parts:
            entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
print(f"Built {destination.name}")
print("SHA256 " + hashlib.sha256(destination.read_bytes()).hexdigest())
