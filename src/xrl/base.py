from abc import ABC, abstractmethod
import redis.asyncio as redis
import logging

class BaseRateLimiter(ABC):
    """
    Base class for all rate limiter implementations.
    Defines the common interface that all rate limiters must implement.
    """

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize the rate limiter.

        Args:
            redis_client: redis.asyncio.Redis instance
        """
        self.redis = redis_client
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Checks and waits for token availability.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of requests allowed
            rate: Rate limit (requests per second)

        Returns:
            True when a request is allowed
        """
        pass

    @abstractmethod
    async def try_acquire_token(self, key: str, capacity: int, rate: float) -> bool:
        """
        Try to acquire permission without waiting/blocking.

        Args:
            key: Unique identifier per user/action
            capacity: Maximum number of requests allowed
            rate: Rate limit (requests per second)

        Returns:
            True if request is allowed, False if rate limited
        """
        pass 