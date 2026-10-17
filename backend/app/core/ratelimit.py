"""
Rate limiting for sensitive endpoints (sign-in, OTP, password reset, webhooks).

Limits are counted per key in RATE_LIMIT_STORAGE_URL (memory in development,
Redis in production so all workers share counters). Endpoints usually apply
two limits: one per client IP and one per target account, so a distributed
attack on a single account is still slowed down.

    limiter.hit("login:ip", "10/minute", client_ip(request))
"""

import time
from functools import lru_cache

from limits import RateLimitItem, parse
from limits.storage import Storage, storage_from_string
from limits.strategies import MovingWindowRateLimiter

from app.core.config import get_settings
from app.core.errors import RateLimited


class Limiter:
    def __init__(self, storage_url: str, enabled: bool = True) -> None:
        storage = storage_from_string(storage_url)
        if not isinstance(storage, Storage):
            raise TypeError("RATE_LIMIT_STORAGE_URL must name a synchronous storage backend")
        self.storage = storage
        self.strategy = MovingWindowRateLimiter(self.storage)
        self.enabled = enabled

    @staticmethod
    @lru_cache(maxsize=64)
    def _item(rate: str) -> RateLimitItem:
        return parse(rate)

    def hit(self, scope: str, rate: str, key: str) -> None:
        """Count one attempt; raise RateLimited when the limit is exceeded."""
        if not self.enabled or not key:
            return
        item = self._item(rate)
        if not self.strategy.hit(item, scope, key):
            reset_at, _ = self.strategy.get_window_stats(item, scope, key)
            raise RateLimited(retry_after=max(1, int(reset_at - time.time())))

    def reset(self) -> None:
        self.storage.reset()


@lru_cache(maxsize=1)
def get_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(settings.rate_limit_storage_url, settings.rate_limits_enabled)
