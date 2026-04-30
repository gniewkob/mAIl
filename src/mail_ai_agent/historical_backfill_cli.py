from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .config import MailboxConfig, Settings
from .decision_engine import decide_from_llm, decide_from_rule
from .email_parser import compute_content_fingerprint, compute_message_fingerprint, parse_email
from .folder_mapper import target_folders
from .imap_client import IMAPClient
from .llm_gateway import LLMGateway
from .rule_engine import evaluate_rules
from .state_manager import StateManager

_COMMON_EXCLUDED_FOLDERS = {
    "Drafts",
    "INBOX.Drafts",
    "INBOX.Junk",
    "INBOX.Sent",
    "INBOX.Sent Items",
    "INBOX.Spam",
    "INBOX.Trash",
    "Junk",
    "Sent",
    "Sent Items",
    "Spam",
    "Trash",
}


def _normalize_requested_folders(raw_value: str | None) -> list[str] | None:
    if raw_value is None:
        return None
    folders = [item.strip() for item in raw_value.split(",") if item.strip()]
    return folders or None


def _excluded_folders(mailbox: MailboxConfig) -> set[str]:
    return set(target_folders(mailbox)) | {mailbox.imap_source_folder} | _COMMON_EXCLUDED_FOLDERS


def _select_folders(
    *,
    discovered: list[str],
    mailbox: MailboxConfig,
    requested_folders: list[str] | None,
) -> tuple[list[str], list[str]]:
    if requested_folders is not None:
        discovered_set = set(discovered)
        selected = [folder for folder in requested_folders if folder in discovered_set]
        missing = [folder for folder in requested_folders if folder not in discovered_set]
        return selected, missing
    excluded = _excluded_folders(mailbox)
    selected = [folder for folder in discovered if folder not in excluded]
    return selected, []


