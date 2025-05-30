import asyncio
import pytest
import pytest_asyncio
import redis.asyncio as redis
from xrl import XRL
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
async def xrl_instance(redis_client):
    """Create an XRL instance with real Redis client."""
    return XRL(redis_client)


@pytest.mark.integration
class TestXRLIntegration:
    """Integration tests with real Redis."""

    @pytest.mark.asyncio
    async def test_basic_token_acquisition(self, xrl_instance, redis_client):
        """Test basic token acquisition with real Redis."""
        key = "test:basic:token"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Should be able to acquire tokens up to capacity immediately
        capacity = 5
        for i in range(capacity):
            result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=1.0)
            assert result is True, f"Token {i+1} should be acquired immediately"

        # Should be rate limited after exhausting capacity
        result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=1.0)
        assert result is False, "Should be rate limited after exhausting capacity"

    @pytest.mark.asyncio
    async def test_token_refill_over_time(self, xrl_instance, redis_client):
        """Test that tokens refill over time."""
        key = "test:refill:token"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire all tokens immediately (capacity=2)
        capacity = 2
        rate = 2.0  # 2 tokens per second (0.5 seconds per token)

        for i in range(capacity):
            result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
            assert result is True, f"Token {i+1} should be acquired immediately"

        # Should be rate limited after exhausting capacity
        result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited after exhausting capacity"

        # Wait for token refill - since Redis TIME has second precision,
        # we need to wait at least 1 full second plus buffer
        await asyncio.sleep(1.1)

        # Should be able to acquire token again after refill
        result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "Should be able to acquire token after refill"

    @pytest.mark.asyncio
    async def test_acquire_token_with_waiting(self, xrl_instance, redis_client):
        """Test acquire_token method that waits for tokens."""
        key = "test:waiting:token"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire the single token immediately (capacity=1)
        capacity = 1
        rate = 1.0  # 1 token per second (1 second per token)

        result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is True, "First token should be acquired immediately"

        # This should wait and then succeed
        start_time = time.time()
        result = await xrl_instance.acquire_token(key, capacity=capacity, rate=rate)
        end_time = time.time()

        assert result is True, "Token should be acquired after waiting"
        # Should have waited approximately 1 second (1/rate)
        elapsed = end_time - start_time
        assert 0.8 <= elapsed <= 1.3, f"Should wait ~1.0s, but waited {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_different_users_independent(self, xrl_instance, redis_client):
        """Test that different users have independent rate limits."""
        key1 = "test:user1:independent"
        key2 = "test:user2:independent"

        # Clean up any existing data
        await redis_client.delete(key1, f"{key1}:timestamp")
        await redis_client.delete(key2, f"{key2}:timestamp")

        # Both users should be able to acquire tokens
        result1 = await xrl_instance.try_acquire_token(key1, capacity=1, rate=1.0)
        result2 = await xrl_instance.try_acquire_token(key2, capacity=1, rate=1.0)

        assert result1 is True
        assert result2 is True

        # Both should be rate limited for second attempt
        result1 = await xrl_instance.try_acquire_token(key1, capacity=1, rate=1.0)
        result2 = await xrl_instance.try_acquire_token(key2, capacity=1, rate=1.0)

        assert result1 is False
        assert result2 is False

    @pytest.mark.asyncio
    async def test_burst_capacity(self, xrl_instance, redis_client):
        """Test burst capacity handling."""
        key = "test:burst:capacity"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Should be able to acquire multiple tokens up to capacity
        capacity = 5
        rate = 1.0

        successful_acquisitions = 0
        for _ in range(capacity + 2):  # Try more than capacity
            result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
            if result:
                successful_acquisitions += 1

        # Should have acquired exactly the capacity number of tokens
        assert successful_acquisitions == capacity

    @pytest.mark.asyncio
    async def test_redis_key_expiration(self, xrl_instance, redis_client):
        """Test that Redis keys have appropriate TTL."""
        key = "test:ttl:expiration"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire a token
        await xrl_instance.try_acquire_token(key, capacity=10, rate=1.0)

        # Check that keys exist and have TTL
        tokens_ttl = await redis_client.ttl(key)
        timestamp_ttl = await redis_client.ttl(f"{key}:timestamp")

        assert tokens_ttl > 0
        assert timestamp_ttl > 0
        # TTL should be reasonable (between 60 and 86400 seconds based on Lua script)
        assert 60 <= tokens_ttl <= 86400
        assert 60 <= timestamp_ttl <= 86400

    @pytest.mark.asyncio
    async def test_high_rate_limiting(self, xrl_instance, redis_client):
        """Test rate limiting scenarios."""
        key = "test:high:rate"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Use a conservative rate for testing: 1 token per second
        # This ensures no token refill during the brief test execution time
        capacity = 5
        rate = 1.0

        # Should be able to acquire tokens up to capacity quickly
        successful_acquisitions = 0
        for _ in range(capacity):
            result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
            if result:
                successful_acquisitions += 1

        assert successful_acquisitions == capacity

        # Immediately try to acquire another token - should be rate limited
        # since we just exhausted all tokens
        result = await xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
        assert result is False, "Should be rate limited immediately after exhausting capacity"

    @pytest.mark.asyncio
    async def test_concurrent_access_same_key(self, xrl_instance, redis_client):
        """Test concurrent access to the same rate limit key."""
        key = "test:concurrent:same"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        capacity = 10
        rate = 1.0
        concurrent_requests = 15

        # Create concurrent tasks
        tasks = [
            xrl_instance.try_acquire_token(key, capacity=capacity, rate=rate)
            for _ in range(concurrent_requests)
        ]

        results = await asyncio.gather(*tasks)
        successful_acquisitions = sum(1 for result in results if result)

        # Should have acquired exactly the capacity number of tokens
        assert successful_acquisitions == capacity
        assert sum(
            1 for result in results if not result) == concurrent_requests - capacity


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
