from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .quality_learning_cli import build_quality_learning_payload
from .sieve_deploy_cli import deploy_all
from .sieve_unify_cli import DEFAULT_POLICY, SievePolicy, _render


_IF_BLOCK_RE = re.compile(r"if\s+anyof\s*\((?P<cond>.*?)\)\s*\{(?P<body>.*?)\}", re.IGNORECASE | re.DOTALL)
_SUBJECT_LIST_RE = re.compile(r'header\s*:contains\s*"subject"\s*\[(?P<items>.*?)\]', re.IGNORECASE | re.DOTALL)
_ADDRESS_LIST_RE = re.compile(r'address\s*:contains\s*\[[^\]]*\]\s*\[(?P<items>.*?)\]', re.IGNORECASE | re.DOTALL)
_PRECEDENCE_LIST_RE = re.compile(r'header\s*:contains\s*"precedence"\s*\[(?P<items>.*?)\]', re.IGNORECASE | re.DOTALL)
_QUOTED_RE = re.compile(r'"([^"]+)"')


def _resolve_signals_dir(raw: str) -> Path:
    if raw != "auto":
        return Path(raw)
    candidates = [
        Path("logs/sieve-unified-auto"),
        Path("logs/sieve-unified"),
    ]
    backup_dirs = sorted(Path("logs").glob("sieve-backup-*"), reverse=True)
    candidates.extend(backup_dirs)
    for path in candidates:
        if path.exists() and any(path.glob("*.sieve")):
            return path
    return Path("logs/sieve-unified-auto")


def _parse_items(raw: str) -> list[str]:
    out: list[str] = []
    for match in _QUOTED_RE.finditer(raw):
        token = match.group(1).strip().lower()
        if token:
            out.append(token)
    return out


def _category_from_target(target_folder: str) -> str | None:
    folder = target_folder.lower()
    if "junk" in folder or "spam" in folder:
        return "spam"
    if "billing" in folder:
        return "billing"
    if "system" in folder:
        return "system"
    if "newsletter" in folder:
        return "newsletter"
    return None


