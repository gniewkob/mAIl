from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import MailboxConfig, Settings
from .imap_client import IMAPAuthError, IMAPClient


@dataclass
class FolderMigrationStats:
    mailbox_id: str
    source_folder: str
    keyword: str
    scanned: int = 0
    no_message_id: int = 0
    missing_in_source: int = 0
    dedup_skipped: int = 0
    copied: int = 0
    pruned: int = 0
    tagged: int = 0
    missing_tag: int = 0
    tagged_existing: int = 0
    tag_already_present: int = 0
    errors: int = 0
    mailbox_error: str | None = None


def _category_folders(mailbox: MailboxConfig) -> list[tuple[str, str]]:
    return [
        (mailbox.imap_appointments_folder, "appointment"),
        (mailbox.imap_questions_folder, "question"),
        (mailbox.imap_complaints_folder, "complaint"),
        (mailbox.imap_spam_folder, "spam"),
        (mailbox.imap_newsletter_folder, "newsletter"),
        (mailbox.imap_offer_folder, "offer"),
        (mailbox.imap_other_folder, "other"),
        (mailbox.imap_billing_folder, "billing"),
        (mailbox.imap_system_folder, "system"),
        (mailbox.imap_uncertain_folder, "uncertain"),
    ]


def _keyword(prefix: str, category: str) -> str:
    prefix_norm = (prefix or "$AI_").strip() or "$AI_"
    return f"{prefix_norm}{category}"


