from __future__ import annotations

from .config import MailboxConfig


def category_to_folder(category: str, mailbox: MailboxConfig) -> str:
    mapping = {
        "appointment": mailbox.imap_appointments_folder,
        "question": mailbox.imap_questions_folder,
        "complaint": mailbox.imap_complaints_folder,
        "spam": mailbox.imap_spam_folder,
        "newsletter": mailbox.imap_newsletter_folder,
        "offer": mailbox.imap_offer_folder,
        "parse_error": mailbox.imap_uncertain_folder,
        "other": mailbox.imap_other_folder,
        "billing": mailbox.imap_billing_folder,
        "system": mailbox.imap_system_folder,
    }
    return mapping.get(category, mailbox.imap_source_folder)


def target_folders(mailbox: MailboxConfig) -> list[str]:
    """Return all target routing folders for a mailbox in canonical order."""
    return [
        mailbox.imap_uncertain_folder,
        mailbox.imap_appointments_folder,
        mailbox.imap_questions_folder,
        mailbox.imap_complaints_folder,
        mailbox.imap_spam_folder,
        mailbox.imap_newsletter_folder,
        mailbox.imap_offer_folder,
        mailbox.imap_other_folder,
        mailbox.imap_billing_folder,
        mailbox.imap_system_folder,
    ]
