from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from mail_ai_agent.apply_quality_patch_cli import run_apply, run_check


def _write_patch(path: Path, original: str, updated: str) -> None:
    diff = "".join(
        difflib.unified_diff(
            [f"{original}\n"],
            [f"{updated}\n"],
            fromfile="a/src/mail_ai_agent/rule_engine.py",
            tofile="b/src/mail_ai_agent/rule_engine.py",
        )
    )
    path.write_text(diff, encoding="utf-8")


def test_run_check_validates_patch_without_changing_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    target = repo_root / "src/mail_ai_agent/rule_engine.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    patch_path = repo_root / "logs/quality-learning/sample.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    _write_patch(patch_path, "VALUE = 1", "VALUE = 2")

    result = run_check(repo_root=repo_root, patch_path=patch_path)

    assert result["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_run_apply_restores_backup_when_validation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path / "repo"
    target = repo_root / "src/mail_ai_agent/rule_engine.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    patch_path = repo_root / "logs/quality-learning/sample.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    _write_patch(patch_path, "VALUE = 1", "VALUE = 2")

    from mail_ai_agent import apply_quality_patch_cli as module

    def fail_tests(repo_root: Path) -> None:
        raise RuntimeError("tests failed")

    monkeypatch.setattr(module, "_run_validation_tests", fail_tests)

    with pytest.raises(RuntimeError):
        run_apply(repo_root=repo_root, patch_path=patch_path)

    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert list(target.parent.glob("rule_engine.py.bak.*"))
