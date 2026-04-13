"""Redis-backed token bucket rate limiter for per-model rate limiting."""

import redis

from garakboard.config import settings


class TokenBucket:
    """
    Redis-backed token bucket for per-model rate limiting.

    Uses a Redis key per model_name. The key stores the remaining token count.
    On first access (key missing), the bucket is initialised to capacity.
    The key has a TTL of 60 seconds — when it expires, the bucket refills.

    Designed for use with multiple concurrent Celery workers.
    All operations are atomic via WATCH/MULTI/EXEC pipelines.
    """

    def __init__(self, redis_client, capacity: int = 15, window_seconds: int = 60):
        """
        Args:
            redis_client: A redis.Redis (or fakeredis.FakeRedis) client instance
            capacity: Maximum tokens per window (default 15 — 15 RPM headroom under 20 RPM limit)
            window_seconds: Token refill window in seconds (default 60)
        """
        self.redis = redis_client
        self.capacity = capacity
        self.window_seconds = window_seconds

    def _key(self, model_name: str) -> str:
        """Generate the Redis key for a model's bucket."""
        return f"rate_limit:{model_name}"

    def acquire(self, model_name: str) -> bool:
        """
        Attempt to acquire one token for the given model.

        Uses WATCH/MULTI/EXEC to ensure atomicity across concurrent workers.

        Args:
            model_name: The OpenRouter model identifier (used as bucket key)

        Returns:
            True if a token was acquired (request can proceed)
            False if the bucket is empty (caller should wait)
        """
        key = self._key(model_name)

        # Use a pipeline with WATCH for optimistic locking
        with self.redis.pipeline() as pipe:
            while True:
                try:
                    # Watch the key for changes
                    pipe.watch(key)

                    # Get current value
                    current = pipe.get(key)
                    
                    if current is None:
                        # Key doesn't exist — initialise bucket at capacity - 1
                        # (we're consuming 1 token now)
                        pipe.multi()
                        pipe.set(key, self.capacity - 1)
                        pipe.expire(key, self.window_seconds)
                        pipe.execute()
                        return True
                    else:
                        current_val = int(current)
                        if current_val <= 0:
                            # Bucket empty — give up
                            pipe.unwatch()
                            return False
                        
                        # Decrement and refresh TTL
                        pipe.multi()
                        pipe.decr(key)
                        pipe.expire(key, self.window_seconds)
                        pipe.execute()
                        return True

                except redis.WatchError:
                    # Another client modified the key — retry
                    continue

    def remaining(self, model_name: str) -> int:
        """
        Return the number of tokens remaining for a model.

        Returns capacity if the key does not exist yet (bucket full).
        """
        value = self.redis.get(self._key(model_name))
        if value is None:
            return self.capacity
        return int(value)

    def reset(self, model_name: str) -> None:
        """
        Reset (delete) the bucket for a model. Useful for testing.
        """
        self.redis.delete(self._key(model_name))


def get_redis_client():
    """Return a configured Redis client from settings."""
    return redis.from_url(settings.redis_url)


def get_token_bucket(capacity: int = 15) -> TokenBucket:
    """Return a TokenBucket using the production Redis client."""
    return TokenBucket(redis_client=get_redis_client(), capacity=capacity)