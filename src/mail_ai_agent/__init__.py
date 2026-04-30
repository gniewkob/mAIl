"""AI mail triage package."""

# Repository exports for testing
from .repositories import FakeStateManager
from .pipeline import ProcessingPipeline, ProcessingResult

__all__ = ["FakeStateManager", "ProcessingPipeline", "ProcessingResult"]
