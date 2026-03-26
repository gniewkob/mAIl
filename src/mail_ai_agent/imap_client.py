from __future__ import annotations

import imaplib
from contextlib import AbstractContextManager

from .config import MailboxConfig
from .schemas import CandidateMessage


class IMAPClient(AbstractContextManager["IMAPClient"]):
    def __init__(self, mailbox: MailboxConfig) -> None:
        self.mailbox = mailbox
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "IMAPClient":
        self.connection = imaplib.IMAP4_SSL(self.mailbox.imap_host, self.mailbox.imap_port)
        self.connection.login(self.mailbox.imap_user, self.mailbox.imap_pass.get_secret_value())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except imaplib.IMAP4.error:
                pass

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        assert self.connection is not None
        status, _ = self.connection.select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}")
        status, data = self.connection.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Unable to search folder")

        messages: list[CandidateMessage] = []
        for uid in data[0].split():
            status, fetched = self.connection.uid("fetch", uid, "(BODY.PEEK[] INTERNALDATE RFC822.HEADER)")
            if status != "OK":
                continue
            raw_bytes = b""
            internaldate = None
            for item in fetched:
                if isinstance(item, tuple):
                    raw_bytes = item[1]
                    metadata = item[0].decode("utf-8", errors="ignore")
                    if 'INTERNALDATE "' in metadata:
                        internaldate = metadata.split('INTERNALDATE "', 1)[1].split('"', 1)[0]
            messages.append(CandidateMessage(uid=uid.decode(), internaldate=internaldate, raw_bytes=raw_bytes))
        return messages

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        assert self.connection is not None
        status, _ = self.connection.select(source_folder)
        if status != "OK":
            raise RuntimeError(f"Unable to select source folder {source_folder}")
        status, _ = self.connection.uid("copy", uid, target_folder)
        if status != "OK":
            raise RuntimeError(f"Unable to copy message {uid} to {target_folder}")

    def set_flagged(self, folder: str, uid: str) -> None:
        assert self.connection is not None
        status, _ = self.connection.select(folder)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}")
        status, _ = self.connection.uid("store", uid, "+FLAGS.SILENT", "(\\Flagged)")
        if status != "OK":
            raise RuntimeError(f"Unable to flag message {uid}")

    def mark_deleted(self, folder: str, uid: str) -> None:
        assert self.connection is not None
        status, _ = self.connection.select(folder)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}")
        status, _ = self.connection.uid("store", uid, "+FLAGS.SILENT", "(\\Deleted)")
        if status != "OK":
            raise RuntimeError(f"Unable to mark message {uid} as deleted")

    def expunge(self, folder: str) -> None:
        assert self.connection is not None
        status, _ = self.connection.select(folder)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}")
        status, _ = self.connection.expunge()
        if status != "OK":
            raise RuntimeError(f"Unable to expunge folder {folder}")