def _extract_sieve_signals(sieve_dir: Path) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = {
        "billing_subject": Counter(),
        "billing_sender": Counter(),
        "system_subject": Counter(),
        "system_sender": Counter(),
        "newsletter_subject": Counter(),
        "newsletter_precedence": Counter(),
    }
    for path in sorted(sieve_dir.glob("*.sieve")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _IF_BLOCK_RE.finditer(text):
            cond = match.group("cond")
            body = match.group("body")
            target_match = re.search(r'fileinto\s+"([^"]+)"', body, re.IGNORECASE)
            if not target_match:
                continue
            category = _category_from_target(target_match.group(1))
            if category is None:
                continue

            subjects: list[str] = []
            addresses: list[str] = []
            precedence: list[str] = []
            for m in _SUBJECT_LIST_RE.finditer(cond):
                subjects.extend(_parse_items(m.group("items")))
            for m in _ADDRESS_LIST_RE.finditer(cond):
                addresses.extend(_parse_items(m.group("items")))
            for m in _PRECEDENCE_LIST_RE.finditer(cond):
                precedence.extend(_parse_items(m.group("items")))

            if category == "billing":
                counters["billing_subject"].update(subjects)
                counters["billing_sender"].update(addresses)
            elif category == "system":
                counters["system_subject"].update(subjects)
                counters["system_sender"].update(addresses)
            elif category == "newsletter":
                counters["newsletter_subject"].update(subjects)
                counters["newsletter_precedence"].update(precedence)
    return counters


def _merge_keywords(base: tuple[str, ...], learned: Counter[str], *, min_count: int, max_total: int) -> tuple[str, ...]:
    result = list(base)
    existing = {x.lower() for x in base}
    for token, count in learned.most_common():
        if count < min_count:
            continue
        if token in existing:
            continue
        result.append(token)
        existing.add(token)
        if len(result) >= max_total:
            break
    return tuple(result)


def _load_policy(path: Path | None) -> SievePolicy:
    if path is None or not path.exists():
        return DEFAULT_POLICY
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SievePolicy(
        spam_headers=tuple(payload.get("spam_headers", DEFAULT_POLICY.spam_headers)),
        spam_levels=tuple(payload.get("spam_levels", DEFAULT_POLICY.spam_levels)),
        billing_subject_keywords=tuple(payload.get("billing_subject_keywords", DEFAULT_POLICY.billing_subject_keywords)),
        billing_sender_keywords=tuple(payload.get("billing_sender_keywords", DEFAULT_POLICY.billing_sender_keywords)),
        system_sender_keywords=tuple(payload.get("system_sender_keywords", DEFAULT_POLICY.system_sender_keywords)),
        system_subject_keywords=tuple(payload.get("system_subject_keywords", DEFAULT_POLICY.system_subject_keywords)),
        newsletter_subject_keywords=tuple(
            payload.get("newsletter_subject_keywords", DEFAULT_POLICY.newsletter_subject_keywords)
        ),
        newsletter_precedence_keywords=tuple(
            payload.get("newsletter_precedence_keywords", DEFAULT_POLICY.newsletter_precedence_keywords)
        ),
    )


def _save_policy(path: Path, policy: SievePolicy) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(policy), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _build_mailbox_thresholds(settings: Settings, quality_payload: dict[str, Any]) -> dict[str, Any]:
    base_main = float(settings.move_confidence_threshold)
    base_other = float(settings.other_move_confidence_threshold)
    uncertain_by_mailbox = quality_payload.get("current_uncertain_by_mailbox", {})
    failed_by_mailbox = quality_payload.get("current_failed_by_mailbox", {})
    if not isinstance(uncertain_by_mailbox, dict):
        uncertain_by_mailbox = {}
    if not isinstance(failed_by_mailbox, dict):
        failed_by_mailbox = {}

    by_mailbox: dict[str, dict[str, float]] = {}
    for mailbox in settings.load_mailboxes():
        mailbox_id = mailbox.mailbox_id
        uncertain = int(uncertain_by_mailbox.get(mailbox_id, 0) or 0)
        failed = int(failed_by_mailbox.get(mailbox_id, 0) or 0)
        main = base_main
        other = base_other

        # Backlog-driven relaxation to reduce unnecessary uncertain accumulation.
        if uncertain >= 20:
            main -= 0.07
            other -= 0.07
        elif uncertain >= 10:
            main -= 0.05
            other -= 0.05
        elif uncertain >= 5:
            main -= 0.03
            other -= 0.03

        # Failure-driven tightening to protect precision and operational stability.
        if failed >= 5:
            main += 0.05
            other += 0.05
        elif failed >= 2:
            main += 0.03
            other += 0.03

        main = _clamp(main, 0.55, 0.95)
        other = _clamp(other, 0.35, 0.90)
        if main < other + 0.10:
            main = _clamp(other + 0.10, 0.55, 0.95)

        by_mailbox[mailbox_id] = {
            "move_confidence_threshold": round(main, 4),
            "other_move_confidence_threshold": round(other, 4),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": {
            "move_confidence_threshold": round(base_main, 4),
            "other_move_confidence_threshold": round(base_other, 4),
        },
        "by_mailbox": by_mailbox,
    }


