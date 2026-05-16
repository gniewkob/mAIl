import os
import json
import stat
from pathlib import Path
from unittest.mock import patch, Mock
from mail_ai_agent.manifest_secrets_cli import _write_manifest
from mail_ai_agent.utils import _secure_write_text, _secure_open

def test_write_manifest_secure_order(tmp_path):
    path = tmp_path / "secure_secret.json"
    payload = {"key": "secret_value"}

    # With the fix, os.open should be called with 0o600
    with patch("os.open", wraps=os.open) as mock_open:
        _write_manifest(path, payload)

        # Find the call to os.open for our path
        relevant_calls = [call for call in mock_open.call_args_list if str(call.args[0]) == str(path)]
        assert len(relevant_calls) > 0
        # The mode is the third argument
        assert relevant_calls[0].args[2] == 0o600

def test_secure_write_text_permissions(tmp_path):
    path = tmp_path / "perm_test.txt"
    _secure_write_text(path, "content")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600

def test_secure_open_permissions(tmp_path):
    path = tmp_path / "open_test.txt"
    with _secure_open(path, "w") as f:
        f.write("content")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600

def test_secure_open_append_permissions(tmp_path):
    path = tmp_path / "append_test.txt"
    # First create it
    with _secure_open(path, "w") as f:
        f.write("first\n")

    # Then append
    with _secure_open(path, "a") as f:
        f.write("second\n")

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    assert path.read_text() == "first\nsecond\n"
