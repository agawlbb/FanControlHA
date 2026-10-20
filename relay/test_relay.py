"""Local-only HTTP/security tests using synthetic credentials; never sends APNs."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

PHP = shutil.which("php") or r"C:\xampp\php\php.exe"
SOURCE = Path(__file__).parent


class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fancontrol-relay-test-")
        cls.home = Path(cls.temp.name)
        public = cls.home / "site" / "FanControl"
        public.mkdir(parents=True)
        shutil.copy(SOURCE / "public" / "index.php", public / "index.php")
        private = cls.home / "fancontrol-private"
        private.mkdir()
        cls.config = private / "config.php"
        cls.config.write_text("<?php return ['enabled'=>true,'installations'=>['one'=>['secret'=>'" + "x"*64 + "'],'two'=>['secret'=>'" + "y"*64 + "']]];")
        router = cls.home / "router.php"
        router.write_text("<?php $_SERVER['HTTPS']='on'; require __DIR__.'/site/FanControl/index.php';")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/FanControl/"
        cls.server = subprocess.Popen([PHP, "-S", f"127.0.0.1:{port}", "-t", str(public.parent), str(router)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                urllib.request.urlopen(cls.url, timeout=1).close()
                return
            except OSError:
                time.sleep(.1)
        raise RuntimeError("Local PHP test server failed")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.temp.cleanup()

    def post(self, body, installation="one", nonce=None, timestamp=None, bad_signature=False):
        raw = json.dumps(body, separators=(",", ":")).encode()
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = nonce or secrets.token_hex(16)
        secret = b"x"*64 if installation == "one" else b"y"*64
        signature = hmac.new(secret, timestamp.encode()+b"\n"+nonce.encode()+b"\n"+raw, hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.url, data=raw, headers={"Content-Type":"application/json",
            "X-FanControl-Installation":installation,"X-FanControl-Timestamp":timestamp,
            "X-FanControl-Nonce":nonce,"X-FanControl-Signature":"0"*64 if bad_signature else signature})
        try:
            result = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            result = error
        with result:
            return result.status, json.load(result)

    def test_registration_replay_and_installation_isolation(self):
        body = {"action":"register","watch_id":"synthetic","device_token":"a"*64,"environment":"sandbox"}
        nonce = secrets.token_hex(16)
        self.assertEqual(self.post(body, nonce=nonce)[0], 200)
        self.assertEqual(self.post(body, nonce=nonce)[0], 409)
        self.assertEqual(self.post({"action":"refresh","watch_id":"synthetic","reason":"selection"}, installation="two")[0], 404)
        self.assertEqual(self.post({"action":"unregister","watch_id":"synthetic"})[0], 200)
        self.assertEqual(self.post({"action":"refresh","watch_id":"synthetic","reason":"selection"})[0], 404)

    def test_authentication_and_bounded_input(self):
        body = {"action":"unregister","watch_id":"synthetic"}
        self.assertEqual(self.post(body,bad_signature=True)[0],401)
        self.assertEqual(self.post(body,timestamp=int(time.time())-600)[0],401)
        self.assertEqual(self.post({**body,"command":"wake"})[0],400)
        self.assertEqual(self.post({"action":"wake","watch_id":"synthetic"})[0],400)
        self.assertEqual(self.post({**body,"huge":"x"*5000})[0],413)

    def test_get_is_static_and_post_fails_closed_without_config(self):
        with urllib.request.urlopen(self.url) as result:
            self.assertEqual(json.load(result)["status"], "configuration_required")
        held = self.config.with_suffix(".held")
        self.config.rename(held)
        try:
            self.assertEqual(self.post({"action":"unregister","watch_id":"synthetic"})[0],503)
        finally:
            held.rename(self.config)

    def test_jwt_signature_is_valid_es256(self):
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        key = ec.generate_private_key(ec.SECP256R1())
        key_file = self.home / "synthetic.p8"
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        functions = (SOURCE / "public" / "index.php").read_text().split("\ntry {",1)[0]
        script = self.home / "jwt-test.php"
        script.write_text(functions + '\necho jwt(["key_id"=>"SYNTHETIC","team_id"=>"SYNTHETIC","key_file"=>$argv[1]]);')
        jwt = subprocess.check_output([PHP,str(script),str(key_file)],text=True).strip()
        header,payload,signature = jwt.split(".")
        signature = base64.urlsafe_b64decode(signature+"==")
        self.assertEqual(len(signature),64)
        key.public_key().verify(encode_dss_signature(int.from_bytes(signature[:32],"big"),int.from_bytes(signature[32:],"big")),(header+"."+payload).encode(),ec.ECDSA(hashes.SHA256()))


if __name__ == "__main__":
    unittest.main()
