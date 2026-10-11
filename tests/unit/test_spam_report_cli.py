from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from mail_ai_agent.spam_report_cli import _sha, is_server_flagged, select_candidates

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _item(uid: str, hours_old: float, mid: str, flag: str | None = None, size: int = 1000):
    headers = f"Message-ID: {mid}\r\nSubject: x\r\n"
    if flag:
        headers += f"X-Spam-Flag: {flag}\r\n"
    return (uid, format_datetime(NOW - timedelta(hours=hours_old)), size, headers.encode() + b"\r\n")


def _select(items, reported=frozenset(), limit=40):
    return select_candidates(list(items), set(reported), now=NOW, min_age_hours=24, max_age_days=8,
                             max_bytes=1_000_000, limit=limit)


def test_server_flagged_spam_is_not_reported() -> None:
    assert is_server_flagged(b"X-Spam-Flag: YES\r\n\r\n")
    assert _select([_item("1", 48, "<a@x>", flag="YES")]) == []


def test_grace_period_protects_fresh_mail() -> None:
    assert _select([_item("1", 3, "<a@x>")]) == []
    assert [u for u, _ in _select([_item("1", 30, "<a@x>")])] == ["1"]


def test_too_old_and_too_big_are_skipped() -> None:
    assert _select([_item("1", 24 * 20, "<a@x>")]) == []
    assert _select([_item("1", 30, "<a@x>", size=5_000_000)]) == []


def test_already_reported_is_skipped_and_limit_applies() -> None:
    items = [_item(str(i), 30 + i, f"<m{i}@x>") for i in range(5)]
    assert _select(items, reported={_sha("<m0@x>")}, limit=2) == [("4", _sha("<m4@x>")), ("3", _sha("<m3@x>"))]


def test_server_flag_no_is_reportable() -> None:
    assert len(_select([_item("1", 30, "<a@x>", flag="NO")])) == 1


def test_trusted_sender_is_never_reported() -> None:
    uid, date, size, headers = _item("1", 30, "<a@x>")
    headers = b"From: Booksy <no-reply@booksy.com>\r\n" + headers
    out = select_candidates([(uid, date, size, headers)], set(), now=NOW, min_age_hours=24, max_age_days=8,
                            max_bytes=1_000_000, limit=40, trusted_domains=("booksy.com",))
    assert out == []


def test_model_tagged_junk_is_never_reported() -> None:
    from mail_ai_agent.spam_report_cli import has_model_tag

    assert has_model_tag('12 (UID 5 FLAGS (\\Seen $AI_spam) INTERNALDATE "x")', "$AI_")
    assert not has_model_tag('12 (UID 5 FLAGS (\\Seen Junk) INTERNALDATE "x")', "$AI_")
    assert not has_model_tag('12 (UID 5 FLAGS () INTERNALDATE "x")', "$AI_")
    assert not has_model_tag('12 (UID 5 FLAGS ($AI_spam) INTERNALDATE "x")', "")
