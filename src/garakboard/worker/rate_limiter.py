"""Redis-backed per-model run lock for garak job serialisation."""

import redis

from garakboard.config import settings


class RunLock:
    """
    Redis-backed per-model run lock using INCR/EXPIRE.

    Ensures at most one garak run executes per model at a time across
    multiple concurrent Celery workers. Uses INCR atomicity — no Lua,
    no WATCH/MULTI/EXEC pipelines.

    The lock key carries a TTL equal to the garak timeout so it
    self-heals if a worker dies without reaching the finally block.
    """

    def __init__(self, redis_client, timeout: int = 3600):
        """
        Args:
            redis_client: A redis.Redis (or fakeredis.FakeRedis) client instance
            timeout: Lock TTL in seconds — should match garak_timeout_seconds
        """
        self.redis = redis_client
        self.timeout = timeout

    def _key(self, model_name: str) -> str:
        return f"run_lock:{model_name}"

    def acquire(self, model_name: str) -> bool:
        """
        Attempt to acquire the run lock for a model.

        Returns True if the lock was acquired (no other run is active).
        Returns False if another run is already active for this model.

        On concurrent acquire attempts:
          - INCR is atomic, so exactly one worker gets count=1.
          - All others get count>1, DECR to undo, and return False.
          - EXPIRE is set on every call so the key always has a TTL,
            even if the previous holder crashed before setting one.
        """
        key = self._key(model_name)
        count = self.redis.incr(key)
        self.redis.expire(key, self.timeout)
        if count > 1:
            self.redis.decr(key)
            return False
        return True

    def release(self, model_name: str) -> None:
        """Release the lock by deleting the key."""
        self.redis.delete(self._key(model_name))

    def reset(self, model_name: str) -> None:
        """Alias for release. Useful in tests."""
        self.release(model_name)


def get_redis_client() -> redis.Redis:
    """Return a configured Redis client from settings."""
    return redis.from_url(settings.redis_url)


def get_run_lock(timeout: int | None = None) -> RunLock:
    """Return a RunLock using the production Redis client."""
    return RunLock(
        redis_client=get_redis_client(),
        timeout=timeout if timeout is not None else settings.garak_timeout_seconds,
    )
