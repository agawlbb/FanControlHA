import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import pytest
spec=importlib.util.spec_from_file_location("relay_deploy",Path(__file__).parents[1]/"scripts/deploy_relay.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class FakeClient:
    def __init__(self, corrupt=False): self.files={(module.PUBLIC_DIR,"index.php"):b"old relay"};self.writes=[];self.corrupt=corrupt
    def read(self,directory,name):
        if self.corrupt and directory == module.PUBLIC_DIR and len(self.writes)>1: return b"bad readback"
        return self.files[(directory,name)]
    def upload(self,directory,name,content): self.files[(directory,name)]=content;self.writes.append((directory,name,content))

def responses(): return [io.BytesIO(b"<?php function outcome_payload() {}"),io.BytesIO(json.dumps({"service":"FanControl push relay","status":"available"}).encode())]

def test_pinned_public_only_deployment_preserves_private_state():
    client=FakeClient()
    with patch.object(module.urllib.request,"urlopen",side_effect=responses()):
        result=module.deploy("a"*40,client)
    assert len(client.writes)==2
    assert client.writes[0][0]==module.PRIVATE_DIR and client.writes[0][1].startswith("index-before-outcomes-")
    assert client.writes[1][:2]==(module.PUBLIC_DIR,"index.php")
    assert result["private_config_key_database_written"] is False

def test_readback_mismatch_restores_previous_entry():
    client=FakeClient(corrupt=True)
    with patch.object(module.urllib.request,"urlopen",return_value=responses()[0]):
        with pytest.raises(RuntimeError,match="previous source restored"): module.deploy("a"*40,client)
    assert client.writes[-1]==(module.PUBLIC_DIR,"index.php",b"old relay")

def test_arbitrary_source_and_paths_fail_closed():
    with pytest.raises(ValueError): module.deploy("main",FakeClient())
    client=object.__new__(module.Client)
    for directory,name in [(module.PRIVATE_DIR,"config.php"),("/home/negaau5/coffeybean.com","index.php"),(module.PUBLIC_DIR,".env")]:
        with pytest.raises(ValueError): client.upload(directory,name,b"bad")

def test_discovery_failure_restores_previous_entry():
    client=FakeClient()
    with patch.object(module.urllib.request,"urlopen",side_effect=[responses()[0],OSError("unreachable")]):
        with pytest.raises(RuntimeError,match="Discovery check failed"): module.deploy("a"*40,client)
    assert client.writes[-1]==(module.PUBLIC_DIR,"index.php",b"old relay")
