import asyncio
import redis.asyncio as redis
from .base import BaseRateLimiter

class FixedWindowRateLimiter(BaseRateLimiter):
    """
    Fixed Window Rate Limiter - A distributed rate limiter using Redis fixed window algorithm.

    This class provides rate limiting functionality with a fixed time window.
    It uses Redis Lua scripts for atomic operations to ensure consistency in distributed environments.
    """

    LUA_SCRIPT = """
    local key = KEYS[1]
    local capacity = tonumber(ARGV[1])
    local window_size = tonumber(ARGV[2])  -- window size in seconds

    -- Get current window start time
    local now = tonumber(redis.call("time")[1])
    local window_start = math.floor(now / window_size) * window_size
    local window_key = key .. ":" .. window_start

    -- Get current count for this window
    local count = tonumber(redis.call("get", window_key)) or 0

    if count < capacity then
        -- Increment counter and set expiry
        redis.call("incr", window_key)
        redis.call("expire", window_key, window_size)
        return 0  -- allowed
    else
        return 1  -- rate limited
    end
    """

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize the Fixed Window rate limiter.

        Args:
            redis_client: redis.asyncio.Redis instance
        """
        super().__init__(redis_client)
        self.script = redis_client.register_script(self.LUA_SCRIPT)

    async def acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Checks and waits for request availability using a fixed window.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of requests allowed in the window
            rate: Rate limit (requests per second) - used to calculate window size

        Returns:
            True when a request is allowed

        Examples:
            # 100 requests per minute
            await xrl.acquire_token("user:123", capacity=100, rate=100/60)

            # 200 requests per minute
            await xrl.acquire_token("user:789", capacity=200, rate=200/60)

            # 100 requests per second
            await xrl.acquire_token("user:456", capacity=100, rate=100)

            # 200 requests per second
            await xrl.acquire_token("user:abc", capacity=200, rate=200)
        """
        window_size = 1.0  # Default to 1 second window
        if rate < 1 and rate > 0:
            window_size = 1.0 / rate  # For rates less than 1 per second, use larger window

        attempt = 1
        while True:
            self.logger.debug(f"Attempt {attempt} to acquire token for key '{key}' (capacity={capacity}, window_size={window_size:.2f}s)")
            result = await self.script(keys=[key], args=[capacity, window_size])
            
            if result == 0:
                self.logger.debug(f"Token acquired for key '{key}' on attempt {attempt}")
                return True  # Request allowed
            
            self.logger.debug(f"Rate limited for key '{key}', waiting {window_size:.2f}s for next window")
            await asyncio.sleep(window_size)  # Wait for next window
            attempt += 1

    async def try_acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Try to acquire permission without waiting/blocking.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of requests allowed in the window
            rate: Rate limit (requests per second) - used to calculate window size

        Returns:
            True if request is allowed, False if rate limited
        """
        window_size = 1.0  # Default to 1 second window
        if rate < 1 and rate > 0:
            window_size = 1.0 / rate  # For rates less than 1 per second, use larger window

        self.logger.debug(f"Trying to acquire token for key '{key}' (capacity={capacity}, window_size={window_size:.2f}s)")
        result = await self.script(keys=[key], args=[capacity, window_size])
        success = int(result) == 0
        self.logger.debug(f"{'Token acquired' if success else 'Rate limited'} for key '{key}'")
        return success 