import asyncio

import redis.asyncio as redis

__version__ = "0.1.0"


class XRL:
    """
    eXtended Rate Limiter - A distributed rate limiter using Redis token bucket algorithm.

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
        Initialize the XRL rate limiter.

        Args:
            redis_client: redis.asyncio.Redis instance
        """
        self.redis = redis_client
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

        while True:
            result = await self.script(keys=[key], args=[capacity, rate])
            if result == 0:
                return True  # Token acquired
            await asyncio.sleep(retry_interval)  # Wait and retry

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
        result = await self.script(keys=[key], args=[capacity, rate])
        return int(result) == 0


# async def main():
#     """Example usage of the XRL rate limiter with dependency injection."""

#     # Create Redis connection (managed externally)
#     redis_client = redis.from_url("redis://localhost")

#     try:
#         # Create XRL instance with dependency injection
#         xrl = XRL(redis_client)

#         print("=== Testing normal rate ===")
#         await xrl.acquire_token("user:123", capacity=5, rate=1)
#         print("✅ Token acquired for normal rate!")

#         # Try to acquire another token without waiting
#         if await xrl.try_acquire_token("user:123", capacity=5, rate=1):
#             print("✅ Second token acquired immediately!")
#         else:
#             print("❌ Second token not available (rate limited)")

#     finally:
#         # Always close the Redis connection
#         await redis_client.aclose()


# if __name__ == "__main__":
#     asyncio.run(main())
