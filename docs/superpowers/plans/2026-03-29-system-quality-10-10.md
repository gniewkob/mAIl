# mAIl System Quality 10/10 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all 27 audit findings (10 HIGH · 13 MEDIUM · 4 LOW) and bring the mAIl system to production-grade quality across security, correctness, performance, code quality, tests, and deployment.

**Architecture:** Changes are non-breaking incremental fixes. No schema migrations needed (existing SQLite tables are extended in-place). All changes preserve the existing single-worker-per-run design. UIDPLUS is known to be absent on the server — the plan works around this with batch FETCH (no UIDPLUS needed) + admin email consultation for eventual UIDPLUS enablement.

**Tech Stack:** Python 3.11+, pydantic-settings, imaplib, sqlite3, smtplib, pytest, uv

---

## File Map

| Action | File | Change |
|--------|------|--------|
| **Create** | `src/mail_ai_agent/utils.py` | Shared `_hash_value`, `_chmod_owner_only` |
| **Create** | `src/mail_ai_agent/smtp_notifier.py` | Minimal SMTP send |
| **Create** | `src/mail_ai_agent/admin_notify_cli.py` | UIDPLUS admin notification CLI |
| **Modify** | `src/mail_ai_agent/state_manager.py` | WAL + busy_timeout, atomic lock, import utils |
| **Modify** | `src/mail_ai_agent/audit_logger.py` | fsync, import utils |
| **Modify** | `src/mail_ai_agent/maintenance.py` | archive chmod, batch scrub, import utils |
| **Modify** | `src/mail_ai_agent/manifest_secrets_cli.py` | chmod 0o600 on sidecar |
| **Modify** | `src/mail_ai_agent/email_parser.py` | UTC in `_normalize_date`, remove dead alias |
| **Modify** | `src/mail_ai_agent/cleanup_cli.py` | per-record try/except |
| **Modify** | `src/mail_ai_agent/folder_mapper.py` | safe `.get()` with fallback |
| **Modify** | `src/mail_ai_agent/config.py` | `or` → `is not None`, SMTP fields, PII consistency check |
| **Modify** | `src/mail_ai_agent/draft_store.py` | PII redaction at save |
| **Modify** | `src/mail_ai_agent/llm_gateway.py` | backoff between retries |
| **Modify** | `src/mail_ai_agent/rule_engine.py` | hardcoded email → config field |
| **Modify** | `src/mail_ai_agent/imap_client.py` | batch FETCH |
| **Modify** | `src/mail_ai_agent/reporting.py` | streaming cursor in export_state_csv |
| **Modify** | `src/mail_ai_agent/main.py` | import utils, type hints on `_log_skip` |
| **Modify** | `com.mailai.multi.plist.template` | add EnvironmentVariables/PYTHONPATH |
| **Modify** | `.gitignore` | add `config/mailboxes*/` |
| **Modify** | `tests/unit/test_main_workflow.py` | instance-level FakeIMAPClient state |
| **Modify** | `tests/unit/test_rule_engine.py` | use MailboxConfig not Settings |
| **Modify** | `tests/unit/test_cleanup_cli.py` | partial-failure test |
| **Modify** | `tests/unit/test_email_parser.py` | edge case tests |

---

## Task 1: Extract shared `_hash_value` and `_chmod_owner_only` to `utils.py`

**Enables:** Tasks 2, 4, 6, 7 — removes duplication across `main.py`, `state_manager.py`, `maintenance.py`.

**Files:**
- Create: `src/mail_ai_agent/utils.py`
- Modify: `src/mail_ai_agent/state_manager.py`
- Modify: `src/mail_ai_agent/maintenance.py`
- Modify: `src/mail_ai_agent/audit_logger.py`
- Modify: `src/mail_ai_agent/main.py`
- Modify: `src/mail_ai_agent/draft_store.py`
- Test: `tests/unit/test_utils.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_utils.py
from mail_ai_agent.utils import _hash_value, _chmod_owner_only


def test_hash_value_none():
    assert _hash_value(None) is None


def test_hash_value_empty():
    assert _hash_value("") is None


def test_hash_value_str():
    result = _hash_value("hello")
    assert result is not None
    assert len(result) == 64
    assert result == _hash_value("hello")  # deterministic


def test_hash_value_different():
    assert _hash_value("a") != _hash_value("b")
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_utils.py -v
```
Expected: `ModuleNotFoundError: No module named 'mail_ai_agent.utils'`

- [ ] **Step 3: Create `utils.py`**

```python
# src/mail_ai_agent/utils.py
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _hash_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _chmod_owner_only(path: Path) -> None:
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass
```

- [ ] **Step 4: Run test to confirm pass**

```
pytest tests/unit/test_utils.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Replace duplicates in `state_manager.py`**

Remove the `_hash_value` and `_chmod_owner_only` functions at the bottom of `state_manager.py` (lines 527–538) and add import at top:

```python
# Add after existing imports, around line 10:
from .utils import _chmod_owner_only, _hash_value
```

Remove these functions from the file (they are at the bottom, after `StateManager` class):
```python
def _chmod_owner_only(path: Path) -> None:
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass


def _hash_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
```

Also remove `import hashlib` and `import os` from `state_manager.py` only if they are no longer used elsewhere in that file (check — `os` is only used in `_chmod_owner_only`, `hashlib` only in `_hash_value`).

- [ ] **Step 6: Replace duplicates in `maintenance.py`**

Add import to `maintenance.py`:
```python
from .utils import _chmod_owner_only, _hash_value
```

Remove the `_hash_value` and `_chmod_owner_only` functions at lines 150–161. Remove `import hashlib` and `import os` if unused elsewhere in `maintenance.py` (check: `os` only in `_chmod_owner_only`, `hashlib` only in `_hash_value` → both removable).

- [ ] **Step 7: Replace duplicate in `audit_logger.py`**

Add import to `audit_logger.py`:
```python
from .utils import _chmod_owner_only
```

Remove `_chmod_owner_only` from the bottom of `audit_logger.py` (lines 41–46). Remove `import os` if unused (it is — only used in `_chmod_owner_only`). Keep `import hashlib` — it's used in `_sanitize_payload`.

- [ ] **Step 8: Replace duplicate in `main.py`**

Add import to `main.py`:
```python
from .utils import _hash_value
```

Remove `_hash_value` from `main.py` (lines 107–110). Remove `import hashlib` if unused elsewhere in `main.py`.

- [ ] **Step 9: Replace duplicate in `draft_store.py`**

Add import to `draft_store.py`:
```python
from .utils import _chmod_owner_only
```

Remove `_chmod_owner_only` from `draft_store.py` (lines 32–37). Remove `import os` if unused.

- [ ] **Step 10: Run full test suite**

```
pytest -q
```
Expected: all existing tests pass (no new failures).

- [ ] **Step 11: Commit**

```bash
git add src/mail_ai_agent/utils.py \
        src/mail_ai_agent/state_manager.py \
        src/mail_ai_agent/maintenance.py \
        src/mail_ai_agent/audit_logger.py \
        src/mail_ai_agent/main.py \
        src/mail_ai_agent/draft_store.py \
        tests/unit/test_utils.py
git commit -m "refactor: extract shared _hash_value and _chmod_owner_only to utils"
```

---

## Task 2: Fix fingerprint timezone — UTC in `_normalize_date`

**Problem:** `value.astimezone()` uses local TZ → fingerprints change after DST or server migration → double-processing.

**Files:**
- Modify: `src/mail_ai_agent/email_parser.py:179-182`
- Test: `tests/unit/test_email_parser.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_email_parser.py  (add to existing file)
from datetime import datetime, timezone, timedelta
from mail_ai_agent.email_parser import compute_message_fingerprint
from mail_ai_agent.schemas import ParsedEmail


def _make_parsed(date: datetime | None = None) -> ParsedEmail:
    return ParsedEmail(
        message_id="<test@example.com>",
        sender="from@example.com",
        subject="test",
        plain_text_body="body",
        normalized_body="body",
        date=date,
    )


def test_fingerprint_stable_across_timezones():
    """Same moment in time expressed in different TZs must produce same fingerprint."""
    utc_time = datetime(2024, 3, 31, 12, 0, 0, tzinfo=timezone.utc)
    warsaw_time = utc_time.astimezone(timezone(timedelta(hours=2)))  # CEST

    fp_utc = compute_message_fingerprint(_make_parsed(utc_time))
    fp_warsaw = compute_message_fingerprint(_make_parsed(warsaw_time))

    assert fp_utc == fp_warsaw, (
        f"Fingerprints differ: {fp_utc} vs {fp_warsaw}. "
        "astimezone() must normalize to UTC before serializing."
    )
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_email_parser.py::test_fingerprint_stable_across_timezones -v
```
Expected: `FAILED — AssertionError: Fingerprints differ`

- [ ] **Step 3: Fix `_normalize_date` in `email_parser.py`**

Change line 182:
```python
# Before:
return value.astimezone().isoformat()

