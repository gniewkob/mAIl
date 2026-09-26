from __future__ import annotations

from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.migrate_to_tag_architecture_cli import migrate_mailbox


class FakeIMAPClient:
    def __init__(self, mailbox: MailboxConfig) -> None:
        self.mailbox = mailbox
        self._uids = {
            mailbox.imap_source_folder: ["200"],
            mailbox.imap_questions_folder: ["1", "2"],
            mailbox.imap_offer_folder: ["3"],
        }
        self._msgid = {"1": "<a@x>", "2": "<b@x>", "3": "<c@x>", "200": "<b@x>"}
        self._keywords: dict[str, set[str]] = {}
        self.copied: list[tuple[str, str, str]] = []
        self.tagged: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def __enter__(self) -> "FakeIMAPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_uids(self, folder: str) -> list[str]:
        return list(self._uids.get(folder, []))

    def fetch_message_id_header(self, folder: str, uid: str) -> str | None:
        return self._msgid.get(uid)

    def fetch_message_id_headers(self, folder: str, uids: list[str], *, batch_size: int = 200) -> dict[str, str | None]:
        return {uid: self._msgid.get(uid) for uid in uids}

    def fetch_uid_keywords(self, folder: str, uids: list[str], *, batch_size: int = 200) -> dict[str, set[str]]:
        return {uid: set(self._keywords.get(uid, set())) for uid in uids}

    def message_exists_with_message_id(self, folder: str, message_id: str) -> bool:
        return message_id == "<b@x>"

    def message_exists_with_message_id_and_keyword(self, folder: str, message_id: str, keyword: str) -> bool:
        return False

    def message_exists_with_keyword(self, folder: str, keyword: str) -> bool:
        return False

    def find_uids_by_message_id(self, folder: str, message_id: str) -> list[str]:
        if message_id == "<b@x>":
            return ["200"]
        return []

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str | None:
        self.copied.append((source_folder, uid, target_folder))
        return f"T{uid}"

    def add_keyword(self, folder: str, uid: str, keyword: str) -> str:
        self.tagged.append((folder, uid, keyword))
        self._keywords.setdefault(uid, set()).add(keyword)
        return keyword

    def delete_message(self, folder: str, uid: str) -> None:
        self.deleted.append((folder, uid))


def _mailbox() -> MailboxConfig:
    return MailboxConfig.model_validate(
        {
            "mailbox_id": "m1",
            "imap_host": "imap.example.com",
            "imap_user": "u@example.com",
            "imap_pass": "secret",
            "imap_source_folder": "INBOX",
            "imap_questions_folder": "INBOX.Questions",
            "imap_offer_folder": "INBOX.Offer",
        }
    )


def test_migrate_mailbox_apply_copies_and_tags_with_dedupe(monkeypatch) -> None:
    import mail_ai_agent.migrate_to_tag_architecture_cli as m

    instances: list[FakeIMAPClient] = []

    def _factory(mailbox: MailboxConfig) -> FakeIMAPClient:
        client = FakeIMAPClient(mailbox)
        instances.append(client)
        return client

    monkeypatch.setattr(m, "IMAPClient", _factory)

    stats = migrate_mailbox(
        mailbox=_mailbox(),
        apply=True,
        dedupe_by_message_id=True,
        limit_per_folder=0,
        tag_prefix="$AI_",
        missing_policy="copy",
    )

    assert len(stats) >= 2
    total_scanned = sum(s.scanned for s in stats)
    total_missing = sum(s.missing_in_source for s in stats)
    total_skipped = sum(s.dedup_skipped for s in stats)
    total_copied = sum(s.copied for s in stats)
    total_tagged = sum(s.tagged for s in stats)
    total_missing_tag = sum(s.missing_tag for s in stats)
    total_tagged_existing = sum(s.tagged_existing for s in stats)
    assert total_scanned == 3
    assert total_missing == 2
    assert total_skipped == 1
    assert total_copied == 2
    assert total_tagged == 2
    assert total_missing_tag == 1
    assert total_tagged_existing == 1


def test_migrate_mailbox_delete_policy_prunes_orphans(monkeypatch) -> None:
    import mail_ai_agent.migrate_to_tag_architecture_cli as m

    instances: list[FakeIMAPClient] = []

    def _factory(mailbox: MailboxConfig) -> FakeIMAPClient:
        client = FakeIMAPClient(mailbox)
        instances.append(client)
        return client

    monkeypatch.setattr(m, "IMAPClient", _factory)

    stats = migrate_mailbox(
        mailbox=_mailbox(),
        apply=True,
        dedupe_by_message_id=True,
        limit_per_folder=0,
        tag_prefix="$AI_",
        missing_policy="delete",
    )

    total_pruned = sum(s.pruned for s in stats)
    total_copied = sum(s.copied for s in stats)
    assert total_pruned == 2
    assert total_copied == 0
