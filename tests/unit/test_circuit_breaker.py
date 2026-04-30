from __future__ import annotations

import pytest
from mail_ai_agent.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState


def test_circuit_breaker_allows_calls_when_closed() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, timeout_seconds=60)
    
    result = cb.call(lambda: "success")
    
    assert result == "success"
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_opens_after_failures() -> None:
    cb = CircuitBreaker("test", failure_threshold=2, timeout_seconds=60)
    
    # First failure
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail 1")))
    
    # Second failure - should open circuit
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail 2")))
    
    assert cb.state == CircuitState.OPEN
    
    # Next call should fail immediately with CircuitBreakerOpenError
    with pytest.raises(CircuitBreakerOpenError):
        cb.call(lambda: "should not execute")


def test_circuit_breaker_half_open_after_timeout() -> None:
    cb = CircuitBreaker("test", failure_threshold=1, timeout_seconds=0)  # 0s timeout for test
    
    # Open the circuit
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
    
    assert cb.state == CircuitState.OPEN
    
    # After timeout, should transition to HALF_OPEN
    # With timeout=0, next call should be allowed
    result = cb.call(lambda: "success")
    
    assert result == "success"
    assert cb.state == CircuitState.CLOSED  # Successful call closes circuit


def test_circuit_breaker_closes_after_success_in_half_open() -> None:
    cb = CircuitBreaker("test", failure_threshold=1, timeout_seconds=0)
    
    # Open circuit
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))
    
    # Success in half-open should close circuit
    cb.call(lambda: "success")
    
    assert cb.state == CircuitState.CLOSED
    
    # Subsequent calls should work normally
    result = cb.call(lambda: "success 2")
    assert result == "success 2"


def test_circuit_breaker_reopens_on_failure_in_half_open() -> None:
    cb = CircuitBreaker("test", failure_threshold=1, timeout_seconds=0)
    
    # Open circuit
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail 1")))
    
    # Failure in half-open should reopen circuit
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError("fail 2")))
    
    assert cb.state == CircuitState.OPEN


def test_circuit_breaker_counts_only_expected_exceptions() -> None:
    cb = CircuitBreaker("test", failure_threshold=1, timeout_seconds=60, expected_exception=ValueError)
    
    # RuntimeError is not counted as failure
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("unexpected")))
    
    # Circuit should still be closed
    assert cb.state == CircuitState.CLOSED
