from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .reporting import summarize_state


def _load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def _set_env_key(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            updated = True
        else:
            out.append(line)
    if not updated:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _feedback_7d_summary(feedback_log: Path) -> dict[str, float]:
    if not feedback_log.exists():
        return {"corrected": 0.0, "outcome_bad_rate": 0.0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    corrected = 0
    outcome_total = 0
    outcome_bad = 0
    for line in feedback_log.read_text(encoding="utf-8").splitlines()[-10000:]:
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        ts_raw = item.get("timestamp")
        if ts_raw is None:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        if bool(item.get("was_wrong", False)):
            corrected += 1
        if "outcome_ok" in item:
            outcome_total += 1
            if not bool(item.get("outcome_ok")):
                outcome_bad += 1
    outcome_bad_rate = (outcome_bad / outcome_total) if outcome_total else 0.0
    return {"corrected": float(corrected), "outcome_bad_rate": float(outcome_bad_rate)}


def recommend_rate(
    *,
    current_rate: float,
    uncertain_by_mailbox: dict[str, int],
    failed_by_mailbox: dict[str, int],
    max_uncertain_per_mailbox: int,
    max_failed_per_mailbox: int,
    outcome_bad_rate_7d: float,
    corrected_7d: float,
    min_rate: float = 0.005,
    max_rate: float = 0.03,
    step: float = 0.0025,
) -> tuple[float, str]:
    overload_uncertain = any(v >= max_uncertain_per_mailbox for v in uncertain_by_mailbox.values())
    overload_failed = any(v >= max_failed_per_mailbox for v in failed_by_mailbox.values())
    if overload_uncertain or overload_failed or outcome_bad_rate_7d > 0.2:
        return (max(min_rate, round(current_rate - step, 4)), "decrease")

    # Conservative increase only when we have low risk and weak learning signal volume.
    has_mailbox_signal = bool(uncertain_by_mailbox) or bool(failed_by_mailbox)
    very_low_backlog = all(v <= max(1, max_uncertain_per_mailbox // 3) for v in uncertain_by_mailbox.values())
    no_failed = all(v == 0 for v in failed_by_mailbox.values())
    if has_mailbox_signal and very_low_backlog and no_failed and corrected_7d < 10 and outcome_bad_rate_7d <= 0.1:
        return (min(max_rate, round(current_rate + step, 4)), "increase")

    return (round(current_rate, 4), "hold")


def run_tuner(
    *,
    env_file: Path,
    state_db: Path,
    audit_log: Path,
    feedback_log: Path,
    apply: bool,
    output_log: Path,
) -> dict[str, Any]:
    env = _load_env(env_file)
    current_rate = float(env.get("EXPLORATION_RATE", "0.02") or 0.02)
    max_uncertain = int(env.get("EXPLORATION_MAX_UNCERTAIN_PER_MAILBOX", "15") or 15)
    max_failed = int(env.get("EXPLORATION_MAX_FAILED_PER_MAILBOX", "3") or 3)

    state = summarize_state(state_db)
    uncertain_by_mailbox = {str(k): int(v) for k, v in (state.get("uncertain_by_mailbox", {}) or {}).items()}
    failed_by_mailbox = {str(k): int(v) for k, v in (state.get("failed_by_mailbox", {}) or {}).items()}
    feedback_7d = _feedback_7d_summary(feedback_log)

    new_rate, action = recommend_rate(
        current_rate=current_rate,
        uncertain_by_mailbox=uncertain_by_mailbox,
        failed_by_mailbox=failed_by_mailbox,
        max_uncertain_per_mailbox=max_uncertain,
        max_failed_per_mailbox=max_failed,
        outcome_bad_rate_7d=float(feedback_7d["outcome_bad_rate"]),
        corrected_7d=float(feedback_7d["corrected"]),
    )

    changed = new_rate != round(current_rate, 4)
    if apply and changed:
        _set_env_key(env_file, "EXPLORATION_RATE", f"{new_rate:.4f}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "env_file": str(env_file),
        "current_rate": current_rate,
        "recommended_rate": new_rate,
        "action": action,
        "changed": changed,
        "applied": bool(apply and changed),
        "state": {
            "uncertain_by_mailbox": uncertain_by_mailbox,
            "failed_by_mailbox": failed_by_mailbox,
        },
        "feedback_7d": feedback_7d,
        "policy": {
            "max_uncertain_per_mailbox": max_uncertain,
            "max_failed_per_mailbox": max_failed,
        },
    }

    output_log.parent.mkdir(parents=True, exist_ok=True)
    with output_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-tune exploration rate based on backlog and feedback quality.")
    parser.add_argument("--env-file", default=".env.multi.prod")
    parser.add_argument("--state-db", default="data/multi-prod-state.sqlite")
    parser.add_argument("--audit-log", default="logs/multi-prod-audit.jsonl")
    parser.add_argument("--feedback-log", default="logs/quality-feedback.jsonl")
    parser.add_argument("--output-log", default="logs/exploration-tuner.jsonl")
    parser.add_argument("--apply", action="store_true", help="Apply recommended rate to env file")
    args = parser.parse_args()

    payload = run_tuner(
        env_file=Path(args.env_file),
        state_db=Path(args.state_db),
        audit_log=Path(args.audit_log),
        feedback_log=Path(args.feedback_log),
        apply=args.apply,
        output_log=Path(args.output_log),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
