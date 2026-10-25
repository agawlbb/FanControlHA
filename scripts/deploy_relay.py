"""Deploy one pinned FanControl public entry using existing cPanel credentials.

Never reads or writes the provider key, private config, registration database or
another website. Called only by an explicitly authorized release workflow.
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import urllib.parse
import urllib.request

HOST = "https://ecngx362.inmotionhosting.com:2083"
PUBLIC_DIR = "/home/negaau5/coffeybean.com/FanControl"
PRIVATE_DIR = "/home/negaau5/fancontrol-private"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect())
        self.authorization = "cpanel " + os.environ["CPANEL_USER"] + ":" + os.environ["CPANEL_API_TOKEN"]
    def request(self, method, fields, files=None):
        if files:
            boundary = "fancontrol-" + secrets.token_hex(16)
            parts = []
            for name, value in fields.items():
                parts.append(("--"+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+value+'\r\n').encode())
            for name, content in files.items():
                parts.append(("--"+boundary+'\r\nContent-Disposition: form-data; name="file-1"; filename="'+name+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+content+b"\r\n")
            raw=b"".join(parts)+("--"+boundary+"--\r\n").encode()
            kind="multipart/form-data; boundary="+boundary
        else:
            raw=urllib.parse.urlencode(fields).encode();kind="application/x-www-form-urlencoded"
        req=urllib.request.Request(HOST+"/execute/Fileman/"+method,data=raw,headers={"Authorization":self.authorization,"Content-Type":kind})
        with self.opener.open(req,timeout=60) as response:
            result=json.load(response)
        if result.get("status") != 1:
            raise RuntimeError("cPanel operation failed; private response omitted")
        return result.get("data")
    def read(self, directory, name):
        return self.request("get_file_content",{"dir":directory,"file":name})["content"].encode()
    def upload(self, directory, name, content):
        allowed = directory == PUBLIC_DIR and name == "index.php" or directory == PRIVATE_DIR and re.fullmatch(r"index-before-outcomes-[0-9TZ]+\.php", name)
        if not allowed: raise ValueError("Deployment path rejected")
        self.request("upload_files",{"dir":directory,"overwrite":"1"},{name:content})

def deploy(sha, client=None):
    if not re.fullmatch(r"[a-f0-9]{40}",sha): raise ValueError("Pinned source required")
    with urllib.request.urlopen("https://raw.githubusercontent.com/agawlbb/FanControlHA/"+sha+"/relay/public/index.php",timeout=30) as response:
        source=response.read()
    if not source.startswith(b"<?php") or b"function outcome_payload" not in source: raise ValueError("Relay source rejected")
    client=client or Client()
    previous=client.read(PUBLIC_DIR,"index.php")
    backup="index-before-outcomes-"+datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".php"
    client.upload(PRIVATE_DIR,backup,previous)
    if client.read(PRIVATE_DIR,backup) != previous: raise RuntimeError("Rollback source verification failed")
    client.upload(PUBLIC_DIR,"index.php",source)
    if client.read(PUBLIC_DIR,"index.php") != source:
        client.upload(PUBLIC_DIR,"index.php",previous)
        raise RuntimeError("Relay readback failed; previous source restored")
    try:
        with urllib.request.urlopen("https://coffeybean.com/FanControl/",timeout=30) as response:
            discovery=json.load(response)
        if discovery.get("service") != "FanControl push relay" or discovery.get("status") != "available":
            raise ValueError("Unexpected discovery")
    except Exception:
        client.upload(PUBLIC_DIR,"index.php",previous)
        raise RuntimeError("Discovery check failed; previous source restored") from None
    result={"source":sha,"relay_sha256":hashlib.sha256(source).hexdigest(),"backup":PRIVATE_DIR+"/"+backup,
            "public_entry_verified":True,"private_config_key_database_written":False}
    print(json.dumps(result))
    return result

if __name__ == "__main__":
    try: deploy(os.environ["FANCONTROL_SOURCE_SHA"])
    except Exception as error:
        print("FanControl-only relay deployment failed: "+type(error).__name__)
        raise SystemExit(1) from None
