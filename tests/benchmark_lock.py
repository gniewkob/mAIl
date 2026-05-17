import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from mail_ai_agent.main import _process_mailbox
from mail_ai_agent.config import MailboxConfig, Settings
from mail_ai_agent.state_manager import StateManager
from mail_ai_agent.schemas import CandidateMessage, WorkflowStatus
from mail_ai_agent.message_processor import ProcessingResult
from mail_ai_agent.constants import ActionTaken

def run_benchmark():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "state.sqlite"

        settings = Settings(
            IMAP_HOST="imap.example.com",
            IMAP_USER="user@example.com",
            IMAP_PASS="secret",
            STATE_DB_PATH=db_path,
            WORKER_ID="bench-worker",
            DRY_RUN=True,
            PROCESSING_LEASE_SECONDS=900
        )

        mailbox = MailboxConfig(
            mailbox_id="bench",
            imap_host="imap.example.com",
            imap_user="user@example.com",
            imap_pass="secret",
            imap_source_folder="INBOX"
        )

        state = StateManager(db_path)
        # Initialize the lock
        state.acquire_worker_lock(worker_id="bench-worker", lease_seconds=900)

        # Mock processor
        processor = MagicMock()
        processor.state = state
        processor.settings = settings
        processor.process_candidate.return_value = ProcessingResult(
            action_taken="simulated_mock",
            final_status=WorkflowStatus.PROCESSED,
            category="mock",
            confidence=0.99,
            target_folder="Target",
            latency_ms=1,
            draft_path=None
        )

        # Mock CleanupManager
        cleanup_manager = MagicMock()
        cleanup_manager.run_cleanup_pass.return_value = (0, 0, 0)

        # Mock IMAP Client context manager
        imap_mock = MagicMock()

        # Generate 1000 candidates
        candidates = []
        for i in range(1000):
            candidates.append(CandidateMessage(
                uid=str(i),
                uidvalidity="123",
                internaldate=None,
                raw_bytes=b"From: test@example.com\r\nSubject: Test\r\n\r\nBody"
            ))
        imap_mock.fetch_candidates.return_value = candidates

        # Mock IMAPClient class
        class MockIMAPClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return imap_mock
            def __exit__(self, *args):
                pass

        import mail_ai_agent.main
        original_imap_client = mail_ai_agent.main.IMAPClient
        mail_ai_agent.main.IMAPClient = MockIMAPClient

        try:
            print(f"Starting benchmark with {len(candidates)} candidates...")
            start_time = time.perf_counter()

            _process_mailbox(
                mailbox=mailbox,
                settings=settings,
                processor=processor,
                cleanup_manager=cleanup_manager
            )

            end_time = time.perf_counter()
            duration = end_time - start_time
            print(f"Processed {len(candidates)} candidates in {duration:.4f} seconds")
            print(f"Average time per candidate: {(duration / len(candidates)) * 1000:.2f} ms")

        finally:
            mail_ai_agent.main.IMAPClient = original_imap_client

if __name__ == "__main__":
    run_benchmark()
