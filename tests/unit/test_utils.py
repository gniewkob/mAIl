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
