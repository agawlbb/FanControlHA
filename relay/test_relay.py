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

    def test_correlation_validation_and_apns_evidence_never_claim_delivery(self):
        update = "12345678-1234-4234-a234-123456789abc"
        body = {"action":"refresh", "watch_id":"unregistered", "reason":"selection", "update_id":update}
        self.assertEqual(self.post(body)[0], 404) # Valid hint schema; no APNs dispatch.
        self.assertEqual(self.post({**body,"update_id":"invalid\r\nheader"})[0], 400)
        functions = (SOURCE / "public" / "index.php").read_text().split("\ntry {",1)[0]
        script = self.home / "evidence-test.php"
        script.write_text(functions + '\necho json_encode([json_decode(refresh_payload($argv[1], "synthetic"),true),apns_evidence($argv[1],200,""),apns_evidence($argv[1],400,\'{"reason":"BadDeviceToken"}\'),apns_evidence($argv[1],0,false)]);')
        payload, accepted, rejected, ambiguous = json.loads(subprocess.check_output([PHP,str(script),update],text=True))
        self.assertEqual(payload["fancontrol"], {"refresh":True,"update_id":update,"watch_id":"synthetic"})
        for evidence in (accepted, rejected, ambiguous):
            self.assertEqual(evidence["apns_id"], update)
            self.assertFalse(evidence["watch_delivery_confirmed"])
        self.assertEqual(rejected["apns_reason"], "BadDeviceToken")

    def test_outcome_payloads_and_schema_use_separate_watch_route(self):
        operation = "12345678-1234-4234-a234-123456789abc"
        body = {"action":"outcome","watch_id":"synthetic.commands","operation_id":operation,"status":"failed","update_id":operation}
        self.assertEqual(self.post(body)[0],404)
        self.assertEqual(self.post({**body,"watch_id":"synthetic"})[0],400)
        self.assertEqual(self.post({**body,"status":"pending"})[0],400)
        self.assertEqual(self.post({**body,"title":"arbitrary"})[0],400)
        functions=(SOURCE/"public"/"index.php").read_text().split("\ntry {",1)[0]
        script=self.home/"outcome-test.php"
        script.write_text(functions+'\necho json_encode([json_decode(outcome_payload($argv[1],"failed"),true),json_decode(outcome_payload($argv[1],"complete"),true)]);')
        failed,complete=json.loads(subprocess.check_output([PHP,str(script),operation],text=True))
        self.assertIn("alert",failed["aps"])
        self.assertNotIn("alert",complete["aps"])
        self.assertEqual(complete["aps"]["content-available"],1)
        self.assertEqual(failed["fancontrol"],{"operation_id":operation})

    def test_get_is_static_and_post_fails_closed_without_config(self):
        with urllib.request.urlopen(self.url) as result:
            value = json.load(result)
            self.assertEqual(value["status"], "available")
            self.assertEqual(value["authentication"], "required")
        held = self.config.with_suffix(".held")
        self.config.rename(held)
        try:
            self.assertEqual(self.post({"action":"unregister","watch_id":"synthetic"})[0],503)
        finally:
            held.rename(self.config)

    def test_automation_notification_validation_and_phone_destination(self):
        update = "12345678-1234-4234-a234-123456789abc"
        body = {"action":"notify", "watch_id":"alerts.notifications", "title":"Garage", "message":"Still open", "update_id":update}
        self.assertEqual(self.post(body)[0],404)
        for changes in ({"watch_id":"alerts.commands"}, {"message":" "}, {"message":"x"*1501},
                        {"title":"😀"*51}, {"message":"bad\x00text"}, {"message":[]},
                        {"update_id":"invalid"}, {"url":"https://example.invalid"}):
            self.assertEqual(self.post({**body, **changes})[0],400)
        self.assertEqual(self.post({"action":"register", "watch_id":body["watch_id"],
                                   "device_token":"a"*64, "environment":"sandbox"})[0],200)
        self.assertEqual(self.post(body)[0],400) # A watch registration cannot receive phone alerts.
        self.post({"action":"unregister", "watch_id":body["watch_id"]})
        functions = (SOURCE / "public" / "index.php").read_text().split("\ntry {",1)[0]
        script = self.home / "notification-test.php"
        script.write_text(functions + '\necho notification_payload($argv[1], "synthetic", "Garage 😀", "Still open\\nCheck the door");')
        payload = json.loads(subprocess.check_output([PHP,str(script),update],text=True))
        self.assertEqual(payload["aps"]["alert"], {"title":"Garage 😀", "body":"Still open\nCheck the door"})
        self.assertEqual(payload["aps"]["sound"], "default")
        self.assertNotIn("content-available", payload["aps"])
        self.assertEqual(payload["fancontrol"], {"notification":True, "update_id":update, "watch_id":"synthetic"})

    def test_automation_dispatch_uses_fancontrol_phone_topic_and_alert_headers(self):
        # Run the actual request entry with cURL replaced by an in-process fake.
        # Disable extensions except PDO/SQLite; this cannot reach APNs or any network.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import base64
        key = ec.generate_private_key(ec.SECP256R1())
        key_file = self.home / "routing-synthetic.p8"
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.config.write_text("<?php return ['enabled'=>true,'key_id'=>'SYNTHETIC','team_id'=>'SYNTHETIC','topic'=>'synthetic.watch.topic', 'key_file'=>" + json.dumps(str(key_file)) + ", 'installations'=>['one'=>['secret'=>'" + "x"*64 + "'],'two'=>['secret'=>'" + "y"*64 + "']]];")
        identity = "routing.notifications"
        self.assertEqual(self.post({"action":"register", "watch_id":identity, "device_token":"b"*64,
                                   "environment":"sandbox", "destination":"phone"})[0],200)
        update = "12345678-1234-4234-a234-123456789abc"
        raw = json.dumps({"action":"notify", "watch_id":identity, "title":"Garage", "message":"Still open", "update_id":update}, separators=(",", ":")).encode()
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        signature = hmac.new(b"x"*64,timestamp.encode()+b"\n"+nonce.encode()+b"\n"+raw,hashlib.sha256).hexdigest()
        source = (SOURCE / "public/index.php").read_text().replace("'php://input'", "__DIR__.'/routing-body.json'")
        entry = self.home / "site/FanControl/routing-entry.php"
        entry.write_text(source)
        (entry.parent / "routing-body.json").write_bytes(raw)
        capture = self.home / "capture.json"
        fake = self.home / "routing.php"
        fake.write_text("<?php\n" + "\n".join([
            "define('"+name+"',"+str(i+1)+");" for i,name in enumerate((
                "CURL_VERSION_HTTP2", "CURLOPT_POST", "CURLOPT_RETURNTRANSFER", "CURLOPT_HTTP_VERSION", "CURL_HTTP_VERSION_2_0",
                "CURLOPT_CONNECTTIMEOUT", "CURLOPT_TIMEOUT", "CURLOPT_PROTOCOLS", "CURLPROTO_HTTPS", "CURLOPT_HTTPHEADER",
                "CURLOPT_POSTFIELDS", "CURLINFO_RESPONSE_CODE"))]) + "\n" +
            "function curl_version(){return ['features'=>CURL_VERSION_HTTP2];}\n" +
            "function curl_init($url){return (object)['url'=>$url];}\n" +
            "function curl_setopt_array($curl,$options){$curl->options=$options;}\n" +
            "function curl_exec($curl){file_put_contents("+json.dumps(str(capture))+",json_encode(['url'=>$curl->url,'headers'=>$curl->options[CURLOPT_HTTPHEADER],'payload'=>json_decode($curl->options[CURLOPT_POSTFIELDS],true)]));return '';}\n" +
            "function curl_getinfo($curl,$option){return 200;} function curl_close($curl){}\n" +
            "$_SERVER=" + "json_decode(base64_decode('"+base64.b64encode(json.dumps({
                "HTTPS":"on", "REQUEST_METHOD":"POST", "CONTENT_TYPE":"application/json", "HTTP_X_FANCONTROL_INSTALLATION":"one",
                "HTTP_X_FANCONTROL_TIMESTAMP":timestamp,"HTTP_X_FANCONTROL_NONCE":nonce,"HTTP_X_FANCONTROL_SIGNATURE":signature}).encode()).decode()+"'),true);\n" +
            "require "+json.dumps(str(entry))+";")
        extension_dir = subprocess.check_output([PHP,"-r","echo ini_get('extension_dir');"],text=True).strip()
        result = subprocess.check_output([PHP,"-n", "-d", "extension="+extension_dir+"/pdo.so",
                                          "-d", "extension="+extension_dir+"/pdo_sqlite.so",str(fake)],text=True)
        self.assertEqual(json.loads(result)["status"],"accepted_by_apns")
        sent = json.loads(capture.read_text())
        self.assertEqual(sent["url"],"https://api.sandbox.push.apple.com/3/device/"+"b"*64)
        self.assertIn("apns-topic: dev.fancontrol.watch",sent["headers"])
        self.assertIn("apns-push-type: alert",sent["headers"])
        self.assertIn("apns-priority: 10",sent["headers"])
        self.assertIn("apns-collapse-id: fc-"+update,sent["headers"])
        self.assertEqual(sent["payload"]["fancontrol"]["watch_id"],"routing")
        self.assertEqual(sent["payload"]["aps"]["alert"]["body"],"Still open")

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
