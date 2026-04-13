"""Unit tests for Redis token bucket rate limiter."""

import pytest
import fakeredis

from garakboard.worker.rate_limiter import TokenBucket


@pytest.fixture
def redis_client():
    """FakeRedis client — no real Redis needed."""
    return fakeredis.FakeRedis()


@pytest.fixture
def bucket(redis_client):
    """TokenBucket with capacity=5 and 60s window for testing."""
    return TokenBucket(redis_client=redis_client, capacity=5, window_seconds=60)


def test_acquire_returns_true_on_first_call(bucket):
    """acquire() returns True when bucket has tokens."""
    result = bucket.acquire("model-a")
    assert result is True


def test_acquire_decrements_remaining_tokens(bucket):
    """acquire() reduces remaining() by 1."""
    bucket.acquire("model-a")
    remaining = bucket.remaining("model-a")
    assert remaining == 4  # capacity 5 - 1 consumed


def test_acquire_returns_false_when_bucket_empty(bucket):
    """acquire() returns False after capacity tokens have been consumed."""
    # Exhaust the bucket
    for _ in range(5):
        bucket.acquire("model-a")
    # 6th acquire should fail
    result = bucket.acquire("model-a")
    assert result is False


def test_acquire_fills_bucket_on_first_use(bucket):
    """After one acquire, remaining() == capacity - 1."""
    bucket.acquire("model-a")
    remaining = bucket.remaining("model-a")
    assert remaining == 4  # 5 - 1


def test_remaining_returns_capacity_when_key_absent(bucket):
    """remaining() returns capacity when bucket has never been used."""
    remaining = bucket.remaining("unused-model")
    assert remaining == 5


def test_remaining_decreases_with_each_acquire(bucket):
    """remaining() decreases by 1 for each successful acquire."""
    bucket.acquire("model-a")
    assert bucket.remaining("model-a") == 4
    bucket.acquire("model-a")
    assert bucket.remaining("model-a") == 3
    bucket.acquire("model-a")
    assert bucket.remaining("model-a") == 2


def test_reset_clears_the_bucket(bucket):
    """reset() deletes the bucket key; next remaining() returns capacity."""
    bucket.acquire("model-a")
    assert bucket.remaining("model-a") == 4
    bucket.reset("model-a")
    assert bucket.remaining("model-a") == 5


def test_different_models_have_independent_buckets(bucket):
    """Acquiring tokens for model-a does not affect model-b bucket."""
    # Exhaust model-a's bucket
    for _ in range(5):
        bucket.acquire("model-a")
    # model-b should still be full
    remaining_b = bucket.remaining("model-b")
    assert remaining_b == 5
    # And model-b should still accept acquires
    result = bucket.acquire("model-b")
    assert result is True


def test_bucket_exhaustion_exact_capacity(bucket):
    """Exactly capacity acquires succeed; the (capacity+1)th returns False."""
    results = []
    for _ in range(5):
        results.append(bucket.acquire("model-x"))
    results.append(bucket.acquire("model-x"))  # 6th should fail
    assert results == [True, True, True, True, True, False]


def test_acquire_is_true_after_reset(bucket):
    """acquire() returns True after reset() clears the bucket."""
    # Exhaust the bucket
    for _ in range(5):
        bucket.acquire("model-a")
    assert bucket.acquire("model-a") is False
    # Reset and verify it works again
    bucket.reset("model-a")
    result = bucket.acquire("model-a")
    assert result is True