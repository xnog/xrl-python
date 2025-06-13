import asyncio
import redis.asyncio as redis
from .base import BaseRateLimiter

class TokenBucketRateLimiter(BaseRateLimiter):
    """
    Token Bucket Rate Limiter - A distributed rate limiter using Redis token bucket algorithm.

    This class provides rate limiting functionality with configurable capacity and refill rates.
    It uses Redis Lua scripts for atomic operations to ensure consistency in distributed environments.
    """

    LUA_SCRIPT = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local rate = tonumber(ARGV[2])

    local tokens_key = key
    local timestamp_key = key .. ":timestamp"

    -- Dynamic TTL: time to fill the bucket + buffer
    -- Minimum 60 seconds, maximum 24 hours (86400 seconds)
    local ttl = math.max(60, math.min(86400, math.ceil(capacity / rate) + 300))

    local values = redis.call("mget", tokens_key, timestamp_key)
    local tokens = tonumber(values[1]) or capacity
    local timestamp = tonumber(values[2]) or 0

    local now = tonumber(redis.call("time")[1])
    local elapsed = now - timestamp

    tokens = math.min(capacity, tokens + elapsed * rate)

    redis.call("mset", tokens_key, tokens, timestamp_key, now)
    redis.call("expire", tokens_key, ttl)
    redis.call("expire", timestamp_key, ttl)

    if tokens >= 1 then
        redis.call("incrbyfloat", tokens_key, -1)
        return 0  -- allowed
    else
        return 1  -- rate limited
    end
    """

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize the Token Bucket rate limiter.

        Args:
            redis_client: redis.asyncio.Redis instance
        """
        super().__init__(redis_client)
        self.script = redis_client.register_script(self.LUA_SCRIPT)

    async def acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Checks and waits for token availability using a Redis token bucket.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of tokens in the bucket
            rate: Token refill rate (tokens per second)

        Returns:
            True when a token is successfully acquired

        Examples:
            # 100 RPM (requests per minute) = 100/60 = 1.67 tokens per second
            await xrl.acquire_token("user:123", capacity=100, rate=100/60)

            # 200 RPM (requests per minute) = 200/60 = 3.33 tokens per second
            await xrl.acquire_token("user:789", capacity=200, rate=200/60)

            # 100 RPS (requests per second) = 100 tokens per second
            await xrl.acquire_token("user:456", capacity=100, rate=100)

            # 200 RPS (requests per second) = 200 tokens per second
            await xrl.acquire_token("user:abc", capacity=200, rate=200)
        """
        retry_interval = 1.0 / rate
        attempt = 1

        while True:
            self.logger.debug(f"Attempt {attempt} to acquire token for key '{key}' (capacity={capacity}, rate={rate})")
            result = await self.script(keys=[key], args=[capacity, rate])
            
            if result == 0:
                self.logger.debug(f"Token acquired for key '{key}' on attempt {attempt}")
                return True  # Token acquired
            
            self.logger.debug(f"Rate limited for key '{key}', waiting {retry_interval:.2f}s before retry")
            await asyncio.sleep(retry_interval)  # Wait and retry
            attempt += 1

    async def try_acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Try to acquire a token without waiting/blocking.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of tokens in the bucket
            rate: Token refill rate (tokens per second)

        Returns:
            True if token was acquired, False if rate limited
        """
        self.logger.debug(f"Trying to acquire token for key '{key}' (capacity={capacity}, rate={rate})")
        result = await self.script(keys=[key], args=[capacity, rate])
        success = int(result) == 0
        self.logger.debug(f"{'Token acquired' if success else 'Rate limited'} for key '{key}'")
        return success 