# After:
return value.astimezone(timezone.utc).isoformat()
```

Also add `timezone` to the existing import at line 7:
```python
# Before:
from datetime import datetime

# After:
from datetime import datetime, timezone
```

- [ ] **Step 4: Run test to confirm pass**

```
pytest tests/unit/test_email_parser.py::test_fingerprint_stable_across_timezones -v
```
Expected: `PASSED`

- [ ] **Step 5: Run full suite**

```
pytest -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/email_parser.py tests/unit/test_email_parser.py
git commit -m "fix: normalize fingerprint dates to UTC to prevent DST-driven double-processing"
```

---

## Task 3: Remove dead `compute_fingerprint` alias from `email_parser.py`

**Problem:** `compute_fingerprint` at line 147 is an alias of `compute_message_fingerprint`. It is not imported anywhere. Dead code creates confusion.

**Files:**
- Modify: `src/mail_ai_agent/email_parser.py:147-148`

- [ ] **Step 1: Verify no import of `compute_fingerprint` exists**

```
grep -r "compute_fingerprint" src/ tests/
```
Expected: only `email_parser.py` itself defines and uses it (alias). No external caller.

- [ ] **Step 2: Remove the alias**

Delete lines 147–148 from `email_parser.py`:
```python
# DELETE these two lines:
def compute_fingerprint(parsed_email: ParsedEmail) -> str:
    return compute_message_fingerprint(parsed_email)
```

- [ ] **Step 3: Run full suite**

```
pytest -q
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/mail_ai_agent/email_parser.py
git commit -m "refactor: remove dead compute_fingerprint alias"
```

---

## Task 4: SQLite safety — WAL mode + busy timeout

**Problem:** Without WAL and `busy_timeout`, concurrent access (launchd run overlap) raises `OperationalError: database is locked`.

**Files:**
- Modify: `src/mail_ai_agent/state_manager.py:22-25`
- Test: `tests/unit/test_state_manager.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_state_manager.py  (add to existing file)
import sqlite3
from pathlib import Path


def test_state_manager_uses_wal_mode(tmp_path: Path):
    from mail_ai_agent.state_manager import StateManager

    StateManager(tmp_path / "state.sqlite")

    with sqlite3.connect(tmp_path / "state.sqlite") as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal", f"Expected WAL mode, got: {mode}"


def test_state_manager_has_busy_timeout(tmp_path: Path):
    from mail_ai_agent.state_manager import StateManager

    sm = StateManager(tmp_path / "state.sqlite")

    with sm._connect() as conn:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got: {timeout}"
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_state_manager.py::test_state_manager_uses_wal_mode tests/unit/test_state_manager.py::test_state_manager_has_busy_timeout -v
```
Expected: `FAILED`

- [ ] **Step 3: Add WAL + busy_timeout to `_connect`**

In `state_manager.py`, replace `_connect` (lines 22–25):

```python
# Before:
def _connect(self) -> sqlite3.Connection:
    connection = sqlite3.connect(self.db_path)
    connection.row_factory = sqlite3.Row
    return connection

