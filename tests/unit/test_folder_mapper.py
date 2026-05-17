import pytest
from pydantic import SecretStr
from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.folder_mapper import category_to_folder, target_folders

@pytest.fixture
def mock_mailbox() -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="test_mailbox",
        imap_host="imap.example.com",
        imap_user="test@example.com",
        imap_pass=SecretStr("secret"),
        imap_source_folder="INBOX.Source",
        imap_uncertain_folder="INBOX.Uncertain",
        imap_appointments_folder="INBOX.Appointments",
        imap_questions_folder="INBOX.Questions",
        imap_complaints_folder="INBOX.Complaints",
        imap_spam_folder="Junk",
        imap_newsletter_folder="INBOX.Newsletter",
        imap_offer_folder="INBOX.Offer",
        imap_other_folder="INBOX.Other",
        imap_billing_folder="INBOX.Billing",
        imap_system_folder="INBOX.System",
    )

def test_category_to_folder_valid_categories(mock_mailbox: MailboxConfig) -> None:
    assert category_to_folder("appointment", mock_mailbox) == "INBOX.Appointments"
    assert category_to_folder("question", mock_mailbox) == "INBOX.Questions"
    assert category_to_folder("complaint", mock_mailbox) == "INBOX.Complaints"
    assert category_to_folder("spam", mock_mailbox) == "Junk"
    assert category_to_folder("newsletter", mock_mailbox) == "INBOX.Newsletter"
    assert category_to_folder("offer", mock_mailbox) == "INBOX.Offer"
    assert category_to_folder("parse_error", mock_mailbox) == "INBOX.Uncertain"
    assert category_to_folder("other", mock_mailbox) == "INBOX.Other"
    assert category_to_folder("billing", mock_mailbox) == "INBOX.Billing"
    assert category_to_folder("system", mock_mailbox) == "INBOX.System"

def test_category_to_folder_unknown_fallback(mock_mailbox: MailboxConfig) -> None:
    # Any unrecognized category should fall back to the source folder
    assert category_to_folder("unknown_category", mock_mailbox) == "INBOX.Source"
    assert category_to_folder("", mock_mailbox) == "INBOX.Source"

def test_target_folders_canonical_order(mock_mailbox: MailboxConfig) -> None:
    expected = [
        "INBOX.Uncertain",
        "INBOX.Appointments",
        "INBOX.Questions",
        "INBOX.Complaints",
        "Junk",
        "INBOX.Newsletter",
        "INBOX.Offer",
        "INBOX.Other",
        "INBOX.Billing",
        "INBOX.System",
    ]
    assert target_folders(mock_mailbox) == expected
