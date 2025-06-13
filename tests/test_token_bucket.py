import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
import redis.asyncio as redis
from xrl import TokenBucketRateLimiter


@pytest_asyncio.fixture
async def redis_client():
    """Create a mock Redis client for testing."""
    mock_redis = AsyncMock(spec=redis.Redis)
    return mock_redis


@pytest_asyncio.fixture
async def rate_limiter(redis_client):
    """Create a TokenBucketRateLimiter instance with mocked Redis client."""
    return TokenBucketRateLimiter(redis_client)


class TestTokenBucketInitialization:
    """Test TokenBucketRateLimiter class initialization."""

    def test_init_with_redis_client(self, redis_client):
        """Test TokenBucketRateLimiter initialization with Redis client."""
        limiter = TokenBucketRateLimiter(redis_client)
        assert limiter.redis == redis_client
        redis_client.register_script.assert_called_once_with(TokenBucketRateLimiter.LUA_SCRIPT)

    def test_lua_script_content(self):
        """Test that the Lua script contains expected components for token bucket."""
        script = TokenBucketRateLimiter.LUA_SCRIPT
        assert "local key = KEYS[1]" in script
        assert "local capacity = tonumber(ARGV[1])" in script
        assert "local refill_rate = tonumber(ARGV[2])" in script
        assert "tokens" in script
        assert "timestamp" in script
        assert "return 0" in script  # allowed
        assert "return 1" in script  # rate limited


