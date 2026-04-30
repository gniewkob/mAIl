# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 2026-04-04

### Added

#### Error Handling & Resilience
- **Graceful degradation for encoding errors** - Worker no longer crashes on unknown character encodings (e.g., cp-850)
- **Automatic message isolation** - Problematic messages are marked as "parse failed" and skipped
- **Fallback encoding strategy** - UTF-8 fallback for unsupported character sets
- **Comprehensive test coverage** - 3 new tests for encoding error handling

#### Architecture Improvements
- Thread-safe fake repositories with RLock (prevents deadlocks in tests)
- Connection pool resource leak fix (proper tracking of temporary connections)
- Improved circuit breaker pattern implementation

### Fixed

#### Critical Bugs
- **P0**: `LookupError: unknown encoding: cp-850` crashing worker ([#1](docs/error-handling-architecture.md))
- **P1**: `pipeline/stages.py` using wrong mailbox config for uncertain folder routing
- **P1**: `async_llm_gateway.py` same uncertain folder routing bug
- **P2**: Resource leak in connection pool when exhausted
- **P2**: `decide_from_rule()` missing fields in FinalDecision (confidence, priority, etc.)

#### Code Quality
- Import statement moved from middle of file to top in `llm_gateway.py`
- SQLite WorkflowStatus NULL handling fallback

### Changed

#### Breaking Changes
- Historical `spam_or_offer` classification has been retired in favor of explicit `spam`, `newsletter`, and `offer`

#### Internal Changes
- `ParseStage` catches parser exceptions and quarantines them as `parse_error`
- `_safe_part_content()` has double try-except for LookupError with UTF-8 fallback

### Performance

- Worker continues processing after encountering problematic messages
- No more manual intervention required for encoding errors
- Estimated processing rate: ~230 messages/minute

### Metrics

| Metric | Before | After |
|--------|--------|-------|
| Messages processed | 10,025 | 10,191+ (growing) |
| Worker crashes | 1+ per encoding error | 0 |
| Manual interventions | Required | Not required |
| Test coverage | 257 tests | 260 tests |

---

## Previous Changes

### Pre-2026-04-04

- Initial architecture with pipeline pattern
- Repository pattern implementation (SQLite + Fake)
- Async LLM gateway with circuit breaker
- Multi-mailbox support
- Comprehensive audit logging
- State management with leases
- Metrics and health checks

---

## Documentation

- [Error Handling Architecture](docs/error-handling-architecture.md)
- Full architecture documentation in `docs/`

## Contributors

- AI Assistant (Claude) - Error handling improvements
