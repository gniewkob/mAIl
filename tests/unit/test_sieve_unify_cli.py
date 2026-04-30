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

    assert 'fileinto "Junk";' in script
    assert 'fileinto "INBOX.Billing";' in script
    assert 'fileinto "INBOX.System";' in script
    assert 'fileinto "INBOX.Newsletter";' in script
    assert 'fileinto "INBOX.AI-Review";' in script


def test_render_logic_is_uniform_across_mailboxes_except_folder_values() -> None:
    mailbox_a = _mailbox(mailbox_id="a", imap_source_folder="INBOX.AI-Review")
    mailbox_b = _mailbox(mailbox_id="b", imap_source_folder="INBOX.Team-AI-Review")

    script_a = _render(mailbox_a, DEFAULT_POLICY)
    script_b = _render(mailbox_b, DEFAULT_POLICY)

    assert "# Unified Sieve policy for mAIl (generated)." in script_a
    assert "# Unified Sieve policy for mAIl (generated)." in script_b
    assert "INBOX.Team-AI-Review" in script_b
    assert "INBOX.Team-AI-Review" not in script_a