def _midless_marker(prefix: str, folder: str, uid: str) -> str:
    prefix_norm = (prefix or "$AI_").strip() or "$AI_"
    digest = hashlib.sha1(f"{folder}|{uid}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix_norm}midless_{digest}"


def migrate_mailbox(
    *,
    mailbox: MailboxConfig,
    apply: bool,
    dedupe_by_message_id: bool,
    limit_per_folder: int,
    tag_prefix: str,
    missing_policy: str,
) -> list[FolderMigrationStats]:
    stats: list[FolderMigrationStats] = []
    try:
        with IMAPClient(mailbox) as imap:
            source_index: dict[str, list[str]] = {}
            source_keywords: dict[str, set[str]] = {}
            if dedupe_by_message_id:
                source_uids = imap.list_uids(mailbox.imap_source_folder)
                source_msgids = imap.fetch_message_id_headers(mailbox.imap_source_folder, source_uids)
                source_keywords = imap.fetch_uid_keywords(mailbox.imap_source_folder, source_uids)
                for source_uid, source_message_id in source_msgids.items():
                    if not source_message_id:
                        continue
                    source_index.setdefault(source_message_id, []).append(source_uid)
            for folder, category in _category_folders(mailbox):
                if folder == mailbox.imap_source_folder:
                    continue
                keyword = _keyword(tag_prefix, category)
                s = FolderMigrationStats(mailbox_id=mailbox.mailbox_id, source_folder=folder, keyword=keyword)
                stats.append(s)
                uids = imap.list_uids(folder)
                if limit_per_folder > 0:
                    uids = uids[-limit_per_folder:]
                folder_msgids = imap.fetch_message_id_headers(folder, uids) if dedupe_by_message_id else {}
                for uid in uids:
                    s.scanned += 1
                    try:
                        message_id = folder_msgids.get(uid) if dedupe_by_message_id else None
                        if dedupe_by_message_id and not message_id:
                            s.no_message_id += 1
                            marker = _midless_marker(tag_prefix, folder, uid)
                            if imap.message_exists_with_keyword(mailbox.imap_source_folder, marker):
                                s.dedup_skipped += 1
                                s.tag_already_present += 1
                                continue
                            s.missing_in_source += 1
                            if apply:
                                if missing_policy == "delete":
                                    imap.delete_message(folder, uid)
                                    s.pruned += 1
                                elif missing_policy == "copy":
                                    target_uid = imap.copy_message(folder, uid, mailbox.imap_source_folder)
                                    if target_uid:
                                        imap.add_keyword(mailbox.imap_source_folder, target_uid, keyword)
                                        imap.add_keyword(mailbox.imap_source_folder, target_uid, marker)
                                        source_keywords.setdefault(target_uid, set()).update({keyword, marker})
                                        s.tagged += 1
                                    s.copied += 1
                            continue
                        existing_source_uids = source_index.get(message_id, []) if message_id else []
                        exists_in_source = bool(dedupe_by_message_id and existing_source_uids)
                        if exists_in_source:
                            s.dedup_skipped += 1
                            tagged = any(keyword in source_keywords.get(source_uid, set()) for source_uid in existing_source_uids)
                            if tagged:
                                s.tag_already_present += 1
                            else:
                                s.missing_tag += 1
                                if apply:
                                    for source_uid in existing_source_uids:
                                        imap.add_keyword(mailbox.imap_source_folder, source_uid, keyword)
                                        source_keywords.setdefault(source_uid, set()).add(keyword)
                                        s.tagged_existing += 1
                            continue
                        s.missing_in_source += 1
                        if apply:
                            if missing_policy == "delete":
                                imap.delete_message(folder, uid)
                                s.pruned += 1
                            elif missing_policy == "copy":
                                target_uid = imap.copy_message(folder, uid, mailbox.imap_source_folder)
                                if target_uid:
                                    imap.add_keyword(mailbox.imap_source_folder, target_uid, keyword)
                                    source_keywords.setdefault(target_uid, set()).add(keyword)
                                    if message_id:
                                        source_index.setdefault(message_id, []).append(target_uid)
                                    s.tagged += 1
                                s.copied += 1
                    except Exception:
                        s.errors += 1
    except IMAPAuthError as exc:
        stats.append(
            FolderMigrationStats(
                mailbox_id=mailbox.mailbox_id,
                source_folder="__mailbox__",
                keyword="",
                errors=1,
                mailbox_error=str(exc),
            )
        )
    except Exception as exc:
        stats.append(
            FolderMigrationStats(
                mailbox_id=mailbox.mailbox_id,
                source_folder="__mailbox__",
                keyword="",
                errors=1,
                mailbox_error=str(exc),
            )
        )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate old folder-based architecture to INBOX+tag architecture.")
    parser.add_argument("--env-file", default=None, help="Optional env file path.")
    parser.add_argument("--apply", action="store_true", help="Execute IMAP copy+tag operations. Without this flag, dry-run only.")
    parser.add_argument("--no-dedupe", action="store_true", help="Disable Message-ID deduplication against source folder.")
    parser.add_argument("--limit-per-folder", type=int, default=0, help="Process only last N messages per folder (0 = all).")
    parser.add_argument(
        "--missing-policy",
        choices=("copy", "delete", "report"),
        default="copy",
        help="How to handle messages present only in category folders: copy to source, delete copy, or report-only.",
    )
    parser.add_argument("--tag-prefix", default="$AI_", help="Tag prefix used during migration.")
    parser.add_argument("--mailbox-id", action="append", default=[], help="Process only selected mailbox id(s).")
    parser.add_argument("--skip-mailbox-id", action="append", default=[], help="Mailbox ID to skip (can be repeated).")
    parser.add_argument("--output", default="logs/tag-migration-report.json", help="Path to JSON report.")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    missing_policy = str(args.missing_policy)
    if missing_policy == "delete" and args.no_dedupe:
        raise SystemExit("--missing-policy delete requires Message-ID dedupe (remove --no-dedupe).")
    all_stats: list[FolderMigrationStats] = []
    skip_ids = {str(item).strip() for item in args.skip_mailbox_id if str(item).strip()}
    include_ids = {str(item).strip() for item in args.mailbox_id if str(item).strip()}
    selected_mailboxes = settings.load_mailboxes()
    if include_ids:
        selected_mailboxes = [mailbox for mailbox in selected_mailboxes if mailbox.mailbox_id in include_ids]
        if not selected_mailboxes:
            raise SystemExit(f"No mailboxes matched --mailbox-id selection: {sorted(include_ids)}")
    for mailbox in selected_mailboxes:
        if mailbox.mailbox_id in skip_ids:
            all_stats.append(
                FolderMigrationStats(
                    mailbox_id=mailbox.mailbox_id,
                    source_folder="__mailbox__",
                    keyword="",
                    mailbox_error="skipped_by_operator",
                )
            )
            continue
        all_stats.extend(
            migrate_mailbox(
                mailbox=mailbox,
                apply=args.apply,
                dedupe_by_message_id=not args.no_dedupe,
                limit_per_folder=max(0, args.limit_per_folder),
                tag_prefix=args.tag_prefix,
                missing_policy=missing_policy,
            )
        )

    payload = {
        "apply": bool(args.apply),
        "missing_policy": missing_policy,
        "mailboxes": len(selected_mailboxes),
        "stats": [asdict(item) for item in all_stats],
        "totals": {
            "scanned": sum(item.scanned for item in all_stats),
            "no_message_id": sum(item.no_message_id for item in all_stats),
            "missing_in_source": sum(item.missing_in_source for item in all_stats),
            "dedup_skipped": sum(item.dedup_skipped for item in all_stats),
            "copied": sum(item.copied for item in all_stats),
            "pruned": sum(item.pruned for item in all_stats),
            "tagged": sum(item.tagged for item in all_stats),
            "missing_tag": sum(item.missing_tag for item in all_stats),
            "tagged_existing": sum(item.tagged_existing for item in all_stats),
            "tag_already_present": sum(item.tag_already_present for item in all_stats),
            "errors": sum(item.errors for item in all_stats),
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
