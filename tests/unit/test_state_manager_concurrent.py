from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mail_ai_agent.state_manager import StateManager


def test_acquire_lease_concurrent_only_one_succeeds(tmp_path: Path) -> None:
    """Two threads racing to acquire_lease for the same message — exactly one wins."""
    db_path = tmp_path / "state.sqlite"
    manager = StateManager(db_path)

    FINGERPRINT = "fp-concurrent-test"
    outcomes: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def make_acquire_fn(worker_id: str):
        def try_acquire() -> None:
            try:
                barrier.wait(timeout=10)  # synchronize start; timeout prevents indefinite hang
                result = manager.acquire_lease(
                    mailbox_id="default",
                    message_id="<concurrent@example.com>",
                    fingerprint=FINGERPRINT,
                    imap_uid="10",
                    uidvalidity="999",
                    sender="test@example.com",
                    subject="Test concurrent",
                    source_folder="INBOX",
                    internaldate=None,
                    worker_id=worker_id,
                    lease_seconds=300,
                    max_retries=3,
                )
                outcomes.append(result.outcome)
            except Exception as e:
                errors.append(e)

        return try_acquire

    threads = [threading.Thread(target=make_acquire_fn(wid)) for wid in ("worker-a", "worker-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Unexpected errors: {errors}"
    assert len(outcomes) == 2
    assert sorted(outcomes) == ["acquired", "locked"]
