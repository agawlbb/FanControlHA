"""Private, paired-watch-only music provisioning. Never expose files via /local."""
import base64
import hashlib
from pathlib import Path

CHUNK_SIZE = 192 * 1024
EXTENSIONS = {".mp3", ".m4a", ".aac"}


def music_files(folder):
    root = Path(folder).resolve()
    if not root.is_dir():
        return []
    files = sorted((p for p in root.iterdir() if not p.is_symlink() and p.is_file()
                    and p.suffix.lower() in EXTENSIONS), key=lambda p: p.name.casefold())
    if not 1 <= len(files) <= 25 or sum(p.stat().st_size for p in files) > 200 * 1024 * 1024:
        raise ValueError("Music library limit")
    if any(not 0 < p.stat().st_size <= 30 * 1024 * 1024 or len(p.name) > 180 for p in files):
        raise ValueError("Music file limit")
    return files


def music_response(folder, body):
    files = music_files(folder)
    if body["action"] == "exercise_music_list":
        tracks = []
        for file in files:
            with file.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            tracks.append({"name": file.name, "size": file.stat().st_size, "sha256": digest})
        return {"tracks": tracks}
    name, offset = body.get("name"), body.get("offset")
    if type(offset) is not int or offset < 0:
        raise ValueError("Invalid music offset")
    file = next((p for p in files if p.name == name), None)
    if file is None or offset >= file.stat().st_size:
        raise ValueError("Unknown music file")
    with file.open("rb") as source:
        source.seek(offset)
        data = source.read(CHUNK_SIZE)
    return {"offset": offset, "data": base64.b64encode(data).decode("ascii")}


async def private_music(hass, watch, body):
    if watch.get("platform") != "watch_os" or not any(p.get("type") == "exercise" for p in watch.get("panels", [])):
        raise ValueError("Exercise music not approved")
    return await hass.async_add_executor_job(music_response, hass.config.path("fan_control_music"), body)
