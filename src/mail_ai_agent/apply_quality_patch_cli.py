from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _extract_patch_target(patch_path: Path) -> str:
    for line in patch_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+++ b/"):
            return line.removeprefix("+++ b/").strip()
    raise ValueError("Patch does not contain a target file header")


def _run_patch(*, repo_root: Path, patch_path: Path, dry_run: bool) -> None:
    cmd = ["/usr/bin/patch", "-p1", "-i", str(patch_path)]
    if dry_run:
        cmd.insert(1, "--dry-run")
    subprocess.run(cmd, cwd=repo_root, check=True, capture_output=True, text=True)


def _run_validation_tests(repo_root: Path) -> None:
    subprocess.run(
        [
            str(repo_root / ".venv/bin/pytest"),
            "-q",
            "tests/unit/test_rule_engine.py",
            "tests/unit/test_quality_learning_cli.py",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_result_log(*, log_dir: Path, patch_path: Path, target: str, mode: str, status: str, backup_path: str | None = None) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"quality-patch-{mode}-{_utc_stamp()}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": status,
        "patch_path": str(patch_path),
        "target": target,
        "backup_path": backup_path,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_check(*, repo_root: Path, patch_path: Path) -> dict[str, str]:
    target = _extract_patch_target(patch_path)
    _run_patch(repo_root=repo_root, patch_path=patch_path, dry_run=True)
    log_path = _write_result_log(
        log_dir=repo_root / "logs/quality-learning",
        patch_path=patch_path,
        target=target,
        mode="check",
        status="ok",
    )
    return {
        "mode": "check",
        "status": "ok",
        "patch_path": str(patch_path),
        "target": target,
        "log_path": str(log_path),
    }


def run_apply(*, repo_root: Path, patch_path: Path) -> dict[str, str]:
    target = _extract_patch_target(patch_path)
    target_path = repo_root / target
    if not target_path.exists():
        raise FileNotFoundError(f"Patch target does not exist: {target_path}")

    _run_patch(repo_root=repo_root, patch_path=patch_path, dry_run=True)

    backup_path = target_path.with_name(f"{target_path.name}.bak.{_utc_stamp()}")
    shutil.copy2(target_path, backup_path)

    try:
        _run_patch(repo_root=repo_root, patch_path=patch_path, dry_run=False)
        _run_validation_tests(repo_root)
    except Exception:
        shutil.copy2(backup_path, target_path)
        log_path = _write_result_log(
            log_dir=repo_root / "logs/quality-learning",
            patch_path=patch_path,
            target=target,
            mode="apply",
            status="rolled_back",
            backup_path=str(backup_path),
        )
        raise RuntimeError(f"Patch apply failed and was rolled back. See {log_path}") from None

    log_path = _write_result_log(
        log_dir=repo_root / "logs/quality-learning",
        patch_path=patch_path,
        target=target,
        mode="apply",
        status="applied",
        backup_path=str(backup_path),
    )
    return {
        "mode": "apply",
        "status": "applied",
        "patch_path": str(patch_path),
        "target": target,
        "backup_path": str(backup_path),
        "log_path": str(log_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or apply a generated quality-learning patch.")
    parser.add_argument("--patch", required=True, help="Path to the generated .patch file")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate the patch without applying it")
    mode.add_argument("--apply", action="store_true", help="Apply the patch, run tests, and keep a backup")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    patch_path = Path(args.patch).resolve()

    if args.check:
        result = run_check(repo_root=repo_root, patch_path=patch_path)
    else:
        result = run_apply(repo_root=repo_root, patch_path=patch_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
