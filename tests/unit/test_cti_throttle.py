"""Tests for the inference rate limiter (infrastructure/llm/throttle.py)."""

from glokta.infrastructure.llm.throttle import RateLimiter


class FakeClock:
    """Deterministic monotonic clock advanced by recorded sleeps."""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class TestRateLimiter:
    def test_first_call_does_not_sleep(self):
        clock = FakeClock()
        rl = RateLimiter(60, sleep=clock.sleep, monotonic=clock.monotonic)
        rl.wait()
        assert clock.sleeps == []

    def test_second_immediate_call_sleeps_to_min_interval(self):
        clock = FakeClock()
        rl = RateLimiter(60, sleep=clock.sleep, monotonic=clock.monotonic)  # 1s interval
        rl.wait()  # t=0, no sleep
        rl.wait()  # immediate -> must sleep ~1s
        assert clock.sleeps == [1.0]

    def test_no_sleep_when_enough_time_elapsed(self):
        clock = FakeClock()
        rl = RateLimiter(60, sleep=clock.sleep, monotonic=clock.monotonic)
        rl.wait()
        clock.t = 5.0  # plenty of time passed
        rl.wait()
        assert clock.sleeps == []

    def test_zero_or_negative_rpm_disables_throttle(self):
        clock = FakeClock()
        rl = RateLimiter(0, sleep=clock.sleep, monotonic=clock.monotonic)
        rl.wait()
        rl.wait()
        assert clock.sleeps == []
