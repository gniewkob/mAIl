from __future__ import annotations

from pydantic import SecretStr

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.sieve_unify_cli import DEFAULT_POLICY, _render


def _mailbox(**kwargs) -> MailboxConfig:
    base = dict(
        mailbox_id="mbx",
        imap_host="imap.example.com",
        imap_user="user@example.com",
        imap_pass=SecretStr("secret"),
        imap_source_folder="INBOX.AI-Review",
        imap_spam_folder="Junk",
        imap_newsletter_folder="INBOX.Newsletter",
        imap_billing_folder="INBOX.Billing",
        imap_system_folder="INBOX.System",
    )
    base.update(kwargs)
    return MailboxConfig(**base)


def test_render_contains_mailbox_folders() -> None:
    mailbox = _mailbox()
    script = _render(mailbox, DEFAULT_POLICY)

    assert 'fileinto :copy "Junk";' in script
    assert 'fileinto :copy "INBOX.Billing";' in script
    assert 'fileinto :copy "INBOX.System";' in script
    assert 'fileinto :copy "INBOX.Newsletter";' in script
    assert 'fileinto "INBOX.AI-Review";' in script


def test_render_uses_keep_when_source_folder_is_inbox() -> None:
    mailbox = _mailbox(imap_source_folder="INBOX")
    script = _render(mailbox, DEFAULT_POLICY)

    assert "keep;" in script
    assert 'fileinto "INBOX";' not in script


def test_render_logic_is_uniform_across_mailboxes_except_folder_values() -> None:
    mailbox_a = _mailbox(mailbox_id="a", imap_source_folder="INBOX.AI-Review")
    mailbox_b = _mailbox(mailbox_id="b", imap_source_folder="INBOX.Team-AI-Review")

    script_a = _render(mailbox_a, DEFAULT_POLICY)
    script_b = _render(mailbox_b, DEFAULT_POLICY)

    assert "# Unified Sieve policy for mAIl (generated)." in script_a
    assert "# Unified Sieve policy for mAIl (generated)." in script_b
    assert "INBOX.Team-AI-Review" in script_b
    assert "INBOX.Team-AI-Review" not in script_a


# --- single-copy (hybrid/move) mailboxes: server is a spam gate only (2026-09-26) ---------------


def test_render_hybrid_is_spam_gate_without_copy() -> None:
    script = _render(_mailbox(imap_source_folder="INBOX", routing_strategy="hybrid"), DEFAULT_POLICY)
    assert ":copy" not in script
    assert 'fileinto "Junk";' in script
    assert "INBOX.Billing" not in script
    assert "INBOX.System" not in script
    assert "INBOX.Newsletter" not in script
    assert script.rstrip().endswith("keep;")


def test_render_explicit_strategy_overrides_mailbox_default() -> None:
    script = _render(_mailbox(imap_source_folder="INBOX"), DEFAULT_POLICY, "hybrid")
    assert ":copy" not in script


def test_render_copy_plus_tag_keeps_legacy_prefilter() -> None:
    script = _render(_mailbox(imap_source_folder="INBOX", routing_strategy="copy_plus_tag"), DEFAULT_POLICY)
    assert 'fileinto :copy "INBOX.Billing";' in script


def test_trusted_domains_bypass_spam_gate_first() -> None:
    from mail_ai_agent.sieve_unify_cli import trusted_domains_from
    domains = trusted_domains_from("booksy, versum.pl")
    assert domains == ("booksy.com", "versum.pl")
    script = _render(_mailbox(imap_source_folder="INBOX", routing_strategy="hybrid"), DEFAULT_POLICY, None, domains)
    assert script.index('"booksy.com"') < script.index('fileinto "Junk"')
    assert ":copy" not in script