# After:
def _connect(self) -> sqlite3.Connection:
    connection = sqlite3.connect(self.db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection
```

- [ ] **Step 4: Run test to confirm pass**

```
pytest tests/unit/test_state_manager.py::test_state_manager_uses_wal_mode tests/unit/test_state_manager.py::test_state_manager_has_busy_timeout -v
```
Expected: `PASSED`

- [ ] **Step 5: Run full suite**

```
pytest -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/state_manager.py tests/unit/test_state_manager.py
git commit -m "fix: enable SQLite WAL mode and 5s busy_timeout to prevent lock errors"
```

---

## Task 5: Fix TOCTOU in `acquire_worker_lock` — atomic exclusive transaction

**Problem:** SELECT + INSERT are separate operations; two processes can both see `row IS NULL` and then clash on the UNIQUE constraint with an unhandled `OperationalError`.

**Files:**
- Modify: `src/mail_ai_agent/state_manager.py:101-135`
- Test: `tests/unit/test_state_manager.py`

- [ ] **Step 1: Write test for concurrent lock behavior**

```python
# tests/unit/test_state_manager.py  (add to existing file)
def test_worker_lock_acquired_once_by_first_caller(tmp_path: Path):
    from mail_ai_agent.state_manager import StateManager

    sm = StateManager(tmp_path / "state.sqlite")

    result1 = sm.acquire_worker_lock(worker_id="w1", lease_seconds=60)
    result2 = sm.acquire_worker_lock(worker_id="w2", lease_seconds=60)

    assert result1.acquired is True
    assert result2.acquired is False
    assert result2.lock_owner == "w1"


def test_worker_lock_insert_does_not_raise_on_race(tmp_path: Path):
    """Simulate two StateManager instances hitting the same empty DB simultaneously."""
    from mail_ai_agent.state_manager import StateManager

    sm1 = StateManager(tmp_path / "state.sqlite")
    sm2 = StateManager(tmp_path / "state.sqlite")

    # Both try; must not raise OperationalError
    r1 = sm1.acquire_worker_lock(worker_id="w1", lease_seconds=60)
    r2 = sm2.acquire_worker_lock(worker_id="w2", lease_seconds=60)

    acquired = [r for r in [r1, r2] if r.acquired]
    denied = [r for r in [r1, r2] if not r.acquired]
    assert len(acquired) == 1
    assert len(denied) == 1
```

- [ ] **Step 2: Run tests to confirm current behavior (second test may not catch race reliably)**

```
pytest tests/unit/test_state_manager.py::test_worker_lock_acquired_once_by_first_caller -v
```
Expected: `PASSED` (sequential, no race — this one may already pass).

- [ ] **Step 3: Wrap lock acquisition in `BEGIN EXCLUSIVE` transaction**

In `state_manager.py`, replace `acquire_worker_lock` (lines 101–135):

```python
def acquire_worker_lock(self, *, worker_id: str, lease_seconds: int) -> WorkerLockResult:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    with self._connect() as conn:
        conn.execute("BEGIN EXCLUSIVE")
        row = conn.execute(
            "SELECT * FROM worker_runtime_lock WHERE lock_name = ?",
            ("main",),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO worker_runtime_lock (lock_name, lock_owner, lock_expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                ("main", worker_id, expires_at.isoformat(), now.isoformat()),
            )
            return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="worker lock acquired")

        current_expires_at = datetime.fromisoformat(row["lock_expires_at"])
        if current_expires_at > now and row["lock_owner"] != worker_id:
            return WorkerLockResult(
                acquired=False,
                lock_owner=row["lock_owner"],
                reason="another worker holds the active lock",
            )

        conn.execute(
            """
            UPDATE worker_runtime_lock
            SET lock_owner = ?, lock_expires_at = ?, updated_at = ?
            WHERE lock_name = ?
            """,
            (worker_id, expires_at.isoformat(), now.isoformat(), "main"),
        )
        return WorkerLockResult(acquired=True, lock_owner=worker_id, reason="worker lock refreshed")
```

Note: `conn.execute("BEGIN EXCLUSIVE")` inside `with conn:` works because Python's `sqlite3` in WAL mode allows explicit transaction control; `with conn:` commits or rolls back on exit.

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_state_manager.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/state_manager.py tests/unit/test_state_manager.py
git commit -m "fix: use BEGIN EXCLUSIVE in acquire_worker_lock to prevent TOCTOU race"
```

---

## Task 6: Security — file permissions hardening

**Problems:**
1. `manifest_secrets_cli.py:64` — sidecar `.sh` file written with default 0o644
2. `maintenance.py:55` — archive file after log rotation missing 0o600

**Files:**
- Modify: `src/mail_ai_agent/manifest_secrets_cli.py:63-64`
- Modify: `src/mail_ai_agent/maintenance.py:54-56`
- Test: `tests/unit/test_manifest_secrets_cli.py`
- Test: `tests/unit/test_maintenance.py`

- [ ] **Step 1: Write failing test for manifest_secrets_cli permissions**

```python
# tests/unit/test_manifest_secrets_cli.py  (add to existing file)
import json
import os
import stat
from pathlib import Path


def test_sidecar_file_has_restricted_permissions(tmp_path: Path):
    input_manifest = tmp_path / "manifest.json"
    output_manifest = tmp_path / "manifest.out.json"
    sidecar = tmp_path / "secrets.sh"

    input_manifest.write_text(
        json.dumps({"mailboxes": [{"imap_user": "u@example.com", "imap_pass": "secret", "imap_host": "imap.example.com"}]}),
        encoding="utf-8",
    )

    import sys
    from unittest.mock import patch

    with patch.object(
        sys, "argv",
        ["manifest_secrets_cli", "--input", str(input_manifest), "--output", str(output_manifest),
         "--mode", "env", "--sidecar-output", str(sidecar)],
    ):
        from mail_ai_agent.manifest_secrets_cli import main
        main()

    mode = stat.S_IMODE(os.stat(sidecar).st_mode)
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_manifest_secrets_cli.py::test_sidecar_file_has_restricted_permissions -v
```
Expected: `FAILED — AssertionError: Expected 0o600, got 0o644`

- [ ] **Step 3: Fix `manifest_secrets_cli.py`**

Add import at top of file:
```python
import os
```

Replace the sidecar write block (lines 63–64):
```python
# Before:
    if args.sidecar_output:
        Path(args.sidecar_output).write_text("\n".join(sidecar_lines) + ("\n" if sidecar_lines else ""), encoding="utf-8")

# After:
    if args.sidecar_output:
        sidecar_path = Path(args.sidecar_output)
        sidecar_path.write_text("\n".join(sidecar_lines) + ("\n" if sidecar_lines else ""), encoding="utf-8")
        try:
            os.chmod(sidecar_path, 0o600)
        except OSError:
            pass
```

- [ ] **Step 4: Write failing test for maintenance archive permissions**

```python
# tests/unit/test_maintenance.py  (add to existing file)
import os
import stat
from pathlib import Path


def test_rotated_archive_has_restricted_permissions(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    # Write more than max_bytes so rotation triggers
    log_path.write_text("x" * 200, encoding="utf-8")

    from mail_ai_agent.maintenance import rotate_audit_log
    result = rotate_audit_log(log_path, max_bytes=100)

    assert result.rotated is True
    assert result.archive_path is not None
    mode = stat.S_IMODE(os.stat(result.archive_path).st_mode)
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"
```

- [ ] **Step 5: Run test to confirm failure**

```
pytest tests/unit/test_maintenance.py::test_rotated_archive_has_restricted_permissions -v
```
Expected: `FAILED`

- [ ] **Step 6: Fix `maintenance.py` — chmod archive after copy**

In `maintenance.py`, replace the rotation section (lines 54–57):
```python
# Before:
    archive = path.with_suffix(path.suffix + ".1")
    shutil.copy2(path, archive)
    path.write_text("", encoding="utf-8")

# After:
    archive = path.with_suffix(path.suffix + ".1")
    shutil.copy2(path, archive)
    _chmod_owner_only(archive)
    path.write_text("", encoding="utf-8")
```

(The `_chmod_owner_only` import was already added in Task 1.)

- [ ] **Step 7: Run both tests**

```
pytest tests/unit/test_manifest_secrets_cli.py::test_sidecar_file_has_restricted_permissions \
       tests/unit/test_maintenance.py::test_rotated_archive_has_restricted_permissions -v
```
Expected: both `PASSED`

- [ ] **Step 8: Run full suite**

```
pytest -q
```

- [ ] **Step 9: Commit**

```bash
git add src/mail_ai_agent/manifest_secrets_cli.py \
        src/mail_ai_agent/maintenance.py \
        tests/unit/test_manifest_secrets_cli.py \
        tests/unit/test_maintenance.py
git commit -m "fix: set 0o600 on sidecar secrets file and audit log archive"
```

---

## Task 7: Security — `AuditLogger.log` fsync on write

**Problem:** `handle.write(...)` without `flush()` + `fsync()` — last entry may be lost on SIGKILL.

**Files:**
- Modify: `src/mail_ai_agent/audit_logger.py:25-26`
- Test: `tests/unit/test_audit_logger.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/test_audit_logger.py  (add to existing file)
import os
from pathlib import Path


def test_audit_log_content_survives_without_explicit_close(tmp_path: Path):
    """Verify data reaches disk even if process were to die after log() returns."""
    from mail_ai_agent.audit_logger import AuditLogger
    import json

    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path, redact_pii=False)
    logger.log(action="test", value="hello")

    # Read back immediately (without any buffer flush from our side)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["value"] == "hello"
```

(This test will pass even without fsync because Python closes the file handle on context manager exit — but the test documents the requirement. The real guarantee comes from the implementation.)

- [ ] **Step 2: Fix `audit_logger.py` — add flush + fsync**

In `audit_logger.py`, replace the `log` method body (lines 20–27):

```python
def log(self, **payload: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **self._sanitize_payload(payload),
    }
    with self.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _chmod_owner_only(self.path)
```

Also add `import os` (it was removed in Task 1 — re-add it since we now need `os.fsync`):
```python
import os
```

- [ ] **Step 3: Run tests**

```
pytest tests/unit/test_audit_logger.py -v
```
Expected: all pass.

- [ ] **Step 4: Run full suite**

```
pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add src/mail_ai_agent/audit_logger.py tests/unit/test_audit_logger.py
git commit -m "fix: flush and fsync audit log on every write to prevent data loss on SIGKILL"
```

---

## Task 8: Security — redact PII in `DraftStore.save` + validate config consistency

**Problems:**
1. `draft_store.py:27` — drafts always written with plaintext PII regardless of `state_redact_pii`
2. `config.py` — no check that `audit_redact_pii >= state_redact_pii` (can't be more restrictive in state than in audit)

**Files:**
- Modify: `src/mail_ai_agent/draft_store.py`
- Modify: `src/mail_ai_agent/config.py`
- Modify: `src/mail_ai_agent/main.py` (pass redact flag to DraftStore)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing test for draft PII redaction**

```python
# tests/unit/test_config.py  (add to existing file)
import pytest
from pydantic import ValidationError


def test_audit_less_restrictive_than_state_raises():
    """audit_redact_pii=False with state_redact_pii=True is inconsistent."""
    from mail_ai_agent.config import Settings

    with pytest.raises((ValueError, ValidationError)):
        Settings(
            IMAP_HOST="h",
            IMAP_USER="u",
            IMAP_PASS="p",
            AUDIT_REDACT_PII=False,
            STATE_REDACT_PII=True,
        )
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_config.py::test_audit_less_restrictive_than_state_raises -v
```
Expected: `FAILED`

- [ ] **Step 3: Add model validator to `Settings` in `config.py`**

Add import:
```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

Add after the `validate_imap_search_criterion` field validator in `Settings`:

```python
@model_validator(mode="after")
def validate_pii_flag_consistency(self) -> "Settings":
    if self.state_redact_pii and not self.audit_redact_pii:
        raise ValueError(
            "AUDIT_REDACT_PII must be True when STATE_REDACT_PII is True. "
            "The audit log cannot be less restrictive than the state DB."
        )
    return self
```

- [ ] **Step 4: Run test**

```
pytest tests/unit/test_config.py::test_audit_less_restrictive_than_state_raises -v
```
Expected: `PASSED`

- [ ] **Step 5: Add `redact_pii` parameter to `DraftStore.save`**

Modify `draft_store.py`:

```python
# Before:
def save(self, parsed_email: ParsedEmail, decision: FinalDecision, fingerprint: str) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed_email.subject or "no-subject").strip("-").lower()
    target = self.draft_dir / f"{slug[:40] or 'draft'}-{fingerprint[:8]}.json"
    payload = {
        "subject": parsed_email.subject,
        "sender": parsed_email.sender,
        "draft_reply": decision.draft_reply,
        "summary": decision.summary,
        "category": decision.category,
    }

# After:
def save(
    self,
    parsed_email: ParsedEmail,
    decision: FinalDecision,
    fingerprint: str,
    *,
    redact_pii: bool = False,
) -> Path:
    from .utils import _hash_value
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", parsed_email.subject or "no-subject").strip("-").lower()
    target = self.draft_dir / f"{slug[:40] or 'draft'}-{fingerprint[:8]}.json"
    subject = parsed_email.subject
    sender = parsed_email.sender
    payload: dict = {
        "subject": "[redacted]" if redact_pii and subject else subject,
        "sender": "[redacted]" if redact_pii and sender else sender,
        "draft_reply": decision.draft_reply,
        "summary": decision.summary,
        "category": decision.category,
    }
    if redact_pii:
        if subject:
            payload["subject_sha256"] = _hash_value(subject)
        if sender:
            payload["sender_sha256"] = _hash_value(sender)
```

- [ ] **Step 6: Update `DraftStore` call site in `main.py`**

Search for `drafts.save(` in `main.py`. There should be one or more calls. Add `redact_pii=settings.state_redact_pii`:

```python
# Before (example):
draft_path = drafts.save(parsed, decision, fingerprint)

# After:
draft_path = drafts.save(parsed, decision, fingerprint, redact_pii=settings.state_redact_pii)
```

Run `grep -n "drafts.save" src/mail_ai_agent/main.py` to find all call sites first.

- [ ] **Step 7: Run full suite**

```
pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/mail_ai_agent/draft_store.py \
        src/mail_ai_agent/config.py \
        src/mail_ai_agent/main.py \
        tests/unit/test_config.py
git commit -m "fix: redact PII in draft files when state_redact_pii=True; add config consistency check"
```

---

## Task 9: Fix `folder_mapper.py` — safe `.get()` with fallback

**Problem:** `mapping[category]` raises `KeyError` for unknown categories (e.g. future additions).

**Files:**
- Modify: `src/mail_ai_agent/folder_mapper.py:16`
- Test: `tests/unit/test_rule_engine.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_rule_engine.py  (add to existing file)
from mail_ai_agent.folder_mapper import category_to_folder
from mail_ai_agent.config import MailboxConfig
from pydantic import SecretStr


def _make_mailbox() -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="test",
        imap_host="imap.example.com",
        imap_user="u@example.com",
        imap_pass=SecretStr("secret"),
    )


def test_category_to_folder_unknown_returns_source_folder():
    mailbox = _make_mailbox()
    result = category_to_folder("some_future_category", mailbox)
    assert result == mailbox.imap_source_folder
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_rule_engine.py::test_category_to_folder_unknown_returns_source_folder -v
```
Expected: `FAILED — KeyError: 'some_future_category'`

- [ ] **Step 3: Fix `folder_mapper.py`**

```python
# Before:
def category_to_folder(category: str, mailbox: MailboxConfig) -> str:
    mapping = {
        "appointment": mailbox.imap_appointments_folder,
        "question": mailbox.imap_questions_folder,
        "complaint": mailbox.imap_complaints_folder,
        "spam_or_offer": mailbox.imap_other_folder,
        "other": mailbox.imap_other_folder,
        "billing": mailbox.imap_billing_folder,
        "system": mailbox.imap_system_folder,
    }
    return mapping[category]

# After:
def category_to_folder(category: str, mailbox: MailboxConfig) -> str:
    mapping = {
        "appointment": mailbox.imap_appointments_folder,
        "question": mailbox.imap_questions_folder,
        "complaint": mailbox.imap_complaints_folder,
        "spam_or_offer": mailbox.imap_other_folder,
        "other": mailbox.imap_other_folder,
        "billing": mailbox.imap_billing_folder,
        "system": mailbox.imap_system_folder,
    }
    return mapping.get(category, mailbox.imap_source_folder)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_rule_engine.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/folder_mapper.py tests/unit/test_rule_engine.py
git commit -m "fix: folder_mapper falls back to source_folder for unknown categories (no KeyError)"
```

---

## Task 10: Fix `cleanup_cli.py` — per-record error handling

**Problem:** If one `delete_message` raises, the loop aborts — remaining records are never cleaned.

**Files:**
- Modify: `src/mail_ai_agent/cleanup_cli.py:53-59`
- Test: `tests/unit/test_cleanup_cli.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_cleanup_cli.py  (add to existing file)
def test_cleanup_continues_after_single_delete_failure(tmp_path):
    """If record 1 fails to delete, records 2 and 3 should still be processed."""
    from mail_ai_agent.state_manager import StateManager
    from mail_ai_agent.schemas import WorkflowStatus
    # ... setup requires integration with full stack, use existing test pattern
    # This test verifies cleanup_cli does per-record try/except
    # See existing test fixtures in the file for how to set up state + fake IMAP
    pass  # replace with full test using existing helpers in that file
```

**Note:** Read the existing `test_cleanup_cli.py` to understand the fixture pattern, then write the test using the same approach. The test should:
1. Create 3 `CLEANUP_PENDING` records in state DB
2. Mock `IMAPClient.delete_message` to raise on UID of record 1 but succeed for records 2 and 3
3. Call `cleanup_cli.main()` with `--apply`
4. Assert records 2 and 3 are marked `PROCESSED`, record 1 is still `CLEANUP_PENDING`

- [ ] **Step 2: Run test to confirm failure (records 2 and 3 not cleaned)**

```
pytest tests/unit/test_cleanup_cli.py -v -k "partial"
```

- [ ] **Step 3: Wrap delete + mark in per-record try/except**

In `cleanup_cli.py`, replace lines 53–59:

```python
# Before:
        cleaned_record_ids: list[int] = []
        for record in candidates:
            if not record.imap_uid:
                continue
            imap.delete_message(mailbox.imap_source_folder, record.imap_uid)
            cleaned_record_ids.append(record.id)
        for record_id in cleaned_record_ids:
            state.mark_cleanup_done(record_id)

# After:
        failed_uids: list[str] = []
        for record in candidates:
            if not record.imap_uid:
                continue
            try:
                imap.delete_message(mailbox.imap_source_folder, record.imap_uid)
                state.mark_cleanup_done(record.id)
            except Exception as exc:
                failed_uids.append(record.imap_uid)
                import sys
                print(
                    f"[WARN] Failed to clean UID {record.imap_uid}: {exc}",
                    file=sys.stderr,
                )
        if failed_uids:
            payload["failed_uids"] = failed_uids
```

Also update the imports at top of the file: add `import sys` if not present.

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_cleanup_cli.py -v
```
Expected: all pass including the new partial-failure test.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/cleanup_cli.py tests/unit/test_cleanup_cli.py
git commit -m "fix: cleanup_cli processes remaining records after individual delete failure"
```

---

## Task 11: Fix `config.py` — `or` → `is not None` in `_normalize_mailbox`

**Problem:** `raw.get("imap_port") or self.imap_port` silently ignores `0` and `False` values from the manifest.

**Files:**
- Modify: `src/mail_ai_agent/config.py:175-192`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_config.py  (add to existing file)
def test_normalize_mailbox_respects_zero_fetch_limit(tmp_path):
    """imap_fetch_limit=0 in manifest must not be overridden by global default."""
    import json
    from mail_ai_agent.config import Settings

    manifest = tmp_path / "mailboxes.json"
    manifest.write_text(json.dumps([{
        "imap_user": "u@example.com",
        "imap_pass": "secret",
        "imap_host": "imap.example.com",
        "imap_fetch_limit": 0,
    }]), encoding="utf-8")

    settings = Settings(IMAP_HOST="fallback.example.com", MAILBOXES_CONFIG_PATH=str(manifest))
    mailboxes = settings.load_mailboxes()

    assert mailboxes[0].imap_fetch_limit == 0, (
        f"Expected 0 from manifest, got {mailboxes[0].imap_fetch_limit} (global default leaked in)"
    )
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_config.py::test_normalize_mailbox_respects_zero_fetch_limit -v
```
Expected: `FAILED — AssertionError: Expected 0 from manifest, got 100`

- [ ] **Step 3: Fix `_normalize_mailbox` — replace `or` with `is not None` checks**

In `config.py`, replace the `merged` dict construction in `_normalize_mailbox` (lines 173–192):

```python
def _normalize_mailbox(self, raw_mailbox: dict[str, Any]) -> MailboxConfig:
    if "imap_user" not in raw_mailbox:
        raise ValueError("Each mailbox entry must include imap_user.")
    if "imap_pass" not in raw_mailbox and "imap_pass_ref" not in raw_mailbox:
        raise ValueError("Each mailbox entry must include imap_pass or imap_pass_ref.")
    mailbox_user = str(raw_mailbox["imap_user"])

    def _get(key: str, default: Any) -> Any:
        value = raw_mailbox.get(key)
        return value if value is not None else default

    merged = {
        "mailbox_id": raw_mailbox.get("mailbox_id") or _default_mailbox_id(mailbox_user),
        "imap_host": _get("imap_host", self.imap_host),
        "imap_port": _get("imap_port", self.imap_port),
        "imap_user": mailbox_user,
        "imap_pass": _resolve_mailbox_secret(raw_mailbox, mailbox_user),
        "imap_max_retries": _get("imap_max_retries", self.imap_max_retries),
        "imap_retry_backoff_seconds": _get("imap_retry_backoff_seconds", self.imap_retry_backoff_seconds),
        "imap_search_criterion": _get("imap_search_criterion", self.imap_search_criterion),
        "imap_fetch_limit": _get("imap_fetch_limit", self.imap_fetch_limit),
        "imap_allow_folder_expunge": raw_mailbox.get("imap_allow_folder_expunge", self.imap_allow_folder_expunge),
        "imap_source_folder": _get("imap_source_folder", self.imap_source_folder),
        "imap_uncertain_folder": _get("imap_uncertain_folder", self.imap_uncertain_folder),
        "imap_appointments_folder": _get("imap_appointments_folder", self.imap_appointments_folder),
        "imap_questions_folder": _get("imap_questions_folder", self.imap_questions_folder),
        "imap_complaints_folder": _get("imap_complaints_folder", self.imap_complaints_folder),
        "imap_other_folder": _get("imap_other_folder", self.imap_other_folder),
        "imap_billing_folder": _get("imap_billing_folder", self.imap_billing_folder),
        "imap_system_folder": _get("imap_system_folder", self.imap_system_folder),
    }
    if not merged["imap_host"]:
        raise ValueError(f"Mailbox '{merged['mailbox_id']}' has no IMAP host configured.")
    return MailboxConfig.model_validate(merged)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_config.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/config.py tests/unit/test_config.py
git commit -m "fix: use is-not-None check when merging mailbox config to respect 0 and False overrides"
```

---

## Task 12: Add LLM retry backoff + move hardcoded email to config

**Problems:**
1. `llm_gateway.py` — retries without sleep (unlike `imap_client._run_with_retry`)
2. `rule_engine.py:20` — `platnosci@swiatlowodem.pl` hardcoded in source

**Files:**
- Modify: `src/mail_ai_agent/llm_gateway.py:62-84`
- Modify: `src/mail_ai_agent/config.py` (add `billing_payment_email` field)
- Modify: `src/mail_ai_agent/rule_engine.py:20`
- Test: `tests/unit/test_rule_engine.py`

- [ ] **Step 1: Add `billing_payment_email` to `MailboxConfig`**

In `config.py`, add to `MailboxConfig` (after `imap_system_folder`):

```python
billing_payment_email: str | None = Field(default=None)
```

Also add to `MailboxConfig.from_settings` and `_normalize_mailbox` — both should pass through `raw_mailbox.get("billing_payment_email")` and `settings.imap_billing_payment_email` respectively. Add `imap_billing_payment_email: str | None = Field(default=None, alias="IMAP_BILLING_PAYMENT_EMAIL")` to `Settings`.

Update `_normalize_mailbox`:
```python
"billing_payment_email": _get("billing_payment_email", self.imap_billing_payment_email),
```

Update `MailboxConfig.from_settings`:
```python
billing_payment_email=settings.imap_billing_payment_email,
```

- [ ] **Step 2: Write failing test**

```python
# tests/unit/test_rule_engine.py  (add)
from pydantic import SecretStr
from mail_ai_agent.config import MailboxConfig
from mail_ai_agent.schemas import ParsedEmail
from mail_ai_agent.rule_engine import evaluate_rules


def _make_mailbox_for_rule_tests(billing_email: str | None = None) -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="test",
        imap_host="imap.example.com",
        imap_user="u@example.com",
        imap_pass=SecretStr("secret"),
        billing_payment_email=billing_email,
    )


def test_billing_email_from_config_triggers_billing_rule():
    mailbox = _make_mailbox_for_rule_tests(billing_email="custom-billing@mycompany.com")
    parsed = ParsedEmail(
        message_id=None,
        sender="custom-billing@mycompany.com",
        subject="rozliczenie",
        plain_text_body="",
        normalized_body="rozliczenie custom-billing@mycompany.com",
        date=None,
    )
    decision = evaluate_rules(parsed, mailbox)
    assert decision.category == "billing"
```

- [ ] **Step 3: Run test to confirm failure**

```
pytest tests/unit/test_rule_engine.py::test_billing_email_from_config_triggers_billing_rule -v
```
Expected: `FAILED` (hardcoded email not in config yet)

- [ ] **Step 4: Update `PAYMENT_REGEX` in `rule_engine.py` to use config field**

The regex is currently a module-level constant. Change `evaluate_rules` to build the billing check dynamically:

```python
# Before (rule_engine.py):
PAYMENT_REGEX = re.compile(
    r"\b("
    r"..."
    r"platnosci@swiatlowodem\.pl|"
    r"obslugaplatnosci"
    r")\b",
    flags=re.IGNORECASE,
)

# After — remove the two hardcoded address lines from PAYMENT_REGEX:
PAYMENT_REGEX = re.compile(
    r"\b("
    r"płatno(?:ść|ści|sci|scią|sci[aą])|"
    r"termin(?:ie)? płatno(?:ści|sci)|"
    r"brak płatno(?:ści|sci)|"
    r"przypomnienie o terminie płatno(?:ści|sci)|"
    r"rozliczen\w*|"
    r"rachun\w*|"
    r"opłat\w*|"
    r"należno(?:ść|ści|sci)"
    r")\b",
    flags=re.IGNORECASE,
)
```

Add a helper that builds an optional email pattern check:

```python
def _payment_regex_for_mailbox(mailbox: MailboxConfig) -> re.Pattern | None:
    if not mailbox.billing_payment_email:
        return None
    escaped = re.escape(mailbox.billing_payment_email)
    return re.compile(escaped, flags=re.IGNORECASE)
```

Update `evaluate_rules`:
```python
def evaluate_rules(parsed_email: ParsedEmail, mailbox: MailboxConfig) -> RuleDecision:
    subject = parsed_email.subject.lower()
    sender = parsed_email.sender.lower()
    body = parsed_email.normalized_body.lower()
    combined = " ".join([subject, sender, body])

    if any(keyword in subject for keyword in BILLING_KEYWORDS):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="billing keyword matched in subject",
        )

    if PAYMENT_REGEX.search(combined):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="payment or billing pattern matched",
        )

    billing_email_pattern = _payment_regex_for_mailbox(mailbox)
    if billing_email_pattern and billing_email_pattern.search(combined):
        return RuleDecision(
            category="billing",
            target_folder=category_to_folder("billing", mailbox),
            action="skip_ai",
            reason="billing payment email matched",
        )

    # ... rest unchanged
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_rule_engine.py -v
```

- [ ] **Step 6: Add LLM retry backoff in `llm_gateway.py`**

```python
# Before (llm_gateway.py lines 62-84):
        last_error: Exception | None = None
        for _ in range(self.settings.max_retries):
            started = time.perf_counter()
            try:
                ...
                return classification, latency_ms
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                continue

# After:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            started = time.perf_counter()
            try:
                ...
                return classification, latency_ms
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    time.sleep(0.5 * attempt)
                continue
```

- [ ] **Step 7: Run full suite**

```
pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/mail_ai_agent/config.py \
        src/mail_ai_agent/rule_engine.py \
        src/mail_ai_agent/llm_gateway.py \
        tests/unit/test_rule_engine.py
git commit -m "fix: move billing payment email to config; add LLM retry backoff"
```

Also update `.env.example` and `.env.multi.prod.example` to document the new field:
```bash
# In .env.example and .env.multi.prod.example, add:
# IMAP_BILLING_PAYMENT_EMAIL=platnosci@yourcompany.com
```

For existing deployments, add to `config/mailboxes.active.json`:
```json
"billing_payment_email": "platnosci@swiatlowodem.pl"
```

---

## Task 13: Batch IMAP FETCH (N round-trips → 1)

**Context:** UIDPLUS is NOT supported on the server. Batch FETCH does not require UIDPLUS — it uses standard `UID FETCH uid1,uid2,...` syntax. Only `UID EXPUNGE` (per-message targeted expunge) requires UIDPLUS. The existing delete logic using folder-level expunge is unchanged.

**Files:**
- Modify: `src/mail_ai_agent/imap_client.py:120-159`
- Test: `tests/unit/test_imap_client.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_imap_client.py  (add to existing file)
from unittest.mock import MagicMock, patch


def test_fetch_candidates_uses_single_batch_command():
    """Verify that fetching N UIDs sends exactly 1 FETCH command, not N."""
    from mail_ai_agent.imap_client import IMAPClient
    from pydantic import SecretStr
    from mail_ai_agent.config import MailboxConfig

    mailbox = MailboxConfig(
        mailbox_id="test",
        imap_host="imap.example.com",
        imap_user="u@example.com",
        imap_pass=SecretStr("secret"),
        imap_fetch_limit=3,
    )
    client = IMAPClient(mailbox)
    mock_conn = MagicMock()
    client.connection = mock_conn

    # Simulate UID SEARCH returning 3 UIDs
    mock_conn.select.return_value = ("OK", [b"3"])
    mock_conn.response.return_value = (None, [b"12345"])
    mock_conn.uid.side_effect = [
        ("OK", [b"1 2 3"]),   # SEARCH call
        ("OK", _make_batch_fetch_response()),  # single FETCH call
    ]

    client.fetch_candidates("INBOX.Test")

    # uid() should be called exactly twice: once for SEARCH, once for batch FETCH
    assert mock_conn.uid.call_count == 2
    fetch_call_args = mock_conn.uid.call_args_list[1]
    assert fetch_call_args[0][0] == "fetch"
    # The UID arg should be a comma-separated set, not a single UID
    uid_arg = fetch_call_args[0][1]
    assert "," in str(uid_arg), f"Expected batch UID set, got: {uid_arg}"


def _make_batch_fetch_response():
    """Minimal IMAP FETCH response for 3 messages."""
    import email
    msg = email.message.EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<t@t.com>"
    msg.set_content("hello")
    raw = msg.as_bytes()
    return [
        (b'1 (UID 1 INTERNALDATE "01-Jan-2024 00:00:00 +0000" BODY[] {' + str(len(raw)).encode() + b'}', raw),
        b')',
        (b'2 (UID 2 INTERNALDATE "01-Jan-2024 00:00:00 +0000" BODY[] {' + str(len(raw)).encode() + b'}', raw),
        b')',
        (b'3 (UID 3 INTERNALDATE "01-Jan-2024 00:00:00 +0000" BODY[] {' + str(len(raw)).encode() + b'}', raw),
        b')',
    ]
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_imap_client.py::test_fetch_candidates_uses_single_batch_command -v
```
Expected: `FAILED — AssertionError: Expected batch UID set`

- [ ] **Step 3: Rewrite `fetch_candidates` with batch fetch**

In `imap_client.py`, replace `fetch_candidates` (lines 120–159):

```python
def fetch_candidates(self, folder: str) -> list[CandidateMessage]:
    def _fetch() -> list[CandidateMessage]:
        assert self.connection is not None
        status, _ = self.connection.select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}")
        uidvalidity = self._get_uidvalidity()

        search_tokens = self.mailbox.imap_search_criterion.split()
        status, data = self.connection.uid("search", None, *search_tokens)
        if status != "OK":
            raise RuntimeError("Unable to search folder")

        all_uids = data[0].split()
        if self.mailbox.imap_fetch_limit > 0:
            all_uids = all_uids[-self.mailbox.imap_fetch_limit:]
        if not all_uids:
            return []

        uid_set = b",".join(all_uids).decode()
        status, fetched = self.connection.uid("fetch", uid_set, "(UID BODY.PEEK[] INTERNALDATE)")
        if status != "OK":
            raise RuntimeError("Unable to batch-fetch messages")

        return list(_parse_batch_fetch_response(fetched, uidvalidity))

    return self._run_with_retry("fetch_candidates", _fetch)
```

Add the helper function to `imap_client.py` (outside the class):

```python
import re as _re

_UID_RE = _re.compile(r"\bUID\s+(\d+)\b", _re.IGNORECASE)
_INTERNALDATE_RE = _re.compile(r'INTERNALDATE\s+"([^"]+)"', _re.IGNORECASE)


def _parse_batch_fetch_response(
    fetched: list,
    uidvalidity: str | None,
) -> "Generator[CandidateMessage, None, None]":
    from typing import Generator
    for item in fetched:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        metadata_raw = item[0]
        raw_bytes = item[1]
        if not isinstance(raw_bytes, bytes):
            continue
        metadata = metadata_raw.decode("utf-8", errors="ignore") if isinstance(metadata_raw, bytes) else str(metadata_raw)

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
```

Also add `from typing import Generator` to imports (or use `collections.abc.Generator`).

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_imap_client.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/mail_ai_agent/imap_client.py tests/unit/test_imap_client.py
git commit -m "perf: batch IMAP FETCH — replace N round-trips with single UID FETCH uid1,uid2,..."
```

---

## Task 14: Admin email notification — UIDPLUS consultation

**Context:** Server does not support UIDPLUS. The cleanup pass currently uses folder-level expunge (guarded by pre-existing deleted message check). This is safe as long as `INBOX.AI-Review` is exclusively owned by this worker. Send email to admins requesting UIDPLUS enablement.

**Files:**
- Create: `src/mail_ai_agent/smtp_notifier.py`
- Create: `src/mail_ai_agent/admin_notify_cli.py`
- Modify: `src/mail_ai_agent/config.py` (add SMTP fields)
- Test: `tests/unit/test_admin_notify_cli.py`

- [ ] **Step 1: Add SMTP settings to `config.py`**

Add to `Settings` class in `config.py`:

```python
smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
smtp_port: int = Field(default=587, alias="SMTP_PORT")
smtp_user: str | None = Field(default=None, alias="SMTP_USER")
smtp_pass: SecretStr | None = Field(default=None, alias="SMTP_PASS")
smtp_from: str | None = Field(default=None, alias="SMTP_FROM")
admin_notify_email: str | None = Field(default=None, alias="ADMIN_NOTIFY_EMAIL")
```

- [ ] **Step 2: Create `smtp_notifier.py`**

```python
# src/mail_ai_agent/smtp_notifier.py
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Settings


def send_admin_email(settings: Settings, *, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise ValueError("SMTP_HOST is not configured.")
    if not settings.smtp_user:
        raise ValueError("SMTP_USER is not configured.")
    if not settings.smtp_pass:
        raise ValueError("SMTP_PASS is not configured.")
    if not settings.admin_notify_email:
        raise ValueError("ADMIN_NOTIFY_EMAIL is not configured.")

    from_addr = settings.smtp_from or settings.smtp_user
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = settings.admin_notify_email
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass.get_secret_value())
        smtp.send_message(msg)
```

- [ ] **Step 3: Create `admin_notify_cli.py`**

```python
# src/mail_ai_agent/admin_notify_cli.py
from __future__ import annotations

import argparse
import textwrap

from .config import Settings
from .imap_client import IMAPClient
from .smtp_notifier import send_admin_email

UIDPLUS_NOTIFICATION_SUBJECT = "[mAIl] IMAP UIDPLUS not supported — admin consultation required"

UIDPLUS_NOTIFICATION_BODY = textwrap.dedent("""\
    Hello,

    This is an automated notification from the mAIl AI email routing system.

    FINDING: The IMAP server does not advertise the UIDPLUS capability.

    WHAT IS UIDPLUS?
    UIDPLUS (RFC 4315) is an IMAP extension that enables targeted per-message
    expunge: the server can delete exactly one specific message (by UID) without
    affecting any other messages in the folder that may also be flagged as deleted.

    CURRENT WORKAROUND IN PLACE:
    Without UIDPLUS, the system uses folder-level EXPUNGE. Before doing so, it:
    1. Verifies that no other messages in the source folder are already flagged
       as \\Deleted.
    2. Only proceeds if the target set of \\Deleted messages is exactly the one
       message we want to remove.

    This is safe as long as INBOX.AI-Review (or equivalent) is exclusively managed
    by the mAIl worker and no other process flags messages there as deleted.

    ACTION REQUIRED:
    Please contact your IMAP server administrator or hosting provider and request
    that the UIDPLUS extension be enabled. For Dovecot, this is enabled by default
    in most configurations (check /etc/dovecot/conf.d/20-imap.conf for
    `imap_capabilities`). For other servers, consult their documentation.

    Once UIDPLUS is enabled, the mAIl system will automatically detect and use it,
    eliminating the need for folder-level expunge.

    IMAP server checked: {imap_host}
    IMAP user: {imap_user}
    Worker ID: {worker_id}

    Regards,
    mAIl automated monitoring
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send admin notification about IMAP UIDPLUS support status")
    parser.add_argument("--env-file", default=None, help="Optional .env file path")
    parser.add_argument("--dry-run", action="store_true", help="Print email to stdout instead of sending")
    args = parser.parse_args()

    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    mailboxes = settings.load_mailboxes()

    results: list[dict] = []
    for mailbox in mailboxes:
        with IMAPClient(mailbox) as imap:
            uidplus_supported = imap.supports_uidplus()

        if uidplus_supported:
            results.append({
                "mailbox_id": mailbox.mailbox_id,
                "uidplus": True,
                "action": "none",
            })
            continue

        body = UIDPLUS_NOTIFICATION_BODY.format(
            imap_host=mailbox.imap_host,
            imap_user=mailbox.imap_user,
            worker_id=settings.worker_id,
        )

        if args.dry_run:
            print(f"--- DRY RUN: would send to {settings.admin_notify_email} ---")
            print(f"Subject: {UIDPLUS_NOTIFICATION_SUBJECT}")
            print()
            print(body)
            results.append({"mailbox_id": mailbox.mailbox_id, "uidplus": False, "action": "dry_run"})
        else:
            send_admin_email(
                settings,
                subject=UIDPLUS_NOTIFICATION_SUBJECT,
                body=body,
            )
            results.append({"mailbox_id": mailbox.mailbox_id, "uidplus": False, "action": "email_sent"})

    import json
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write tests**

```python
# tests/unit/test_admin_notify_cli.py
from unittest.mock import MagicMock, patch


def test_dry_run_prints_email(tmp_path, capsys):
    import sys
    from mail_ai_agent.config import Settings

    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="u@example.com",
        IMAP_PASS="secret",
        ADMIN_NOTIFY_EMAIL="admin@example.com",
        WORKER_ID="test-worker",
    )

    with patch("mail_ai_agent.admin_notify_cli.Settings", return_value=settings), \
         patch("mail_ai_agent.admin_notify_cli.IMAPClient") as mock_imap_cls:
        mock_imap = MagicMock()
        mock_imap.supports_uidplus.return_value = False
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=None)

        with patch.object(sys, "argv", ["admin_notify_cli", "--dry-run"]):
            from mail_ai_agent.admin_notify_cli import main
            main()

    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "UIDPLUS" in captured.out
    assert "imap.example.com" in captured.out


def test_uidplus_supported_skips_notification():
    import sys
    from mail_ai_agent.config import Settings

    settings = Settings(
        IMAP_HOST="imap.example.com",
        IMAP_USER="u@example.com",
        IMAP_PASS="secret",
        ADMIN_NOTIFY_EMAIL="admin@example.com",
    )

    with patch("mail_ai_agent.admin_notify_cli.Settings", return_value=settings), \
         patch("mail_ai_agent.admin_notify_cli.IMAPClient") as mock_imap_cls, \
         patch("mail_ai_agent.admin_notify_cli.send_admin_email") as mock_send:
        mock_imap = MagicMock()
        mock_imap.supports_uidplus.return_value = True
        mock_imap_cls.return_value.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap_cls.return_value.__exit__ = MagicMock(return_value=None)

        with patch.object(sys, "argv", ["admin_notify_cli"]):
            from mail_ai_agent.admin_notify_cli import main
            main()

    mock_send.assert_not_called()
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_admin_notify_cli.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Register CLI entry point in `pyproject.toml`**

Add to `[project.scripts]` in `pyproject.toml`:
```toml
mail-ai-admin-notify = "mail_ai_agent.admin_notify_cli:main"
```

- [ ] **Step 7: Update `.env.multi.prod.example` with SMTP fields**

```bash
# Add to .env.multi.prod.example:
# SMTP_HOST=smtp.yourdomain.com
# SMTP_PORT=587
# SMTP_USER=notifications@yourdomain.com
# SMTP_PASS=your-smtp-password
# SMTP_FROM=mAIl-system@yourdomain.com
# ADMIN_NOTIFY_EMAIL=admin@yourdomain.com
```

- [ ] **Step 8: Run full suite**

```
pytest -q
```

- [ ] **Step 9: Send notification (manual step for operator)**

```bash
.venv/bin/python -m mail_ai_agent.admin_notify_cli --env-file .env.multi.prod --dry-run
# Review output, then:
.venv/bin/python -m mail_ai_agent.admin_notify_cli --env-file .env.multi.prod
```

- [ ] **Step 10: Commit**

```bash
git add src/mail_ai_agent/smtp_notifier.py \
        src/mail_ai_agent/admin_notify_cli.py \
        src/mail_ai_agent/config.py \
        tests/unit/test_admin_notify_cli.py \
        pyproject.toml \
        .env.multi.prod.example
git commit -m "feat: add admin_notify_cli to send UIDPLUS consultation email to admins"
```

---

## Task 15: Performance — batch `scrub_state_pii` + streaming `export_state_csv`

**Files:**
- Modify: `src/mail_ai_agent/maintenance.py:90-117`
- Modify: `src/mail_ai_agent/reporting.py:58-73`
- Test: `tests/unit/test_maintenance.py`
- Test: `tests/unit/test_reporting.py` (if exists, else inline)

- [ ] **Step 1: Write test for batch scrub**

```python
# tests/unit/test_maintenance.py  (add)
import sqlite3
from pathlib import Path


def test_scrub_state_pii_issues_single_update(tmp_path: Path, monkeypatch):
    """scrub_state_pii should issue a batch UPDATE, not N individual updates."""
    from mail_ai_agent.state_manager import StateManager
    from mail_ai_agent.maintenance import scrub_state_pii

    db_path = tmp_path / "state.sqlite"
    sm = StateManager(db_path)
    # Insert 3 records with PII
    for i in range(3):
        sm.acquire_lease(
            mailbox_id="test",
            message_id=f"<msg{i}@test.com>",
            fingerprint=f"fp{i}",
            imap_uid=str(i),
            sender=f"sender{i}@example.com",
            subject=f"Subject {i}",
            source_folder="INBOX",
            internaldate=None,
            worker_id="w",
            lease_seconds=60,
            max_retries=3,
        )

    execute_calls: list = []
    original_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        original_execute = conn.execute
        def tracked_execute(sql, *a, **k):
            if sql.strip().upper().startswith("UPDATE"):
                execute_calls.append(sql)
            return original_execute(sql, *a, **k)
        conn.execute = tracked_execute
        return conn

    monkeypatch.setattr(sqlite3, "connect", counting_connect)
    scrub_state_pii(db_path)

    update_count = len([s for s in execute_calls if "email_processing_state" in s])
    assert update_count == 1, f"Expected 1 batch UPDATE, got {update_count} UPDATE calls"
```

- [ ] **Step 2: Run test to confirm failure**

```
pytest tests/unit/test_maintenance.py::test_scrub_state_pii_issues_single_update -v
```
Expected: `FAILED — Expected 1 batch UPDATE, got 3`

- [ ] **Step 3: Replace row-by-row scrub with batch UPDATE**

In `maintenance.py`, replace `scrub_state_pii` (lines 90–117):

```python
def scrub_state_pii(db_path: Path) -> StateScrubResult:
    if not db_path.exists():
        return StateScrubResult(updated_rows=0)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # First pass: fill in missing sha256 values for rows that have PII but no hash
        rows_needing_hash = conn.execute(
            """
            SELECT id, sender, subject
            FROM email_processing_state
            WHERE (sender IS NOT NULL AND sender != '' AND sender != '[redacted]' AND sender_sha256 IS NULL)
               OR (subject IS NOT NULL AND subject != '' AND subject != '[redacted]' AND subject_sha256 IS NULL)
            """
        ).fetchall()
        for row in rows_needing_hash:
            conn.execute(
                "UPDATE email_processing_state SET sender_sha256 = ?, subject_sha256 = ? WHERE id = ?",
                (
                    _hash_value(row["sender"]) if row["sender"] not in (None, "", "[redacted]") else row["sender"],
                    _hash_value(row["subject"]) if row["subject"] not in (None, "", "[redacted]") else row["subject"],
                    row["id"],
                ),
            )

        # Second pass: batch redact all unredacted PII
        cursor = conn.execute(
            """
            UPDATE email_processing_state
            SET sender = '[redacted]', subject = '[redacted]'
            WHERE (sender IS NOT NULL AND sender != '' AND sender != '[redacted]')
               OR (subject IS NOT NULL AND subject != '' AND subject != '[redacted]')
            """
        )
        updated_rows = cursor.rowcount

    return StateScrubResult(updated_rows=updated_rows)
```

- [ ] **Step 4: Fix `export_state_csv` to use streaming cursor**

In `reporting.py`, replace `export_state_csv` (lines 58–73):

```python
def export_state_csv(db_path: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM email_processing_state ORDER BY id")
        fieldnames: list[str] | None = None
        row_count = 0
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = None
            for row in cursor:
                if fieldnames is None:
                    fieldnames = list(row.keys())
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                assert writer is not None
                writer.writerow(dict(row))
                row_count += 1
        if row_count == 0:
            destination.write_text("", encoding="utf-8")
    return row_count
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_maintenance.py tests/unit/test_cleanup_and_review.py -v
```
Expected: all pass.

- [ ] **Step 6: Run full suite**

```
pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/mail_ai_agent/maintenance.py src/mail_ai_agent/reporting.py \
        tests/unit/test_maintenance.py
git commit -m "perf: batch-UPDATE scrub_state_pii; stream export_state_csv to avoid full table load"
```

---

## Task 16: Fix class-level state in `FakeIMAPClient` test fixtures

**Problem:** `copied`, `flagged`, `deleted`, `validated` are class-level attributes — shared across test instances, causing flakey ordering-dependent failures.

**Files:**
- Modify: `tests/unit/test_main_workflow.py:26-63`

- [ ] **Step 1: Run tests twice in different order to observe flakiness**

```
pytest tests/unit/test_main_workflow.py -v --randomly-seed=12345
pytest tests/unit/test_main_workflow.py -v --randomly-seed=99999
```
(Install `pytest-randomly` if needed: `.venv/bin/pip install pytest-randomly`)

- [ ] **Step 2: Fix `FakeIMAPClient` — move lists to `__init__`**

```python
# Before:
class FakeIMAPClient:
    copied: list[tuple[str, str, str]] = []
    flagged: list[tuple[str, str]] = []
    deleted: list[tuple[str, str]] = []
    validated: list[tuple[str, tuple[str, ...], bool]] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

# After:
class FakeIMAPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.copied: list[tuple[str, str, str]] = []
        self.flagged: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.validated: list[tuple[str, tuple[str, ...], bool]] = []
```

Do the same for `FakeMultiMailboxIMAPClient`:
```python
# Before:
class FakeMultiMailboxIMAPClient:
    copied: list[tuple[str, str, str]] = []

    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox

# After:
class FakeMultiMailboxIMAPClient:
    def __init__(self, mailbox) -> None:
        self.mailbox = mailbox
        self.copied: list[tuple[str, str, str]] = []
```

- [ ] **Step 3: Remove all manual `FakeIMAPClient.copied = []` resets in test functions**

Search for `FakeIMAPClient.copied = []`, `FakeIMAPClient.flagged = []` etc. in the file and remove them (they're no longer needed since state is per-instance now).

```
grep -n "FakeIMAPClient\." tests/unit/test_main_workflow.py
```

Remove all class-level reset lines.

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_main_workflow.py -v
```
Expected: all pass.

- [ ] **Step 5: Run full suite twice (different seeds)**

```
pytest -q --randomly-seed=12345
pytest -q --randomly-seed=99999
```
Expected: same results both times.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_main_workflow.py
git commit -m "fix: FakeIMAPClient uses instance-level state to eliminate flakey test ordering"
```

---

## Task 17: Fix `test_rule_engine.py` — use `MailboxConfig` not `Settings`

**Problem:** Tests pass `Settings` to `evaluate_rules()` which expects `MailboxConfig`. Works by accident (duck typing), but breaks if signatures diverge.

**Files:**
- Modify: `tests/unit/test_rule_engine.py`

- [ ] **Step 1: Read current test helpers in `test_rule_engine.py`**

```
grep -n "def make_settings\|def make_mailbox\|evaluate_rules" tests/unit/test_rule_engine.py
```

- [ ] **Step 2: Replace `Settings` with `MailboxConfig` in test helper**

```python
# Before (in test_rule_engine.py):
from mail_ai_agent.config import Settings

def make_settings() -> Settings:
    return Settings(IMAP_HOST="...", IMAP_USER="...", IMAP_PASS="...")

# usage:
evaluate_rules(parsed, make_settings())

# After:
from mail_ai_agent.config import MailboxConfig
from pydantic import SecretStr

def make_mailbox(billing_payment_email: str | None = None) -> MailboxConfig:
    return MailboxConfig(
        mailbox_id="test",
        imap_host="imap.example.com",
        imap_user="u@example.com",
        imap_pass=SecretStr("secret"),
        billing_payment_email=billing_payment_email,
    )

# usage:
evaluate_rules(parsed, make_mailbox())
```

Update all call sites in the test file.

- [ ] **Step 3: Run tests**

```
pytest tests/unit/test_rule_engine.py -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_rule_engine.py
git commit -m "fix: test_rule_engine passes MailboxConfig (not Settings) to evaluate_rules"
```

---

## Task 18: Add `email_parser` edge case tests

**Files:**
- Modify: `tests/unit/test_email_parser.py`

- [ ] **Step 1: Add tests**

```python
# tests/unit/test_email_parser.py  (add all below)
import email as stdlib_email
from email.message import EmailMessage

from mail_ai_agent.config import Settings
from mail_ai_agent.email_parser import parse_email, normalize_body


def _settings() -> Settings:
    return Settings(IMAP_HOST="h", IMAP_USER="u", IMAP_PASS="p")


def test_html_only_email_extracts_text():
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<t@t>"
    msg.add_alternative("<html><body><p>Hello World</p></body></html>", subtype="html")
    result = parse_email(msg.as_bytes(), _settings())
    assert "Hello World" in result.plain_text_body


def test_missing_message_id_returns_none():
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "Test"
    msg.set_content("body")
    result = parse_email(msg.as_bytes(), _settings())
    assert result.message_id is None


def test_body_truncated_at_max_body_chars():
    settings = Settings(IMAP_HOST="h", IMAP_USER="u", IMAP_PASS="p", MAX_BODY_CHARS=10)
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "Test"
    msg.set_content("A" * 500)
    result = parse_email(msg.as_bytes(), settings)
    assert len(result.normalized_body) <= 10


def test_encoded_word_subject_decoded():
    """Subject: =?UTF-8?B?SGVsbG8gV29ybGQ=?= should decode to 'Hello World'."""
    raw = (
        b"From: a@b.com\r\n"
        b"Subject: =?UTF-8?B?SGVsbG8gV29ybGQ=?=\r\n"
        b"Message-ID: <t@t>\r\n"
        b"\r\n"
        b"body"
    )
    settings = _settings()
    result = parse_email(raw, settings)
    assert result.subject == "Hello World"


def test_fingerprint_deterministic_without_message_id():
    """Two identical bodies without Message-ID should produce same fingerprint."""
    from mail_ai_agent.email_parser import compute_message_fingerprint
    from datetime import datetime, timezone
    from mail_ai_agent.schemas import ParsedEmail

    p = ParsedEmail(
        message_id=None,
        sender="a@b.com",
        subject="Test",
        plain_text_body="hello",
        normalized_body="hello",
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert compute_message_fingerprint(p) == compute_message_fingerprint(p)
```

- [ ] **Step 2: Run tests**

```
pytest tests/unit/test_email_parser.py -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_email_parser.py
git commit -m "test: add email_parser edge cases — HTML-only, missing message-id, truncation, encoded subjects"
```

---

## Task 19: Add type hints to `_log_skip` in `main.py`

**Files:**
- Modify: `src/mail_ai_agent/main.py`

- [ ] **Step 1: Find `_log_skip` in main.py**

```
grep -n "_log_skip" src/mail_ai_agent/main.py
```

- [ ] **Step 2: Add type hints**

Replace the function signature of `_log_skip`. It should look like:

```python
def _log_skip(
    *,
    audit: AuditLogger,
    mailbox: MailboxConfig,
    parsed: "ParsedEmail",
    lease: "LeaseAcquireResult",
    settings: Settings,
) -> None:
```

Add the necessary imports at top of `main.py` if `LeaseAcquireResult` or `ParsedEmail` aren't already imported:
```python
from .schemas import ..., LeaseAcquireResult, ParsedEmail
```

(Check existing imports first with `grep "from .schemas" src/mail_ai_agent/main.py`)

- [ ] **Step 3: Run full suite**

```
pytest -q
```

- [ ] **Step 4: Commit**

```bash
git add src/mail_ai_agent/main.py
git commit -m "refactor: add type hints to _log_skip in main.py"
```

---

## Task 20: Deployment — fix `.gitignore` and `multi.plist.template`

**Files:**
- Modify: `.gitignore:17`
- Modify: `com.mailai.multi.plist.template`

- [ ] **Step 1: Fix `.gitignore` — protect subdirectories**

Add after line 17 in `.gitignore`:
```
config/mailboxes*/
```

The file should look like:
```
# Local mailbox manifests
config/mailboxes*.json
config/mailboxes*/
!config/mailboxes.example.json
```

- [ ] **Step 2: Verify `.gitignore` pattern**

```bash
# Create test subdirectory to verify it's excluded:
mkdir -p /tmp/test-gitignore-check/config/mailboxes_prod
echo '{}' > /tmp/test-gitignore-check/config/mailboxes_prod/config.json
cd /tmp/test-gitignore-check && git init -q && git add . 2>&1 | grep mailboxes || echo "OK: subdirectory excluded"
rm -rf /tmp/test-gitignore-check
```

- [ ] **Step 3: Add `EnvironmentVariables` to `com.mailai.multi.plist.template`**

Add before `<key>RunAtLoad</key>`:
```xml
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>/Users/REPLACE_ME/Repos/priv/mAIl</string>
    </dict>
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore com.mailai.multi.plist.template
git commit -m "fix: protect mailboxes subdirs in .gitignore; add PYTHONPATH to multi launchd template"
```

---

## Final Verification

- [ ] **Run complete test suite**

```
pytest -v --tb=short 2>&1 | tail -30
```
Expected: 0 failures, 0 errors.

- [ ] **Check test coverage**

```
pytest --cov=mail_ai_agent --cov-report=term-missing -q
```
Expected: ≥ 80% coverage on modified modules.

- [ ] **Verify no remaining hardcoded secrets**

```
grep -rn "swiatlowodem" src/ tests/
```
Expected: 0 results.

- [ ] **Verify no duplicate `_hash_value` definitions**

```
grep -rn "def _hash_value" src/
```
Expected: exactly 1 result (in `utils.py`).

- [ ] **Verify no duplicate `_chmod_owner_only` definitions**

```
grep -rn "def _chmod_owner_only" src/
```
Expected: exactly 1 result (in `utils.py`).

- [ ] **Verify dead alias removed**

```
grep -rn "def compute_fingerprint" src/
```
Expected: 0 results.

- [ ] **Final commit — bump version or tag**

```bash
git tag v0.10.0 -m "System quality audit complete — 10/10"
```

---

## Audit Finding Resolution Map

| Finding | Task | Status |
|---------|------|--------|
| `manifest_secrets_cli.py` — 0o644 sidecar | Task 6 | ✓ |
| `maintenance.py` — archive chmod | Task 6 | ✓ |
| `audit_logger.py` — fsync | Task 7 | ✓ |
| `draft_store.py` — PII at write | Task 8 | ✓ |
| `state_manager.py` — WAL + busy_timeout | Task 4 | ✓ |
| PII flag consistency | Task 8 | ✓ |
| Worker lock TOCTOU | Task 5 | ✓ |
| `folder_mapper.py` — KeyError | Task 9 | ✓ |
| `email_parser.py` — UTC fingerprint | Task 2 | ✓ |
| `cleanup_cli.py` — per-record error | Task 10 | ✓ |
| IMAP N+1 batch fetch | Task 13 | ✓ |
| `reporting.py` — fetchall() | Task 15 | ✓ |
| `maintenance.py` — N UPDATE | Task 15 | ✓ |
| `compute_fingerprint` dead alias | Task 3 | ✓ |
| `_hash_value` triplicated | Task 1 | ✓ |
| `config.py` — `or` → `is not None` | Task 11 | ✓ |
| `_log_skip` type hints | Task 19 | ✓ |
| Hardcoded billing email | Task 12 | ✓ |
| LLM retry backoff | Task 12 | ✓ |
| FakeIMAPClient class-level state | Task 16 | ✓ |
| `test_rule_engine` Settings→MailboxConfig | Task 17 | ✓ |
| Partial cleanup failure test | Task 10 | ✓ |
| `email_parser` edge case tests | Task 18 | ✓ |
| `bootstrap.sh` — CLI args exposure | Task 12 note | ⚠ (documented in .env.example) |
| `.gitignore` subdirectory gap | Task 20 | ✓ |
| `multi.plist.template` PYTHONPATH | Task 20 | ✓ |
| UIDPLUS admin notification | Task 14 | ✓ |