def _write_csv(rows: list[dict[str, Any]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "mailbox_id",
        "mailbox_user",
        "source_folder",
        "uid",
        "message_id",
        "sender",
        "subject",
        "decision_source",
        "category",
        "target_folder",
        "final_status",
        "action_taken",
        "confidence",
        "requires_reply",
        "summary",
        "reasoning_short",
        "model_latency_ms",
        "status",
        "error",
    ]
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def run_historical_backfill(
    *,
    settings: Settings,
    apply: bool,
    keep_source: bool = False,
    force_reprocess: bool = False,
    mode: str = "stage",
    mailbox_id: str | None = None,
    requested_folders: list[str] | None = None,
    export_csv: Path | None = None,
    fetch_limit: int | None = None,
) -> dict[str, Any]:
    mailboxes = settings.load_mailboxes()
    if mailbox_id:
        mailboxes = [mailbox for mailbox in mailboxes if mailbox.mailbox_id == mailbox_id]
    if not mailboxes:
        raise ValueError("No mailboxes selected for historical backfill.")

    llm = LLMGateway(settings)
    state = StateManager(settings.state_db_path) if force_reprocess else None
    payload: dict[str, Any] = {
        "apply": apply,
        "apply_mode": "copy" if keep_source else "move",
        "force_reprocess": force_reprocess,
        "mailboxes_processed": 0,
        "folders_selected": 0,
        "candidates_seen": 0,
        "planned": 0,
        "applied": 0,
        "failed": 0,
        "state_records_reset": 0,
        "mailboxes": [],
    }
    export_rows: list[dict[str, Any]] = []

    for original_mailbox in mailboxes:
        mailbox = (
            original_mailbox.model_copy(update={"imap_fetch_limit": fetch_limit})
            if fetch_limit is not None
            else original_mailbox
        )
        mailbox_payload: dict[str, Any] = {
            "mailbox_id": mailbox.mailbox_id,
            "mailbox_user": mailbox.imap_user,
            "folders_discovered": [],
            "folders_selected": [],
            "missing_requested_folders": [],
            "candidates_seen": 0,
            "planned": 0,
            "applied": 0,
            "failed": 0,
            "state_records_reset": 0,
            "results": [],
        }
        with IMAPClient(mailbox) as imap:
            discovered = imap.list_folders()
            selected, missing = _select_folders(
                discovered=discovered,
                mailbox=mailbox,
                requested_folders=requested_folders,
            )
            mailbox_payload["folders_discovered"] = discovered
            mailbox_payload["folders_selected"] = selected
            mailbox_payload["missing_requested_folders"] = missing

            for folder in selected:
                try:
                    imap.validate_runtime_setup(
                        source_folder=folder,
                        target_folders=target_folders(mailbox),
                        dry_run=(not apply) or keep_source,
                    )
                except Exception as exc:
                    row = {
                        "mailbox_id": mailbox.mailbox_id,
                        "mailbox_user": mailbox.imap_user,
                        "source_folder": folder,
                        "uid": None,
                        "message_id": None,
                        "sender": None,
                        "subject": None,
                        "decision_source": None,
                        "category": None,
                        "target_folder": None,
                        "final_status": None,
                        "action_taken": "folder_validation_failed",
                        "confidence": None,
                        "requires_reply": None,
                        "summary": None,
                        "reasoning_short": None,
                        "model_latency_ms": None,
                        "status": "failed",
                        "error": str(exc),
                    }
                    mailbox_payload["results"].append(row)
                    export_rows.append(row)
                    mailbox_payload["failed"] += 1
                    payload["failed"] += 1
                    continue

                candidates = imap.fetch_candidates(folder)
                mailbox_payload["candidates_seen"] += len(candidates)
                payload["candidates_seen"] += len(candidates)

                for candidate in candidates:
                    row: dict[str, Any] = {
                        "mailbox_id": mailbox.mailbox_id,
                        "mailbox_user": mailbox.imap_user,
                        "source_folder": folder,
                        "uid": candidate.uid,
                        "message_id": candidate.message_id,
                        "sender": None,
                        "subject": None,
                        "decision_source": None,
                        "category": None,
                        "target_folder": None,
                        "final_status": None,
                        "action_taken": None,
                        "confidence": None,
                        "requires_reply": None,
                        "summary": None,
                        "reasoning_short": None,
                        "model_latency_ms": None,
                        "status": "planned",
                        "error": None,
                        "state_records_reset": 0,
                    }
                    try:
                        parsed = parse_email(candidate.raw_bytes, settings)
                        fingerprint = compute_message_fingerprint(parsed)
                        content_fingerprint = compute_content_fingerprint(parsed)
                    except Exception as exc:
                        row["status"] = "failed"
                        row["action_taken"] = "parse_failed"
                        row["error"] = str(exc)
                        mailbox_payload["failed"] += 1
                        payload["failed"] += 1
                        mailbox_payload["results"].append(row)
                        export_rows.append(row)
                        continue

                    row["message_id"] = parsed.message_id
                    row["sender"] = parsed.sender
                    row["subject"] = parsed.subject

                    if mode == "stage":
                        row["decision_source"] = "sieve_stage"
                        row["target_folder"] = mailbox.imap_source_folder
                        row["action_taken"] = "stage_for_worker"
                        row["final_status"] = "staged"
                    else:
                        try:
                            rule = evaluate_rules(parsed, mailbox)
                            if rule.action == "needs_llm":
                                classification, latency_ms = llm.classify(parsed)
                                decision = decide_from_llm(classification, settings, mailbox)
                                row["decision_source"] = "llm"
                                row["model_latency_ms"] = latency_ms
                            else:
                                decision = decide_from_rule(rule)
                                row["decision_source"] = "rule"
                        except Exception as exc:
                            row["status"] = "failed"
                            row["action_taken"] = "classification_failed"
                            row["error"] = str(exc)
                            mailbox_payload["failed"] += 1
                            payload["failed"] += 1
                            mailbox_payload["results"].append(row)
                            export_rows.append(row)
                            continue

                        row["category"] = decision.category
                        row["target_folder"] = decision.target_folder
                        row["final_status"] = decision.final_status.value
                        row["action_taken"] = decision.action_taken
                        row["confidence"] = decision.confidence
                        row["requires_reply"] = decision.requires_reply
                        row["summary"] = decision.summary
                        row["reasoning_short"] = decision.reasoning_short
                    mailbox_payload["planned"] += 1
                    payload["planned"] += 1

                    if apply:
                        if row["target_folder"] == folder:
                            row["status"] = "skipped_same_folder"
                        else:
                            try:
                                if force_reprocess and state is not None:
                                    reset_count = state.delete_identity_matches(
                                        mailbox_id=mailbox.mailbox_id,
                                        message_id=parsed.message_id,
                                        fingerprint=fingerprint,
                                        content_fingerprint=content_fingerprint,
                                    )
                                    row["state_records_reset"] = reset_count
                                    mailbox_payload["state_records_reset"] += reset_count
                                    payload["state_records_reset"] += reset_count
                                copied_uid = imap.copy_message(folder, candidate.uid, str(row["target_folder"]))
                                row["copied_uid"] = copied_uid
                                if keep_source:
                                    row["status"] = "applied_copy_only"
                                else:
                                    imap.delete_message(folder, candidate.uid)
                                    row["status"] = "applied"
                                mailbox_payload["applied"] += 1
                                payload["applied"] += 1
                            except Exception as exc:
                                row["status"] = "failed"
                                row["action_taken"] = "apply_failed"
                                row["error"] = str(exc)
                                mailbox_payload["failed"] += 1
                                payload["failed"] += 1

                    mailbox_payload["results"].append(row)
                    export_rows.append(row)

        payload["mailboxes"].append(mailbox_payload)
        payload["mailboxes_processed"] += 1
        payload["folders_selected"] += len(mailbox_payload["folders_selected"])

    if export_csv is not None:
        _write_csv(export_rows, export_csv)
        payload["export_csv"] = str(export_csv)

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical multi-folder backfill for AI Mail Triage")
    parser.add_argument("--env-file", default=None, help="Optional env file path")
    parser.add_argument("--mailbox-id", default=None, help="Optional mailbox id filter")
    parser.add_argument(
        "--mode",
        choices=["stage", "classify"],
        default="stage",
        help="stage moves mail into INBOX.AI-Review for the normal worker; classify performs direct historical routing.",
    )
    parser.add_argument(
        "--folders",
        default=None,
        help="Optional comma-separated exact folder names. Defaults to all non-managed folders.",
    )
    parser.add_argument("--fetch-limit", type=int, default=None, help="Optional per-folder fetch limit override")
    parser.add_argument("--export-csv", default=None, help="Optional CSV export path")
    parser.add_argument("--apply", action="store_true", help="Actually copy and delete messages from source folders")
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="When used with --apply, copy messages to the new target folder but keep the originals in place.",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Delete matching state rows before applying, so copied messages can be processed again by the worker.",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    payload = run_historical_backfill(
        settings=settings,
        apply=args.apply,
        keep_source=args.keep_source,
        force_reprocess=args.force_reprocess,
        mode=args.mode,
        mailbox_id=args.mailbox_id,
        requested_folders=_normalize_requested_folders(args.folders),
        export_csv=Path(args.export_csv) if args.export_csv else None,
        fetch_limit=args.fetch_limit,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
