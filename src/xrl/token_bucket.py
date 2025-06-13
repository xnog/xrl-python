import asyncio
import redis.asyncio as redis
import logging

class TokenBucketRateLimiter:
    """
    Token Bucket Rate Limiter - A distributed rate limiter using Redis token bucket algorithm.

    This class provides rate limiting functionality with configurable capacity and refill rates.
    It uses Redis Lua scripts for atomic operations to ensure consistency in distributed environments.
    """

    LUA_SCRIPT = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local refill_rate = tonumber(ARGV[2])

    local tokens_key = key
    local timestamp_key = key .. ":timestamp"

    -- Dynamic TTL: time to fill the bucket + buffer
    -- Minimum 60 seconds, maximum 24 hours (86400 seconds)
    local ttl = math.max(60, math.min(86400, math.ceil(capacity / refill_rate) + 300))

    local values = redis.call("mget", tokens_key, timestamp_key)
    local tokens = tonumber(values[1]) or capacity
    local timestamp = tonumber(values[2]) or 0

    local now = tonumber(redis.call("time")[1])
    local elapsed = now - timestamp

    tokens = math.min(capacity, tokens + elapsed * refill_rate)

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
        self.redis = redis_client
        self.logger = logging.getLogger(self.__class__.__name__)
        self.script = redis_client.register_script(self.LUA_SCRIPT)

    async def acquire_token(self, key: str, capacity: int, refill_rate: float) -> bool:
        """
        Checks and waits for token availability using a Redis token bucket.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of tokens in the bucket
            refill_rate: Token refill rate (tokens per second)

        Returns:
            True when a token is successfully acquired

        Examples:
            # 100 tokens capacity, refill at 1.67 tokens/second (100 per minute)
            await limiter.acquire_token("user:123", capacity=100, refill_rate=100/60)

            # 200 tokens capacity, refill at 3.33 tokens/second (200 per minute)
            await limiter.acquire_token("user:789", capacity=200, refill_rate=200/60)

            # 100 tokens capacity, refill at 100 tokens/second
            await limiter.acquire_token("user:456", capacity=100, refill_rate=100)
        """
        retry_interval = 1.0 / refill_rate if refill_rate > 0 else 1.0
        attempt = 1

        while True:
            self.logger.debug(f"Attempt {attempt} to acquire token for key '{key}' (capacity={capacity}, refill_rate={refill_rate})")
            result = await self.script(keys=[key], args=[capacity, refill_rate])
            
            if result == 0:
                self.logger.debug(f"Token acquired for key '{key}' on attempt {attempt}")
                return True  # Token acquired
            
            self.logger.debug(f"Rate limited for key '{key}', waiting {retry_interval:.2f}s before retry")
            await asyncio.sleep(retry_interval)  # Wait and retry
            attempt += 1

    async def try_acquire_token(self, key: str, capacity: int, refill_rate: float) -> bool:
        """
        Try to acquire a token without waiting/blocking.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of tokens in the bucket
            refill_rate: Token refill rate (tokens per second)

        Returns:
            True if token was acquired, False if rate limited
        """
        self.logger.debug(f"Trying to acquire token for key '{key}' (capacity={capacity}, refill_rate={refill_rate})")
        result = await self.script(keys=[key], args=[capacity, refill_rate])
        success = int(result) == 0
        self.logger.debug(f"{'Token acquired' if success else 'Rate limited'} for key '{key}'")
        return success

    # Keep the old method for backward compatibility but mark it as deprecated
    async def acquire_token_old(self, key: str, capacity: int, rate: float) -> bool:
        """
        DEPRECATED: Use acquire_token(key, capacity, refill_rate) instead.
        This method is kept for backward compatibility.
        """
        return await self.acquire_token(key, capacity, rate)

    async def try_acquire_token_old(self, key: str, capacity: int, rate: float) -> bool:
        """
        DEPRECATED: Use try_acquire_token(key, capacity, refill_rate) instead.
        This method is kept for backward compatibility.
        """
        return await self.try_acquire_token(key, capacity, rate) 