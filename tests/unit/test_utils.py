import os
import stat
from pathlib import Path

from mail_ai_agent.utils import _hash_value, _chmod_owner_only


def test_hash_value_none():
    assert _hash_value(None) is None

def test_hash_value_empty():
    assert _hash_value("") is None

def test_hash_value_str():
    result = _hash_value("hello")
    assert result is not None
    assert len(result) == 64
    assert result == _hash_value("hello")

def test_hash_value_different():
    assert _hash_value("a") != _hash_value("b")


def test_chmod_owner_only_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    from mail_ai_agent.utils import _chmod_owner_only
    _chmod_owner_only(f)
    mode = stat.S_IMODE(os.stat(f).st_mode)
    assert mode == 0o600


def test_chmod_owner_only_dir(tmp_path):
    from mail_ai_agent.utils import _chmod_owner_only
    _chmod_owner_only(tmp_path)
    mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
    assert mode == 0o700
