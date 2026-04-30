"""Tests for thread-safety of AuditLogger."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from mail_ai_agent.audit_logger import AuditLogger


class TestAuditLoggerThreadSafety:
    """Test thread-safety of AuditLogger."""

    def test_concurrent_writes_produce_valid_jsonl(self, tmp_path: Path) -> None:
        """Multiple threads writing concurrently produce valid JSONL."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(log_path, redact_pii=False)

        def write_log(i):
            logger.log(
                level="INFO",
                action_taken="test_action",
                thread_id=i,
                message=f"Message from thread {i}",
            )
            return i

        # Write from multiple threads concurrently
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(write_log, i) for i in range(100)]
            completed = [future.result() for future in as_completed(futures)]

        assert len(completed) == 100

        # Verify all lines are valid JSON
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 100

        for line in lines:
            record = json.loads(line)
            assert "thread_id" in record
            assert "message" in record
            assert "timestamp" in record

    def test_concurrent_writes_no_corrupted_lines(self, tmp_path: Path) -> None:
        """Concurrent writes don't produce corrupted/incomplete lines."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(log_path, redact_pii=False)

        errors = []

        def write_log(i):
            try:
                logger.log(
                    level="INFO",
                    action_taken="concurrent_test",
                    iteration=i,
                    data="x" * 100,  # Larger payload
                )
            except Exception as e:
                errors.append(str(e))

        # Many threads writing large payloads concurrently
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(write_log, i) for i in range(200)]
            for future in as_completed(futures):
                future.result()

        assert not errors, f"Errors during concurrent writes: {errors}"

        # Verify file integrity
        content = log_path.read_text(encoding="utf-8")
        lines = content.strip().splitlines()

        assert len(lines) == 200

        # Each line must be valid JSON and end with newline (except possibly last)
        for i, line in enumerate(lines):
            try:
                record = json.loads(line)
                assert record["action_taken"] == "concurrent_test"
                assert "iteration" in record
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i} is not valid JSON: {e}\nLine content: {line!r}")

    def test_thread_safety_with_pii_redaction(self, tmp_path: Path) -> None:
        """Concurrent writes with PII redaction work correctly."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(log_path, redact_pii=True)  # Redaction enabled

        def write_log(i):
            logger.log(
                level="INFO",
                action_taken="test",
                message_id=f"msg-{i}",
                sender=f"user{i}@example.com",
                subject=f"Subject {i}",
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_log, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 50

        for line in lines:
            record = json.loads(line)
            # PII should be redacted
            assert "message_id" not in record
            assert "sender" not in record
            assert "subject" not in record
            # Hashes should be present
            assert "message_id_sha256" in record
            assert "sender_sha256" in record
            assert "subject_sha256" in record

    def test_mixed_read_write_thread_safety(self, tmp_path: Path) -> None:
        """Reading while writing doesn't cause errors."""
        log_path = tmp_path / "audit.jsonl"
        logger = AuditLogger(log_path, redact_pii=False)
        read_results = []
        write_count = [0]

        def writer(i):
            logger.log(level="INFO", action_taken="write", index=i)
            write_count[0] += 1

        def reader():
            try:
                if log_path.exists():
                    content = log_path.read_text(encoding="utf-8")
                    lines = [l for l in content.splitlines() if l.strip()]
                    # Try to parse each line
                    valid_records = []
                    for line in lines:
                        try:
                            valid_records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass  # Partial write in progress
                    read_results.append(len(valid_records))
            except Exception as e:
                read_results.append(f"error: {e}")

        # Interleave reads and writes
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit writes
            write_futures = [executor.submit(writer, i) for i in range(50)]
            # Submit some reads while writes are happening
            read_futures = [executor.submit(reader) for _ in range(10)]

            for future in as_completed(write_futures + read_futures):
                future.result()

        # Final read to verify all writes succeeded
        final_content = log_path.read_text(encoding="utf-8")
        final_lines = [l for l in final_content.splitlines() if l.strip()]
        assert len(final_lines) == 50

        # All lines should be valid JSON
        for line in final_lines:
            record = json.loads(line)
            assert record["action_taken"] == "write"