def run_weekly_autotune(
    *,
    settings: Settings,
    sieve_signals_dir: Path,
    auto_policy_path: Path,
    generated_sieve_dir: Path,
    learning_output_dir: Path,
    window_days: int,
    min_count: int,
    max_keywords_per_bucket: int,
    mailbox_thresholds_path: Path,
    deploy: bool,
    deploy_port: int,
    deploy_timeout_seconds: int,
    deploy_tls_mode: str,
    deploy_strict_verify: bool,
    deploy_canary_count: int,
    deploy_canary_max_soft_pass_share: float,
) -> dict[str, Any]:
    quality_payload = build_quality_learning_payload(
        state_db=settings.state_db_path,
        audit_log=settings.audit_log_path,
        window_days=window_days,
    )

    resolved_signals_dir = sieve_signals_dir.resolve()
    signals = _extract_sieve_signals(sieve_signals_dir)
    base_policy = _load_policy(auto_policy_path)
    merged = SievePolicy(
        spam_headers=base_policy.spam_headers,
        spam_levels=base_policy.spam_levels,
        billing_subject_keywords=_merge_keywords(
            base_policy.billing_subject_keywords, signals["billing_subject"], min_count=min_count, max_total=max_keywords_per_bucket
        ),
        billing_sender_keywords=_merge_keywords(
            base_policy.billing_sender_keywords, signals["billing_sender"], min_count=min_count, max_total=max_keywords_per_bucket
        ),
        system_sender_keywords=_merge_keywords(
            base_policy.system_sender_keywords, signals["system_sender"], min_count=min_count, max_total=max_keywords_per_bucket
        ),
        system_subject_keywords=_merge_keywords(
            base_policy.system_subject_keywords, signals["system_subject"], min_count=min_count, max_total=max_keywords_per_bucket
        ),
        newsletter_subject_keywords=_merge_keywords(
            base_policy.newsletter_subject_keywords,
            signals["newsletter_subject"],
            min_count=min_count,
            max_total=max_keywords_per_bucket,
        ),
        newsletter_precedence_keywords=_merge_keywords(
            base_policy.newsletter_precedence_keywords,
            signals["newsletter_precedence"],
            min_count=min_count,
            max_total=max_keywords_per_bucket,
        ),
    )
    _save_policy(auto_policy_path, merged)
    mailbox_thresholds = _build_mailbox_thresholds(settings, quality_payload)
    mailbox_thresholds_path.parent.mkdir(parents=True, exist_ok=True)
    mailbox_thresholds_path.write_text(json.dumps(mailbox_thresholds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    generated_sieve_dir.mkdir(parents=True, exist_ok=True)
    generated_files: list[str] = []
    for mailbox in settings.load_mailboxes():
        content = _render(mailbox, merged)
        path = generated_sieve_dir / f"{mailbox.mailbox_id}.main.sieve"
        path.write_text(content, encoding="utf-8")
        generated_files.append(str(path))

    learning_output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    learning_json = learning_output_dir / f"weekly-autotune-{stamp}.quality.json"
    learning_payload: dict[str, Any] = {
        "generated_at": stamp,
        "signals_source_dir": str(resolved_signals_dir),
        "window_days": window_days,
        "quality_payload": quality_payload,
    }

    deploy_payload: dict[str, Any] | None = None
    if deploy:
        mailbox_ids = [mailbox.mailbox_id for mailbox in settings.load_mailboxes()]
        canary_count = min(max(deploy_canary_count, 0), len(mailbox_ids))
        canary_ids = mailbox_ids[:canary_count]
        rest_ids = mailbox_ids[canary_count:]
        canary_results = []
        full_results = []
        rollout_aborted = False
        rollout_abort_reason = None
        if canary_ids:
            canary_results = deploy_all(
                settings=settings,
                input_dir=generated_sieve_dir,
                script_name="main.sieve",
                port=deploy_port,
                timeout_seconds=deploy_timeout_seconds,
                tls_mode=deploy_tls_mode,
                strict_verify=deploy_strict_verify,
                mailbox_ids=canary_ids,
            )
            canary_verified = all(item.verified for item in canary_results) if canary_results else True
            soft_pass_count = sum(1 for item in canary_results if item.verification_mode == "soft_pass")
            soft_pass_share = (soft_pass_count / len(canary_results)) if canary_results else 0.0
            if (not canary_verified) or (soft_pass_share > deploy_canary_max_soft_pass_share):
                rollout_aborted = True
                rollout_abort_reason = (
                    f"canary gate failed: verified={canary_verified}, "
                    f"soft_pass_share={soft_pass_share:.4f}, "
                    f"max_soft_pass_share={deploy_canary_max_soft_pass_share:.4f}"
                )
        if not rollout_aborted and rest_ids:
            full_results = deploy_all(
                settings=settings,
                input_dir=generated_sieve_dir,
                script_name="main.sieve",
                port=deploy_port,
                timeout_seconds=deploy_timeout_seconds,
                tls_mode=deploy_tls_mode,
                strict_verify=deploy_strict_verify,
                mailbox_ids=rest_ids,
            )
        all_results = canary_results + full_results
        deploy_payload = {
            "ok": sum(1 for x in all_results if x.verified),
            "failed": sum(1 for x in all_results if not x.verified),
            "rollout_aborted": rollout_aborted,
            "rollout_abort_reason": rollout_abort_reason,
            "canary": {
                "mailbox_ids": canary_ids,
                "results": [x.__dict__ for x in canary_results],
            },
            "full": {
                "mailbox_ids": rest_ids,
                "results": [x.__dict__ for x in full_results],
            },
            "results": [x.__dict__ for x in all_results],
        }
    learning_payload["deploy"] = deploy_payload
    learning_json.write_text(json.dumps(learning_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "window_days": window_days,
        "signals_source_dir": str(resolved_signals_dir),
        "auto_policy_path": str(auto_policy_path),
        "mailbox_thresholds_path": str(mailbox_thresholds_path),
        "generated_sieve_dir": str(generated_sieve_dir),
        "generated_sieve_files": generated_files,
        "quality_report_path": str(learning_json),
        "quality_summary": quality_payload.get("audit_summary", {}),
        "quality_recommendations": quality_payload.get("recommendations", []),
        "deploy": deploy_payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Weekly auto-tune job: learn from Sieve + quality report, refresh unified policy, optional deploy."
    )
    parser.add_argument("--env-file", default=None, help="Optional env file path.")
    parser.add_argument(
        "--sieve-signals-dir",
        default="auto",
        help="Directory with reference .sieve scripts, or 'auto' to use newest available unified/backup set.",
    )
    parser.add_argument("--auto-policy-path", default="config/sieve_policy.auto.json", help="Generated adaptive policy JSON path.")
    parser.add_argument(
        "--mailbox-thresholds-path",
        default="config/mailbox_thresholds.auto.json",
        help="Generated per-mailbox confidence thresholds JSON path.",
    )
    parser.add_argument("--generated-sieve-dir", default="logs/sieve-unified-auto", help="Output directory for regenerated Sieve scripts.")
    parser.add_argument("--learning-output-dir", default="logs/weekly-autotune", help="Output directory for quality snapshots.")
    parser.add_argument("--window-days", type=int, default=14, help="Rolling quality window in days.")
    parser.add_argument("--min-count", type=int, default=2, help="Minimum occurrence in Sieve signals to learn a token.")
    parser.add_argument("--max-keywords-per-bucket", type=int, default=80, help="Cap keywords per policy bucket.")
    parser.add_argument("--deploy", action="store_true", help="Deploy generated scripts to server via ManageSieve.")
    parser.add_argument("--deploy-port", type=int, default=4190, help="ManageSieve port.")
    parser.add_argument("--deploy-timeout-seconds", type=int, default=20, help="ManageSieve timeout.")
    parser.add_argument(
        "--deploy-tls-mode",
        default="auto",
        choices=["auto", "implicit", "starttls"],
        help="ManageSieve TLS mode.",
    )
    parser.add_argument("--deploy-strict-verify", action="store_true", help="Require strict deploy verification.")
    parser.add_argument("--deploy-canary-count", type=int, default=2, help="Number of first mailboxes to deploy in canary phase.")
    parser.add_argument(
        "--deploy-canary-max-soft-pass-share",
        type=float,
        default=0.5,
        help="Abort full rollout when canary soft-pass share exceeds this fraction.",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()  # type: ignore[call-arg]
    payload = run_weekly_autotune(
        settings=settings,
        sieve_signals_dir=_resolve_signals_dir(args.sieve_signals_dir),
        auto_policy_path=Path(args.auto_policy_path),
        mailbox_thresholds_path=Path(args.mailbox_thresholds_path),
        generated_sieve_dir=Path(args.generated_sieve_dir),
        learning_output_dir=Path(args.learning_output_dir),
        window_days=args.window_days,
        min_count=args.min_count,
        max_keywords_per_bucket=args.max_keywords_per_bucket,
        deploy=args.deploy,
        deploy_port=args.deploy_port,
        deploy_timeout_seconds=args.deploy_timeout_seconds,
        deploy_tls_mode=args.deploy_tls_mode,
        deploy_strict_verify=args.deploy_strict_verify,
        deploy_canary_count=args.deploy_canary_count,
        deploy_canary_max_soft_pass_share=args.deploy_canary_max_soft_pass_share,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
