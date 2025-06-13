import asyncio
import pytest
import pytest_asyncio
import redis.asyncio as redis
from xrl import TokenBucketRateLimiter
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
    """Create a TokenBucketRateLimiter instance with real Redis client."""
    return TokenBucketRateLimiter(redis_client)


@pytest.mark.integration
class TestTokenBucketIntegration:
    """Integration tests for TokenBucketRateLimiter with real Redis."""

    @pytest.mark.asyncio
    async def test_basic_token_acquisition(self, rate_limiter, redis_client):
        """Test basic token acquisition with real Redis."""
        key = "test:token_bucket:basic"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Should be able to acquire tokens up to capacity immediately
        capacity = 5
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=1.0)
            assert result is True, f"Token {i+1} should be acquired immediately"

        # Should be rate limited after exhausting capacity
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=1.0)
        assert result is False, "Should be rate limited after exhausting capacity"

    @pytest.mark.asyncio
    async def test_token_refill_over_time(self, rate_limiter, redis_client):
        """Test that tokens refill over time in token bucket."""
        key = "test:token_bucket:refill"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire all tokens immediately (capacity=2)
        capacity = 2
        rate = 2.0  # 2 tokens per second (0.5 seconds per token)

        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            assert result is True, f"Token {i+1} should be acquired immediately"

        # Should be rate limited after exhausting capacity
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is False, "Should be rate limited after exhausting capacity"

        # Wait for token refill - since Redis TIME has second precision,
        # we need to wait at least 1 full second plus buffer
        await asyncio.sleep(1.1)

        # Should be able to acquire token again after refill
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "Should be able to acquire token after refill"

    @pytest.mark.asyncio
    async def test_acquire_token_with_waiting(self, rate_limiter, redis_client):
        """Test acquire_token method that waits for tokens."""
        key = "test:token_bucket:waiting"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire the single token immediately (capacity=1)
        capacity = 1
        rate = 1.0  # 1 token per second (1 second per token)

        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "First token should be acquired immediately"

        # This should wait and then succeed
        start_time = time.time()
        result = await rate_limiter.acquire_token(key, capacity=capacity, refill_rate=rate)
        end_time = time.time()

        assert result is True, "Token should be acquired after waiting"
        # Should have waited approximately 1 second (1/rate)
        elapsed = end_time - start_time
        assert 0.8 <= elapsed <= 1.3, f"Should wait ~1.0s, but waited {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_different_users_independent(self, rate_limiter, redis_client):
        """Test that different users have independent rate limits."""
        key1 = "test:token_bucket:user1"
        key2 = "test:token_bucket:user2"

        # Clean up any existing data
        await redis_client.delete(key1, f"{key1}:timestamp")
        await redis_client.delete(key2, f"{key2}:timestamp")

        # Both users should be able to acquire tokens
        result1 = await rate_limiter.try_acquire_token(key1, capacity=1, refill_rate=1.0)
        result2 = await rate_limiter.try_acquire_token(key2, capacity=1, refill_rate=1.0)

        assert result1 is True
        assert result2 is True

        # Both should be rate limited for second attempt
        result1 = await rate_limiter.try_acquire_token(key1, capacity=1, refill_rate=1.0)
        result2 = await rate_limiter.try_acquire_token(key2, capacity=1, refill_rate=1.0)

        assert result1 is False
        assert result2 is False

    @pytest.mark.asyncio
    async def test_burst_capacity(self, rate_limiter, redis_client):
        """Test burst capacity handling in token bucket."""
        key = "test:token_bucket:burst"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Should be able to acquire multiple tokens up to capacity immediately
        # Use zero refill rate to prevent tokens from being added during the test
        capacity = 5
        rate = 0.0  # No refill during test to ensure exact count

        successful_acquisitions = 0
        for _ in range(capacity + 2):  # Try more than capacity
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            if result:
                successful_acquisitions += 1

        # Should have acquired exactly the capacity number of tokens
        assert successful_acquisitions == capacity

    @pytest.mark.asyncio
    async def test_redis_key_expiration(self, rate_limiter, redis_client):
        """Test that Redis keys have appropriate TTL."""
        key = "test:token_bucket:ttl"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Acquire a token
        await rate_limiter.try_acquire_token(key, capacity=10, refill_rate=1.0)

        # Check that keys exist and have TTL
        tokens_ttl = await redis_client.ttl(key)
        timestamp_ttl = await redis_client.ttl(f"{key}:timestamp")

        assert tokens_ttl > 0
        assert timestamp_ttl > 0
        # TTL should be reasonable (between 60 and 86400 seconds based on Lua script)
        assert 60 <= tokens_ttl <= 86400
        assert 60 <= timestamp_ttl <= 86400

    @pytest.mark.asyncio
    async def test_high_rate_limiting(self, rate_limiter, redis_client):
        """Test high rate scenarios with token bucket."""
        key = "test:token_bucket:high_rate"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Use zero refill rate to prevent tokens from being added during the test
        capacity = 5
        rate = 0.0  # No refill during test to ensure exact count

        # Should be able to acquire tokens up to capacity quickly
        successful_acquisitions = 0
        for _ in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            if result:
                successful_acquisitions += 1

        assert successful_acquisitions == capacity

        # Immediately try to acquire another token - should be rate limited
        # since we just exhausted all tokens
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is False, "Should be rate limited immediately after exhausting capacity"

    @pytest.mark.asyncio
    async def test_concurrent_access_same_key(self, rate_limiter, redis_client):
        """Test concurrent access to the same rate limit key."""
        key = "test:token_bucket:concurrent"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Use a capacity that allows some concurrent requests
        capacity = 10
        rate = 5.0

        # Create multiple concurrent requests
        tasks = []
        for _ in range(15):  # More than capacity
            task = rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # Should have exactly 'capacity' successful acquisitions
        successful_count = sum(1 for result in results if result)
        assert successful_count == capacity

    @pytest.mark.asyncio
    async def test_fractional_rates(self, rate_limiter, redis_client):
        """Test token bucket with fractional rates."""
        key = "test:token_bucket:fractional"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Use a fractional rate: 0.5 tokens per second (1 token every 2 seconds)
        capacity = 1
        rate = 0.5

        # First token should be available immediately
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "First token should be available"

        # Second attempt should be rate limited
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is False, "Should be rate limited"

        # Wait for refill (2+ seconds for 0.5 rate)
        await asyncio.sleep(2.1)

        # Should be able to acquire token again
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "Should be able to acquire token after refill"

    @pytest.mark.asyncio
    async def test_token_bucket_smoothing(self, rate_limiter, redis_client):
        """Test that token bucket provides smooth rate limiting over time."""
        key = "test:token_bucket:smoothing"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Use moderate capacity and rate
        capacity = 3
        rate = 2.0  # 2 tokens per second

        # Acquire all tokens immediately (burst)
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            assert result is True, f"Token {i+1} should be acquired in burst"

        # Should be rate limited now
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is False, "Should be rate limited after burst"

        # Wait for refill (1+ seconds should give at least 2 tokens)
        await asyncio.sleep(1.1)

        # Should be able to acquire tokens again after refill
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "Should acquire token after refill"

        # Should be able to acquire another token since we waited long enough
        result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
        assert result is True, "Should acquire second token after refill"

    @pytest.mark.asyncio
    async def test_large_capacity_burst(self, rate_limiter, redis_client):
        """Test token bucket with large capacity for burst handling."""
        key = "test:token_bucket:large_burst"

        # Clean up any existing data
        await redis_client.delete(key, f"{key}:timestamp")

        # Large capacity allows for significant burst
        # Use zero refill rate to prevent tokens from being added during the test
        capacity = 100
        rate = 0.0  # No refill during test to ensure exact count

        # Should be able to handle large burst immediately
        successful_acquisitions = 0
        for _ in range(capacity + 5):  # Try more than capacity
            result = await rate_limiter.try_acquire_token(key, capacity=capacity, refill_rate=rate)
            if result:
                successful_acquisitions += 1

        # Should have exactly 'capacity' successful acquisitions
        assert successful_acquisitions == capacity, "Should handle exactly the capacity in burst" 