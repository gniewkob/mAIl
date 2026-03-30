from __future__ import annotations

import imaplib
import ssl
import re as _re
import warnings
import time
from contextlib import AbstractContextManager
from typing import Callable, Generator, TypeVar

from .config import MailboxConfig
from .schemas import CandidateMessage

T = TypeVar("T")

_UID_RE = _re.compile(r"\bUID\s+(\d+)\b", _re.IGNORECASE)
_INTERNALDATE_RE = _re.compile(r'INTERNALDATE\s+"([^"]+)"', _re.IGNORECASE)

_AUTH_FAILURE_KEYWORDS = (
    "AUTHENTICATIONFAILED",
    "LOGIN FAILED",
    "NO LOGIN",
    "INVALID CREDENTIALS",
    "AUTHENTICATION FAILED",
    "AUTHORIZATIONFAILED",
    "[AUTHORIZATIONFAILED]",
)


class IMAPAuthError(RuntimeError):
    """Raised when IMAP login is rejected due to authentication failure."""


class IMAPClient(AbstractContextManager["IMAPClient"]):
    def __init__(self, mailbox: MailboxConfig) -> None:
        self.mailbox = mailbox
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "IMAPClient":
        self._connect_and_login()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except imaplib.IMAP4.error:
                pass

    def _connect_and_login(self) -> None:
        self.connection = imaplib.IMAP4_SSL(
            self.mailbox.imap_host,
            self.mailbox.imap_port,
            ssl_context=ssl.create_default_context(),
        )
        try:
            self.connection.login(self.mailbox.imap_user, self.mailbox.imap_pass.get_secret_value())
        except imaplib.IMAP4.error as exc:
            msg = str(exc).upper()
            if any(keyword in msg for keyword in _AUTH_FAILURE_KEYWORDS):
                raise IMAPAuthError(
                    f"IMAP authentication failed for {self.mailbox.imap_user}: {exc}"
                ) from exc
            raise

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

    def ensure_folder_access(self, folder: str, *, readonly: bool) -> None:
        def _ensure() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(folder, readonly=readonly)
            if status != "OK":
                mode = "read-only" if readonly else "read-write"
                raise RuntimeError(f"Unable to select folder {folder} in {mode} mode")

        self._run_with_retry("ensure_folder_access", _ensure)

    def supports_uidplus(self) -> bool:
        assert self.connection is not None
        capabilities = getattr(self.connection, "capabilities", None)
        if not capabilities and hasattr(self.connection, "capability"):
            status, data = self.connection.capability()
            if status == "OK" and data:
                capabilities = set(b" ".join(data).split())
        if not capabilities:
            return False
        normalized = {
            capability.decode("utf-8", errors="ignore").upper() if isinstance(capability, bytes) else str(capability).upper()
            for capability in capabilities
        }
        return "UIDPLUS" in normalized

    def validate_routing_setup(self, *, source_folder: str, target_folders: list[str], dry_run: bool) -> None:
        self.ensure_folder_access(source_folder, readonly=dry_run)
        unique_targets = []
        for folder in target_folders:
            if folder not in unique_targets:
                unique_targets.append(folder)
        for folder in unique_targets:
            self.ensure_folder_access(folder, readonly=False)
        if not dry_run and not (self.supports_uidplus() or self.mailbox.imap_allow_folder_expunge):
            raise RuntimeError(
                "IMAP server does not advertise UIDPLUS and folder-level expunge is disabled. "
                "Enable IMAP_ALLOW_FOLDER_EXPUNGE only if the source folder is exclusively owned by this worker."
            )

    def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
        def _fetch() -> list[CandidateMessage]:
            assert self.connection is not None
            status, _ = self.connection.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            uidvalidity = self._get_uidvalidity()
            # The criterion is validated in config.py against a safe token whitelist.
            search_tokens = self.mailbox.imap_search_criterion.split()
            status, data = self.connection.uid("search", None, *search_tokens)  # type: ignore[arg-type]
            if status != "OK":
                raise RuntimeError("Unable to search folder")
            raw_uids = data[0] if data and data[0] is not None else b""
            all_uids = [uid for uid in raw_uids.split() if uid.isdigit()]
            if self.mailbox.imap_fetch_limit == 0 and len(all_uids) > 500:
                warnings.warn(
                    f"imap_fetch_limit=0 with {len(all_uids)} UIDs — consider setting a limit to avoid memory/bandwidth issues",
                    RuntimeWarning,
                    stacklevel=2,
                )
            if self.mailbox.imap_fetch_limit > 0:
                all_uids = all_uids[-self.mailbox.imap_fetch_limit:]
            if not all_uids:
                return []
            uid_set = b",".join(all_uids).decode()
            status, fetched = self.connection.uid(
                "fetch", uid_set, "(UID BODY.PEEK[] INTERNALDATE)"
            )
            if status != "OK":
                raise RuntimeError("Unable to batch-fetch messages")
            return list(_parse_batch_fetch_response(fetched, uidvalidity))

        return self._run_with_retry("fetch_candidates", _fetch)

    def copy_message(self, source_folder: str, uid: str, target_folder: str) -> str | None:
        def _copy() -> str | None:
            assert self.connection is not None
            status, _ = self.connection.select(source_folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select source folder {source_folder}")
            status, _ = self.connection.uid("copy", uid, target_folder)
            if status != "OK":
                raise RuntimeError(f"Unable to copy message {uid} to {target_folder}")
            return self._extract_copyuid(uid)

        return self._run_with_retry("copy_message", _copy)

    def _extract_copyuid(self, source_uid: str) -> str | None:
        assert self.connection is not None
        response = self.connection.response("COPYUID")
        if not response or len(response) < 2 or not response[1]:
            return None
        payload = response[1][0]
        raw = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else str(payload)
        parts = raw.strip().split()
        if len(parts) < 3:
            return None
        source_set = parts[1]
        target_set = parts[2]
        if "," in source_set or ":" in source_set or "," in target_set or ":" in target_set:
            return None
        if source_set != source_uid:
            return None
        return target_set

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

    def delete_message(self, folder: str, uid: str) -> None:
        def _uid_expunge() -> None:
            assert self.connection is not None
            status, _ = self.connection.select(folder)
            if status != "OK":
                raise RuntimeError(f"Unable to select folder {folder}")
            pre_existing_deleted = self._search_deleted_uids()
            if pre_existing_deleted and uid not in pre_existing_deleted:
                raise RuntimeError(
                    f"Refusing folder-level expunge in {folder}: other deleted messages already exist ({', '.join(pre_existing_deleted)})"
                )
            status, _ = self.connection.uid("store", uid, "+FLAGS.SILENT", "(\\Deleted)")
            if status != "OK":
                raise RuntimeError(f"Unable to mark message {uid} as deleted")
            if self.supports_uidplus():
                status, _ = self.connection.uid("expunge", uid)
                if status != "OK":
                    raise RuntimeError(f"Unable to UID EXPUNGE message {uid} in {folder}")
                return
            if not self.mailbox.imap_allow_folder_expunge:
                raise RuntimeError(
                    f"Server for {self.mailbox.imap_user} does not support UIDPLUS and folder-level expunge is disabled"
                )
            deleted_uids = self._search_deleted_uids()
            if set(deleted_uids) != {uid}:
                self._clear_deleted_flag(uid)
                raise RuntimeError(
                    f"Refusing folder-level expunge in {folder}: deleted set is {deleted_uids}, expected only [{uid}]"
                )
            status, _ = self.connection.expunge()
            if status != "OK":
                raise RuntimeError(f"Unable to expunge folder {folder}")

        self._run_with_retry("delete_message", _uid_expunge)

    def _search_deleted_uids(self) -> list[str]:
        assert self.connection is not None
        status, data = self.connection.uid("search", None, "DELETED")  # type: ignore[arg-type]
        if status != "OK" or not data:
            raise RuntimeError("Unable to search deleted messages")
        deleted = data[0]
        if not deleted:
            return []
        return [item.decode("utf-8", errors="ignore") for item in deleted.split()]

    def _clear_deleted_flag(self, uid: str) -> None:
        assert self.connection is not None
        status, _ = self.connection.uid("store", uid, "-FLAGS.SILENT", "(\\Deleted)")
        if status != "OK":
            raise RuntimeError(f"Unable to clear deleted flag for message {uid}")


def _parse_batch_fetch_response(
    fetched: list[object],
    uidvalidity: str | None,
) -> Generator["CandidateMessage", None, None]:
    for item in fetched:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        metadata_raw = item[0]
        raw_bytes = item[1]
        if not isinstance(raw_bytes, bytes):
            continue
        metadata = (
            metadata_raw.decode("utf-8", errors="ignore")
            if isinstance(metadata_raw, bytes)
            else str(metadata_raw)
        )
        uid_match = _UID_RE.search(metadata)
        if not uid_match:
            continue
        uid = uid_match.group(1)
        internaldate: str | None = None
        date_match = _INTERNALDATE_RE.search(metadata)
        if date_match:
            internaldate = date_match.group(1)
        yield CandidateMessage(
            uid=uid,
            uidvalidity=uidvalidity,
            internaldate=internaldate,
            raw_bytes=raw_bytes,
        )
