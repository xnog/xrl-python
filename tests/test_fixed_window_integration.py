import asyncio
import pytest
import pytest_asyncio
import redis.asyncio as redis
from xrl import FixedWindowRateLimiter
import time


@pytest_asyncio.fixture
async def redis_client():
    """Create a real Redis client for integration testing."""
    client = redis.from_url("redis://localhost:6379", decode_responses=False)
    try:
        # Test connection
        await client.ping()
        yield client
    except redis.ConnectionError:
        pytest.skip("Redis server not available")
    finally:
        await client.close()


@pytest_asyncio.fixture
async def rate_limiter(redis_client):
    """Create a FixedWindowRateLimiter instance with real Redis client."""
    return FixedWindowRateLimiter(redis_client)


@pytest.mark.integration
class TestFixedWindowIntegration:
    """Integration tests for FixedWindowRateLimiter with real Redis."""

    @pytest.mark.asyncio
    async def test_basic_request_limiting(self, rate_limiter, redis_client):
        """Test basic request limiting with real Redis."""
        key = "test:fixed_window:basic"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Should be able to make requests up to capacity in the same window
        capacity = 5
        rate = 1.0  # 1 second window

        successful_requests = 0
        for i in range(capacity + 2):  # Try more than capacity
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            if result:
                successful_requests += 1

        # Should have exactly 'capacity' successful requests
        assert successful_requests == capacity

    @pytest.mark.asyncio
    async def test_window_reset_behavior(self, rate_limiter, redis_client):
        """Test that fixed window resets at window boundaries."""
        key = "test:fixed_window:reset"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Use a small capacity for easier testing
        capacity = 2
        rate = 1.0  # 1 second window

        # Exhaust the current window
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            assert result is True, f"Request {i+1} should succeed in current window"

        # Should be rate limited in the same window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited in same window"

        # Wait for next window (1+ seconds)
        await asyncio.sleep(1.1)

        # Should be able to make requests again in new window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "Should succeed in new window"

    @pytest.mark.asyncio
    async def test_acquire_token_with_waiting(self, rate_limiter, redis_client):
        """Test acquire_token method that waits for next window."""
        key = "test:fixed_window:waiting"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Use capacity of 1 for predictable behavior
        capacity = 1
        rate = 1.0  # 1 second window

        # First request should succeed immediately
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "First request should succeed"

        # This should wait for next window and then succeed
        start_time = time.time()
        result = await rate_limiter.acquire_token(key, capacity=capacity, rate=rate)
        end_time = time.time()

        assert result is True, "Request should succeed after waiting"
        # Should have waited approximately 1 second (window size)
        elapsed = end_time - start_time
        assert 0.8 <= elapsed <= 1.3, f"Should wait ~1.0s, but waited {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_different_users_independent(self, rate_limiter, redis_client):
        """Test that different users have independent windows."""
        key1 = "test:fixed_window:user1"
        key2 = "test:fixed_window:user2"

        # Clean up any existing data
        await redis_client.delete(f"{key1}:*", f"{key2}:*")

        # Both users should be able to make requests
        result1 = await rate_limiter.try_acquire_token(key1, capacity=1, rate=1.0)
        result2 = await rate_limiter.try_acquire_token(key2, capacity=1, rate=1.0)

        assert result1 is True
        assert result2 is True

        # Both should be rate limited for second attempt in same window
        result1 = await rate_limiter.try_acquire_token(key1, capacity=1, rate=1.0)
        result2 = await rate_limiter.try_acquire_token(key2, capacity=1, rate=1.0)

        assert result1 is False
        assert result2 is False

    @pytest.mark.asyncio
    async def test_window_size_calculation(self, rate_limiter, redis_client):
        """Test that window size is calculated correctly for different rates."""
        key_base = "test:fixed_window:window_size"

        test_cases = [
            (1.0, 1.0),    # 1 RPS -> 1 second window
            (2.0, 1.0),    # 2 RPS -> 1 second window (default minimum)
            (0.5, 2.0),    # 0.5 RPS -> 2 second window
            (0.1, 10.0),   # 0.1 RPS -> 10 second window
        ]

        for i, (rate, expected_window) in enumerate(test_cases):
            key = f"{key_base}:{i}"
            await redis_client.delete(f"{key}:*")

            # Make a request to establish the window
            result = await rate_limiter.try_acquire_token(key, capacity=1, rate=rate)
            assert result is True, f"First request should succeed for rate {rate}"

            # For rates < 1, we can test the window size by waiting less than the window
            if rate < 1.0:
                # Should be rate limited immediately since we already made one request
                result = await rate_limiter.try_acquire_token(key, capacity=1, rate=rate)
                assert result is False, f"Should be rate limited in same window for rate {rate}"

    @pytest.mark.asyncio
    async def test_concurrent_access_same_key(self, rate_limiter, redis_client):
        """Test concurrent access to the same rate limit key."""
        key = "test:fixed_window:concurrent"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Use a capacity that allows some concurrent requests
        # Use a very low rate to create a large window (60 seconds) to avoid boundary issues
        capacity = 10
        rate = 0.1  # 0.1 RPS = 10 second window, large enough to avoid timing issues

        # Create multiple concurrent requests
        tasks = []
        for _ in range(15):  # More than capacity
            task = rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # Should have exactly 'capacity' successful requests
        successful_count = sum(1 for result in results if result)
        assert successful_count == capacity

    @pytest.mark.asyncio
    async def test_redis_key_expiration(self, rate_limiter, redis_client):
        """Test that Redis keys have appropriate TTL."""
        key = "test:fixed_window:ttl"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Make a request to create the window key
        await rate_limiter.try_acquire_token(key, capacity=10, rate=1.0)

        # Find the window key (it will have a timestamp suffix)
        keys = await redis_client.keys(f"{key}:*")
        assert len(keys) > 0, "Should have created a window key"

        # Check that the key has TTL
        window_key = keys[0].decode() if isinstance(keys[0], bytes) else keys[0]
        ttl = await redis_client.ttl(window_key)
        
        assert ttl > 0, "Window key should have TTL"
        # TTL should be reasonable (equal to window size, which is 1 second for rate=1.0)
        assert ttl <= 2, "TTL should be close to window size"

    @pytest.mark.asyncio
    async def test_large_window_behavior(self, rate_limiter, redis_client):
        """Test behavior with large windows (low rates)."""
        key = "test:fixed_window:large_window"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Very low rate creates large window
        capacity = 5
        rate = 0.2  # 0.2 RPS = 5 second window

        # Should be able to make all requests immediately in the same window
        successful_requests = 0
        for _ in range(capacity + 2):  # Try more than capacity
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            if result:
                successful_requests += 1

        assert successful_requests == capacity

        # Should be rate limited for additional requests in same window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited in same large window"

    @pytest.mark.asyncio
    async def test_window_boundary_precision(self, rate_limiter, redis_client):
        """Test precision of window boundaries."""
        key = "test:fixed_window:boundary"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # Use 1 second window for predictable boundaries
        capacity = 1
        rate = 1.0

        # Make first request
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "First request should succeed"

        # Should be rate limited in same window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited in same window"

        # Wait for window boundary (slightly more than 1 second)
        await asyncio.sleep(1.1)

        # Should succeed in new window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "Should succeed in new window"

    @pytest.mark.asyncio
    async def test_high_capacity_window(self, rate_limiter, redis_client):
        """Test fixed window with high capacity."""
        key = "test:fixed_window:high_capacity"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        # High capacity allows many requests in the same window
        capacity = 100
        rate = 10.0  # 10 RPS, 1 second window

        # Should be able to make all requests immediately
        successful_requests = 0
        for _ in range(capacity + 5):  # Try more than capacity
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            if result:
                successful_requests += 1

        # Should have exactly 'capacity' successful requests
        assert successful_requests == capacity, "Should handle exactly the capacity in single window"

    @pytest.mark.asyncio
    async def test_multiple_windows_over_time(self, rate_limiter, redis_client):
        """Test behavior across multiple windows over time."""
        key = "test:fixed_window:multiple_windows"

        # Clean up any existing data
        await redis_client.delete(f"{key}:*")

        capacity = 2
        rate = 2.0  # 2 RPS, 1 second window

        # First window - exhaust capacity
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            assert result is True, f"Request {i+1} should succeed in first window"

        # Should be rate limited in first window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited in first window"

        # Wait for second window
        await asyncio.sleep(1.1)

        # Second window - should have full capacity again
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
            assert result is True, f"Request {i+1} should succeed in second window"

        # Should be rate limited in second window
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited in second window" 