class TestTryAcquireToken:
    """Test the try_acquire_token method."""

    @pytest.mark.asyncio
    async def test_try_acquire_token_success(self, rate_limiter):
        """Test successful token acquisition."""
        # Mock script to return 0 (allowed)
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.try_acquire_token("user:123", capacity=10, refill_rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_rate_limited(self, rate_limiter):
        """Test token acquisition when rate limited."""
        # Mock script to return 1 (rate limited)
        rate_limiter.script = AsyncMock(return_value=1)

        result = await rate_limiter.try_acquire_token("user:456", capacity=5, refill_rate=2.0)

        assert result is False
        rate_limiter.script.assert_called_once_with(
            keys=["user:456"], args=[5, 2.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_different_keys(self, rate_limiter):
        """Test that different keys are handled independently."""
        rate_limiter.script = AsyncMock(return_value=0)

        await rate_limiter.try_acquire_token("user:123", capacity=10, refill_rate=1.0)
        await rate_limiter.try_acquire_token("user:456", capacity=20, refill_rate=2.0)

        assert rate_limiter.script.call_count == 2
        calls = rate_limiter.script.call_args_list
        assert calls[0] == ((), {"keys": ["user:123"], "args": [10, 1.0]})
        assert calls[1] == ((), {"keys": ["user:456"], "args": [20, 2.0]})


class TestAcquireToken:
    """Test the acquire_token method."""

    @pytest.mark.asyncio
    async def test_acquire_token_immediate_success(self, rate_limiter):
        """Test immediate token acquisition without waiting."""
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.acquire_token("user:123", capacity=10, refill_rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_acquire_token_with_retry(self, rate_limiter):
        """Test token acquisition that requires retrying."""
        # First call returns 1 (rate limited), second call returns 0 (allowed)
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=10, refill_rate=2.0)

        assert result is True
        assert rate_limiter.script.call_count == 2
        mock_sleep.assert_called_once_with(0.5)  # 1.0 / 2.0 = 0.5

    @pytest.mark.asyncio
    async def test_acquire_token_multiple_retries(self, rate_limiter):
        """Test token acquisition with multiple retries."""
        # Rate limited twice, then allowed
        rate_limiter.script = AsyncMock(side_effect=[1, 1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=5, refill_rate=5.0)

        assert result is True
        assert rate_limiter.script.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.2)  # 1.0 / 5.0 = 0.2

    @pytest.mark.asyncio
    async def test_acquire_token_retry_interval_calculation(self, rate_limiter):
        """Test that retry interval is calculated correctly for different rates."""
        test_cases = [
            (1.0, 1.0),      # rate=1.0 -> interval=1.0
            (2.0, 0.5),      # rate=2.0 -> interval=0.5
            (10.0, 0.1),     # rate=10.0 -> interval=0.1
            (100/60, 0.6),   # rate=100/60 -> interval=0.6
        ]

        for rate, expected_interval in test_cases:
            # Create fresh mock for each iteration
            rate_limiter.script = AsyncMock(side_effect=[1, 0])
            with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                await rate_limiter.acquire_token("user:test", capacity=10, refill_rate=rate)
                mock_sleep.assert_called_once_with(expected_interval)


class TestTokenBucketSpecific:
    """Test token bucket specific behavior."""

    @pytest.mark.asyncio
    async def test_token_refill_behavior(self, rate_limiter):
        """Test that token bucket allows burst up to capacity."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Token bucket should allow immediate burst up to capacity
        capacity = 10
        rate = 1.0
        
        for i in range(capacity):
            result = await rate_limiter.try_acquire_token("user:burst", capacity=capacity, refill_rate=rate)
            assert result is True

        # Verify all calls were made
        assert rate_limiter.script.call_count == capacity

    @pytest.mark.asyncio
    async def test_fractional_rates(self, rate_limiter):
        """Test token bucket with fractional rates."""
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            # Rate of 0.5 tokens per second = 2 seconds per token
            await rate_limiter.acquire_token("user:fractional", capacity=1, refill_rate=0.5)

        mock_sleep.assert_called_once_with(2.0)  # 1.0 / 0.5 = 2.0


class TestRedisIntegration:
    """Test Redis integration aspects."""

    @pytest.mark.asyncio
    async def test_script_registration(self, redis_client):
        """Test that Lua script is registered with Redis."""
        limiter = TokenBucketRateLimiter(redis_client)
        redis_client.register_script.assert_called_once_with(TokenBucketRateLimiter.LUA_SCRIPT)
        assert limiter.script == redis_client.register_script.return_value

    @pytest.mark.asyncio
    async def test_script_execution_parameters(self, rate_limiter):
        """Test that script is called with correct parameters."""
        rate_limiter.script = AsyncMock(return_value=0)

        await rate_limiter.try_acquire_token("test:key", capacity=100, refill_rate=5.5)

        rate_limiter.script.assert_called_once_with(
            keys=["test:key"],
            args=[100, 5.5]
        )


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_zero_capacity(self, rate_limiter):
        """Test behavior with zero capacity."""
        rate_limiter.script = AsyncMock(return_value=1)  # Should be rate limited

        result = await rate_limiter.try_acquire_token("user:123", capacity=0, refill_rate=1.0)

        assert result is False
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[0, 1.0])

    @pytest.mark.asyncio
    async def test_zero_rate(self, rate_limiter):
        """Test behavior with zero rate."""
        rate_limiter.script = AsyncMock(return_value=0)

        # This should work but with infinite retry interval
        result = await rate_limiter.try_acquire_token("user:123", capacity=10, refill_rate=0.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_very_high_rate(self, rate_limiter):
        """Test behavior with very high rate."""
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=10, refill_rate=1000.0)

        assert result is True
        mock_sleep.assert_called_once_with(0.001)  # 1.0 / 1000.0

    @pytest.mark.asyncio
    async def test_empty_key(self, rate_limiter):
        """Test behavior with empty key."""
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.try_acquire_token("", capacity=10, refill_rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(keys=[""], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_special_characters_in_key(self, rate_limiter):
        """Test behavior with special characters in key."""
        rate_limiter.script = AsyncMock(return_value=0)
        special_key = "user:123@domain.com:action/test"

        result = await rate_limiter.try_acquire_token(special_key, capacity=5, refill_rate=2.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=[special_key], args=[5, 2.0])


class TestRealWorldScenarios:
    """Test real-world rate limiting scenarios."""

    @pytest.mark.asyncio
    async def test_rpm_rate_limiting(self, rate_limiter):
        """Test requests per minute rate limiting."""
        rate_limiter.script = AsyncMock(return_value=0)

        # 100 RPM = 100/60 ≈ 1.67 requests per second
        rpm = 100
        rate = rpm / 60.0
        capacity = rpm

        result = await rate_limiter.try_acquire_token("user:rpm", capacity=capacity, refill_rate=rate)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:rpm"], args=[capacity, rate])

    @pytest.mark.asyncio
    async def test_rps_rate_limiting(self, rate_limiter):
        """Test requests per second rate limiting."""
        rate_limiter.script = AsyncMock(return_value=0)

        # 10 RPS
        rps = 10
        capacity = rps

        result = await rate_limiter.try_acquire_token("user:rps", capacity=capacity, refill_rate=rps)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:rps"], args=[capacity, rps])

    @pytest.mark.asyncio
    async def test_burst_handling(self, rate_limiter):
        """Test burst capacity handling in token bucket."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Token bucket allows burst up to capacity
        capacity = 50
        rate = 10.0  # 10 tokens per second

        # Should allow immediate burst
        for _ in range(capacity):
            result = await rate_limiter.try_acquire_token("user:burst", capacity=capacity, refill_rate=rate)
            assert result is True

        assert rate_limiter.script.call_count == capacity


class TestConcurrency:
    """Test concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_token_acquisition(self, rate_limiter):
        """Test concurrent token acquisition for same key."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Simulate concurrent requests
        tasks = []
        for _ in range(5):
            task = rate_limiter.try_acquire_token("user:concurrent", capacity=10, refill_rate=5.0)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # All should succeed (mocked to return 0)
        assert all(results)
        assert rate_limiter.script.call_count == 5

    @pytest.mark.asyncio
    async def test_concurrent_different_keys(self, rate_limiter):
        """Test concurrent requests for different keys."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Simulate concurrent requests for different users
        tasks = []
        for i in range(5):
            task = rate_limiter.try_acquire_token(f"user:{i}", capacity=10, refill_rate=2.0)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(results)
        assert rate_limiter.script.call_count == 5 