"""Unit tests for Redis-backed per-model run lock."""

import pytest
import fakeredis

from garakboard.worker.rate_limiter import RunLock


@pytest.fixture
def redis_client():
    """FakeRedis client — no real Redis needed."""
    return fakeredis.FakeRedis()


@pytest.fixture
def lock(redis_client):
    """RunLock with a short timeout for testing."""
    return RunLock(redis_client=redis_client, timeout=60)


def test_acquire_returns_true_when_no_run_active(lock):
    """acquire() returns True when no run is active for the model."""
    assert lock.acquire("model-a") is True


def test_acquire_returns_false_when_run_already_active(lock):
    """acquire() returns False when a run is already active for the model."""
    lock.acquire("model-a")
    assert lock.acquire("model-a") is False


def test_release_allows_subsequent_acquire(lock):
    """acquire() returns True again after release()."""
    lock.acquire("model-a")
    lock.release("model-a")
    assert lock.acquire("model-a") is True


def test_different_models_are_independent(lock):
    """Locking model-a does not affect model-b."""
    lock.acquire("model-a")
    assert lock.acquire("model-b") is True


def test_acquire_blocked_model_does_not_affect_other(lock):
    """A failed acquire on model-a leaves model-b acquirable."""
    lock.acquire("model-a")
    lock.acquire("model-a")  # fails — model-a still locked
    assert lock.acquire("model-b") is True


def test_release_is_idempotent(lock):
    """Releasing an already-released lock does not raise."""
    lock.acquire("model-a")
    lock.release("model-a")
    lock.release("model-a")  # second release should not raise
    assert lock.acquire("model-a") is True


def test_reset_is_alias_for_release(lock):
    """reset() behaves identically to release()."""
    lock.acquire("model-a")
    lock.reset("model-a")
    assert lock.acquire("model-a") is True


def test_concurrent_acquire_only_one_succeeds(redis_client):
    """
    Two RunLock instances sharing the same Redis client simulate concurrent workers.
    Exactly one acquire should succeed.
    """
    lock_a = RunLock(redis_client=redis_client, timeout=60)
    lock_b = RunLock(redis_client=redis_client, timeout=60)

    result_a = lock_a.acquire("model-x")
    result_b = lock_b.acquire("model-x")

    assert result_a is True
    assert result_b is False


def test_failed_acquire_does_not_leave_lock_held(redis_client):
    """
    After a failed acquire, a third worker can still acquire once the first releases.
    Verifies that DECR correctly restores count to 1 (not 0).
    """
    lock_a = RunLock(redis_client=redis_client, timeout=60)
    lock_b = RunLock(redis_client=redis_client, timeout=60)
    lock_c = RunLock(redis_client=redis_client, timeout=60)

    lock_a.acquire("model-y")   # succeeds, count=1
    lock_b.acquire("model-y")   # fails, count restored to 1

    # C cannot acquire while A holds the lock
    assert lock_c.acquire("model-y") is False

    # After A releases, C can acquire
    lock_a.release("model-y")
    assert lock_c.acquire("model-y") is True
