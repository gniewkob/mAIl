from __future__ import annotations

import imaplib
import time
from contextlib import AbstractContextManager
from typing import Callable, TypeVar

from .config import MailboxConfig
from .schemas import CandidateMessage

T = TypeVar("T")


class IMAPClient(AbstractContextManager["IMAPClient"]):
    def __init__(self, mailbox: MailboxConfig) -> None:
        self.mailbox = mailbox
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "IMAPClient":
        self._connect_and_login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except imaplib.IMAP4.error:
                pass

    def _connect_and_login(self) -> None:
        self.connection = imaplib.IMAP4_SSL(self.mailbox.imap_host, self.mailbox.imap_port)
        self.connection.login(self.mailbox.imap_user, self.mailbox.imap_pass.get_secret_value())

    def _reconnect(self) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        self._connect_and_login()

    def _run_with_retry(self, operation_name: str, func: Callable[[], T]) -> T:
        last_error: Exception | None = None
        attempts = max(1, self.mailbox.imap_max_retries)
        for attempt in range(1, attempts + 1):
            try:
                return func()
            except (imaplib.IMAP4.abort, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                self._reconnect()
                time.sleep(self.mailbox.imap_retry_backoff_seconds * attempt)
        raise RuntimeError(f"IMAP operation '{operation_name}' failed after retries: {last_error}") from last_error

    def _get_uidvalidity(self) -> str | None:
        assert self.connection is not None
        response = self.connection.response("UIDVALIDITY")
        if not response or len(response) < 2:
            return None
        payload = response[1]
        if not payload:
            return None
        value = payload[0]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        if isinstance(value, str):
            return value
        return None

    def get_uidvalidity(self, folder: str) -> str | None:
        def _get() -> str | None:
            assert self.connection is not None
            status, _ = self.connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            return self._get_uidvalidity()

        return self._run_with_retry("get_uidvalidity", _get)

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        def _fetch() -> list[CandidateMessage]:
            assert self.connection is not None
            status, _ = self.connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            uidvalidity = self._get_uidvalidity()
            # The criterion is validated in config.py against a safe token whitelist.
            search_tokens = self.mailbox.imap_search_criterion.split()
            status, data = self.connection.uid("search", None, *search_tokens)
            if status != "OK":
                raise RuntimeError("Unable to search folder")

            messages: list[CandidateMessage] = []
            uids = data[0].split()
            if self.mailbox.imap_fetch_limit > 0:
                uids = uids[-self.mailbox.imap_fetch_limit :]
            for uid in uids:
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
                messages.append(
                    CandidateMessage(
                        uid=uid.decode(),
                        uidvalidity=uidvalidity,
                        internaldate=internaldate,
                        raw_bytes=raw_bytes,
                    )
                )
            return messages

        return self._run_with_retry("fetch_candidates", _fetch)

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> None:
        def _copy() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(source_folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select source folder {source_folder}")
            status, _ = self.connection.uid("copy", uid, target_folder)
            if status != "OK":
                raise RuntimeError(f"Unable to copy message {uid} to {target_folder}")

        self._run_with_retry("copy_message", _copy)

    def set_flagged(self, folder: str, uid: str) -> None:
        def _flag() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            status, _ = self.connection.uid("store", uid, "+FLAGS.SILENT", "(\\Flagged)")
            if status != "OK":
                raise RuntimeError(f"Unable to flag message {uid}")

        self._run_with_retry("set_flagged", _flag)

    def mark_deleted(self, folder: str, uid: str) -> None:
        def _mark_deleted() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            status, _ = self.connection.uid("store", uid, "+FLAGS.SILENT", "(\\Deleted)")
            if status != "OK":
                raise RuntimeError(f"Unable to mark message {uid} as deleted")

        self._run_with_retry("mark_deleted", _mark_deleted)

    def expunge(self, folder: str) -> None:
        def _expunge() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            status, _ = self.connection.expunge()
            if status != "OK":
                raise RuntimeError(f"Unable to expunge folder {folder}")

        self._run_with_retry("expunge", _expunge)
