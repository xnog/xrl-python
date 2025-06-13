import asyncio
import redis.asyncio as redis
import logging

class FixedWindowRateLimiter:
    """
    Fixed Window Rate Limiter - A distributed rate limiter using Redis fixed window algorithm.

    This class provides rate limiting functionality with a fixed time window.
    It uses Redis Lua scripts for atomic operations to ensure consistency in distributed environments.
    """

    LUA_SCRIPT = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local window_size = tonumber(ARGV[2])  -- window size in seconds

    -- Get current window start time
    local now = tonumber(redis.call("time")[1])
    local window_start = math.floor(now / window_size) * window_size
    local window_key = key .. ":" .. window_start

    -- Get current count for this window
    local count = tonumber(redis.call("get", window_key)) or 0

    if count < limit then
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
        self.redis = redis_client
        self.logger = logging.getLogger(self.__class__.__name__)
        self.script = redis_client.register_script(self.LUA_SCRIPT)

    async def acquire_request(self, key: str, limit: int, window_size: int) -> bool:
        """
        Checks and waits for request availability using a fixed window.

        Args:
            key: Unique identifier per user/action
            limit: Maximum number of requests allowed in the window
            window_size: Window duration in seconds

        Returns:
            True when a request is allowed

        Examples:
            # 100 requests per minute (60 seconds)
            await limiter.acquire_request("user:123", limit=100, window_size=60)

            # 10 requests per 5 seconds
            await limiter.acquire_request("user:456", limit=10, window_size=5)

            # 1000 requests per hour (3600 seconds)
            await limiter.acquire_request("user:789", limit=1000, window_size=3600)
        """
        attempt = 1
        while True:
            self.logger.debug(f"Attempt {attempt} to acquire request for key '{key}' (limit={limit}, window_size={window_size}s)")
            result = await self.script(keys=[key], args=[limit, window_size])
            
            if result == 0:
                self.logger.debug(f"Request allowed for key '{key}' on attempt {attempt}")
                return True  # Request allowed
            
            self.logger.debug(f"Rate limited for key '{key}', waiting {window_size}s for next window")
            await asyncio.sleep(window_size)  # Wait for next window
            attempt += 1

    async def try_acquire_request(self, key: str, limit: int, window_size: int) -> bool:
        """
        Try to acquire permission without waiting/blocking.

        Args:
            key: Unique identifier per user/action
            limit: Maximum number of requests allowed in the window
            window_size: Window duration in seconds

        Returns:
            True if request is allowed, False if rate limited
        """
        self.logger.debug(f"Trying to acquire request for key '{key}' (limit={limit}, window_size={window_size}s)")
        result = await self.script(keys=[key], args=[limit, window_size])
        success = int(result) == 0
        self.logger.debug(f"{'Request allowed' if success else 'Rate limited'} for key '{key}'")
        return success

    # Keep the old methods for backward compatibility but mark them as deprecated
    async def acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        DEPRECATED: Use acquire_request(key, limit, window_size) instead.
        This method tries to convert rate to window_size for backward compatibility.
        """
        # Convert rate to window_size (this is a rough approximation)
        if rate <= 0:
            window_size = 60  # Default to 1 minute
        elif rate >= 1:
            window_size = 1  # 1 second window for high rates
        else:
            window_size = int(1.0 / rate)  # Larger window for low rates
        
        return await self.acquire_request(key, capacity, window_size)

    async def try_acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        DEPRECATED: Use try_acquire_request(key, limit, window_size) instead.
        This method tries to convert rate to window_size for backward compatibility.
        """
        # Convert rate to window_size (this is a rough approximation)
        if rate <= 0:
            window_size = 60  # Default to 1 minute
        elif rate >= 1:
            window_size = 1  # 1 second window for high rates
        else:
            window_size = int(1.0 / rate)  # Larger window for low rates
        
        return await self.try_acquire_request(key, capacity, window_size) 