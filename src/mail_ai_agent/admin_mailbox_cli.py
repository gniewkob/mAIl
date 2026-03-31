from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .audit_logger import AuditLogger
from .config import MailboxConfig, Settings
from .imap_client import IMAPClient
from .schemas import WorkflowStatus
from .state_manager import StateManager


def _load_settings(env_file: str | None) -> Settings:
    return Settings(_env_file=env_file) if env_file else Settings()  # type: ignore[call-arg]


def _mailbox_by_id(settings: Settings, mailbox_id: str) -> MailboxConfig:
    for mailbox in settings.load_mailboxes():
        if mailbox.mailbox_id == mailbox_id:
            return mailbox
    raise ValueError(f"Unknown mailbox_id: {mailbox_id}")


def _parse_record_ids(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return None
    return [int(value) for value in values]


def run_requeue_uncertain(
    *,
    settings: Settings,
    mailbox_id: str | None = None,
    record_ids: list[int] | None = None,
) -> dict[str, Any]:
    state = StateManager(settings.state_db_path)
    audit = AuditLogger(settings.audit_log_path, redact_pii=settings.audit_redact_pii, fsync=settings.audit_fsync)
    records = state.list_by_status(status=WorkflowStatus.UNCERTAIN, mailbox_id=mailbox_id)
    if record_ids is not None:
        record_id_set = set(record_ids)
        records = [record for record in records if record.id in record_id_set]

    payload: dict[str, Any] = {
        "selected": len(records),
        "requeued": 0,
        "failed": 0,
        "results": [],
    }
    if not records:
        return payload

    grouped: dict[str, list[Any]] = {}
    for record in records:
        grouped.setdefault(record.mailbox_id, []).append(record)

    for current_mailbox_id, mailbox_records in grouped.items():
        mailbox = _mailbox_by_id(settings, current_mailbox_id)
        with IMAPClient(mailbox) as imap:
            for record in mailbox_records:
                result: dict[str, Any] = {
                    "record_id": record.id,
                    "mailbox_id": record.mailbox_id,
                    "target_folder": record.target_folder,
                    "target_uid": record.target_uid,
                    "source_folder": record.source_folder,
                    "status": "pending",
                    "error": None,
                }
                payload["results"].append(result)
                if not record.target_folder or not record.target_uid or not record.source_folder:
                    result["status"] = "failed"
                    result["error"] = "uncertain record missing target/source IMAP coordinates"
                    payload["failed"] += 1
                    continue
                try:
                    new_source_uid = imap.copy_message(record.target_folder, record.target_uid, record.source_folder)
                    imap.delete_message(record.target_folder, record.target_uid)
                    state.delete_record(record.id)
                    audit.log(
                        level="INFO",
                        mailbox_id=record.mailbox_id,
                        mailbox_user=mailbox.imap_user,
                        source_folder=record.target_folder,
                        message_id=record.message_id,
                        fingerprint=record.fingerprint,
                        imap_uid=record.target_uid,
                        sender=record.sender,
                        subject=record.subject,
                        status_before=WorkflowStatus.UNCERTAIN.value,
                        status_after="requeued_for_processing",
                        category=record.category,
                        confidence=record.confidence,
                        action_taken="admin_requeue_uncertain",
                        target_folder=record.source_folder,
                        target_uid=new_source_uid,
                        error=None,
                        dry_run=False,
                    )
                    result["status"] = "requeued"
                    result["new_source_uid"] = new_source_uid
                    payload["requeued"] += 1
                except Exception as exc:
                    result["status"] = "failed"
                    result["error"] = str(exc)
                    payload["failed"] += 1
    return payload


def run_delete_imap_message(
    *,
    settings: Settings,
    mailbox_id: str,
    folder: str,
    uid: str,
) -> dict[str, Any]:
    mailbox = _mailbox_by_id(settings, mailbox_id)
    audit = AuditLogger(settings.audit_log_path, redact_pii=settings.audit_redact_pii, fsync=settings.audit_fsync)
    with IMAPClient(mailbox) as imap:
        imap.delete_message(folder, uid)
    audit.log(
        level="INFO",
        mailbox_id=mailbox.mailbox_id,
        mailbox_user=mailbox.imap_user,
        source_folder=folder,
        message_id=None,
        fingerprint=None,
        imap_uid=uid,
        sender=None,
        subject=None,
        status_before=None,
        status_after="deleted_by_admin",
        category=None,
        confidence=None,
        action_taken="admin_delete_message",
        target_folder=None,
        target_uid=None,
        error=None,
        dry_run=False,
    )
    return {
        "mailbox_id": mailbox_id,
        "folder": folder,
        "uid": uid,
        "deleted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Administrative mailbox operations for mAIl")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    requeue_parser = subparsers.add_parser("requeue-uncertain", help="Move uncertain messages back to AI-Review")
    requeue_parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id filter")
    requeue_parser.add_argument("--record-ids", default=None, help="Optional comma-separated state record ids")

    delete_parser = subparsers.add_parser("delete-imap-message", help="Delete one IMAP UID from a mailbox folder")
    delete_parser.add_argument("--mailbox-id", required=True)
    delete_parser.add_argument("--folder", required=True)
    delete_parser.add_argument("--uid", required=True)

    args = parser.parse_args()
    settings = _load_settings(args.env_file)

    if args.command == "requeue-uncertain":
        payload = run_requeue_uncertain(
            settings=settings,
            mailbox_id=args.mailbox_id,
            record_ids=_parse_record_ids(args.record_ids),
        )
    else:
        payload = run_delete_imap_message(
            settings=settings,
            mailbox_id=args.mailbox_id,
            folder=args.folder,
            uid=args.uid,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
