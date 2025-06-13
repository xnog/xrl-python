import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
import redis.asyncio as redis
from xrl import FixedWindowRateLimiter


@pytest_asyncio.fixture
async def redis_client():
    """Create a mock Redis client for testing."""
    mock_redis = AsyncMock(spec=redis.Redis)
    return mock_redis


@pytest_asyncio.fixture
async def rate_limiter(redis_client):
    """Create a FixedWindowRateLimiter instance with mocked Redis client."""
    return FixedWindowRateLimiter(redis_client)


class TestFixedWindowInitialization:
    """Test FixedWindowRateLimiter class initialization."""

    def test_init_with_redis_client(self, redis_client):
        """Test FixedWindowRateLimiter initialization with Redis client."""
        limiter = FixedWindowRateLimiter(redis_client)
        assert limiter.redis == redis_client
        redis_client.register_script.assert_called_once_with(FixedWindowRateLimiter.LUA_SCRIPT)

    def test_lua_script_content(self):
        """Test that the Lua script contains expected components for fixed window."""
        script = FixedWindowRateLimiter.LUA_SCRIPT
        assert "local key = KEYS[1]" in script
        assert "local capacity = tonumber(ARGV[1])" in script
        assert "local window_size = tonumber(ARGV[2])" in script
        assert "window_start" in script
        assert "incr" in script
        assert "return 0" in script  # allowed
        assert "return 1" in script  # rate limited


class TestTryAcquireToken:
    """Test the try_acquire_token method."""

    @pytest.mark.asyncio
    async def test_try_acquire_token_success(self, rate_limiter):
        """Test successful token acquisition."""
        # Mock script to return 0 (allowed)
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.try_acquire_token("user:123", capacity=10, rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_rate_limited(self, rate_limiter):
        """Test token acquisition when rate limited."""
        # Mock script to return 1 (rate limited)
        rate_limiter.script = AsyncMock(return_value=1)

        result = await rate_limiter.try_acquire_token("user:456", capacity=5, rate=2.0)

        assert result is False
        # For rate >= 1, window_size is 1.0
        rate_limiter.script.assert_called_once_with(
            keys=["user:456"], args=[5, 1.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_different_keys(self, rate_limiter):
        """Test that different keys are handled independently."""
        rate_limiter.script = AsyncMock(return_value=0)

        await rate_limiter.try_acquire_token("user:123", capacity=10, rate=1.0)
        await rate_limiter.try_acquire_token("user:456", capacity=20, rate=2.0)

        assert rate_limiter.script.call_count == 2
        calls = rate_limiter.script.call_args_list
        assert calls[0] == ((), {"keys": ["user:123"], "args": [10, 1.0]})
        # For rate >= 1, window_size is 1.0
        assert calls[1] == ((), {"keys": ["user:456"], "args": [20, 1.0]})


class TestAcquireToken:
    """Test the acquire_token method."""

    @pytest.mark.asyncio
    async def test_acquire_token_immediate_success(self, rate_limiter):
        """Test immediate token acquisition without waiting."""
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.acquire_token("user:123", capacity=10, rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_acquire_token_with_retry(self, rate_limiter):
        """Test token acquisition that requires retrying."""
        # First call returns 1 (rate limited), second call returns 0 (allowed)
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=10, rate=2.0)

        assert result is True
        assert rate_limiter.script.call_count == 2
        # Fixed window waits for window_size (1.0 second for rate >= 1)
        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_acquire_token_multiple_retries(self, rate_limiter):
        """Test token acquisition with multiple retries."""
        # Rate limited twice, then allowed
        rate_limiter.script = AsyncMock(side_effect=[1, 1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=5, rate=5.0)

        assert result is True
        assert rate_limiter.script.call_count == 3
        assert mock_sleep.call_count == 2
        # Fixed window waits for window_size (1.0 second for rate >= 1)
        mock_sleep.assert_called_with(1.0)

    @pytest.mark.asyncio
    async def test_acquire_token_low_rate_window_calculation(self, rate_limiter):
        """Test that window size is calculated correctly for low rates."""
        # Rate limited once, then allowed
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            # Rate of 0.5 per second should use window size of 2.0 seconds
            result = await rate_limiter.acquire_token("user:123", capacity=1, rate=0.5)

        assert result is True
        assert rate_limiter.script.call_count == 2
        mock_sleep.assert_called_once_with(2.0)  # 1.0 / 0.5 = 2.0


class TestFixedWindowSpecific:
    """Test fixed window specific behavior."""

    @pytest.mark.asyncio
    async def test_window_size_calculation(self, rate_limiter):
        """Test that window size is calculated correctly."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Test different rates and their expected window sizes
        test_cases = [
            (1.0, 1.0),    # rate=1.0 -> window_size=1.0
            (2.0, 1.0),    # rate=2.0 -> window_size=1.0 (default)
            (0.5, 2.0),    # rate=0.5 -> window_size=2.0
            (0.1, 10.0),   # rate=0.1 -> window_size=10.0
        ]

        for rate, expected_window_size in test_cases:
            await rate_limiter.try_acquire_token("test:key", capacity=10, rate=rate)
            # The window size is passed as the second argument to the script
            last_call = rate_limiter.script.call_args_list[-1]
            assert last_call[1]["args"][1] == expected_window_size

    @pytest.mark.asyncio
    async def test_fixed_window_reset_behavior(self, rate_limiter):
        """Test that fixed window resets at window boundaries."""
        rate_limiter.script = AsyncMock(return_value=0)

        # In fixed window, all requests in the same window share the same limit
        capacity = 5
        rate = 1.0  # 1 second window

        for i in range(capacity):
            result = await rate_limiter.try_acquire_token("user:window", capacity=capacity, rate=rate)
            assert result is True

        # All calls should use the same window size
        assert rate_limiter.script.call_count == capacity
        for call in rate_limiter.script.call_args_list:
            assert call[1]["args"][1] == 1.0  # window_size


class TestRedisIntegration:
    """Test Redis integration aspects."""

    @pytest.mark.asyncio
    async def test_script_registration(self, redis_client):
        """Test that Lua script is registered with Redis."""
        limiter = FixedWindowRateLimiter(redis_client)
        redis_client.register_script.assert_called_once_with(FixedWindowRateLimiter.LUA_SCRIPT)
        assert limiter.script == redis_client.register_script.return_value

    @pytest.mark.asyncio
    async def test_script_execution_parameters(self, rate_limiter):
        """Test that script is called with correct parameters."""
        rate_limiter.script = AsyncMock(return_value=0)

        await rate_limiter.try_acquire_token("test:key", capacity=100, rate=5.5)

        rate_limiter.script.assert_called_once_with(
            keys=["test:key"],
            args=[100, 1.0]  # window_size is 1.0 for rate >= 1
        )


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_zero_capacity(self, rate_limiter):
        """Test behavior with zero capacity."""
        rate_limiter.script = AsyncMock(return_value=1)  # Should be rate limited

        result = await rate_limiter.try_acquire_token("user:123", capacity=0, rate=1.0)

        assert result is False
        rate_limiter.script.assert_called_once_with(
            keys=["user:123"], args=[0, 1.0])

    @pytest.mark.asyncio
    async def test_zero_rate(self, rate_limiter):
        """Test behavior with zero rate."""
        rate_limiter.script = AsyncMock(return_value=0)

        # This should work but with very large window
        result = await rate_limiter.try_acquire_token("user:123", capacity=10, rate=0.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_very_high_rate(self, rate_limiter):
        """Test behavior with very high rate."""
        rate_limiter.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await rate_limiter.acquire_token("user:123", capacity=10, rate=1000.0)

        assert result is True
        # Fixed window uses 1.0 second window for high rates
        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_empty_key(self, rate_limiter):
        """Test behavior with empty key."""
        rate_limiter.script = AsyncMock(return_value=0)

        result = await rate_limiter.try_acquire_token("", capacity=10, rate=1.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(keys=[""], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_special_characters_in_key(self, rate_limiter):
        """Test behavior with special characters in key."""
        rate_limiter.script = AsyncMock(return_value=0)
        special_key = "user:123@domain.com:action/test"

        result = await rate_limiter.try_acquire_token(special_key, capacity=5, rate=2.0)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=[special_key], args=[5, 1.0])


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

        result = await rate_limiter.try_acquire_token("user:rpm", capacity=capacity, rate=rate)

        assert result is True
        # For rate > 1, window_size should be 1.0
        rate_limiter.script.assert_called_once_with(
            keys=["user:rpm"], args=[capacity, 1.0])

    @pytest.mark.asyncio
    async def test_rps_rate_limiting(self, rate_limiter):
        """Test requests per second rate limiting."""
        rate_limiter.script = AsyncMock(return_value=0)

        # 10 RPS
        rps = 10
        capacity = rps

        result = await rate_limiter.try_acquire_token("user:rps", capacity=capacity, rate=rps)

        assert result is True
        rate_limiter.script.assert_called_once_with(
            keys=["user:rps"], args=[capacity, 1.0])

    @pytest.mark.asyncio
    async def test_low_rate_limiting(self, rate_limiter):
        """Test low rate limiting with larger windows."""
        rate_limiter.script = AsyncMock(return_value=0)

        # 1 request per 5 seconds = 0.2 RPS
        rate = 0.2
        capacity = 1

        result = await rate_limiter.try_acquire_token("user:low", capacity=capacity, rate=rate)

        assert result is True
        # For rate < 1, window_size should be 1/rate = 5.0
        rate_limiter.script.assert_called_once_with(
            keys=["user:low"], args=[capacity, 5.0])


class TestConcurrency:
    """Test concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_token_acquisition(self, rate_limiter):
        """Test concurrent token acquisition for same key."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Simulate concurrent requests
        tasks = []
        for _ in range(5):
            task = rate_limiter.try_acquire_token("user:concurrent", capacity=10, rate=5.0)
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
            task = rate_limiter.try_acquire_token(f"user:{i}", capacity=10, rate=2.0)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(results)
        assert rate_limiter.script.call_count == 5


class TestWindowBehavior:
    """Test specific fixed window behavior."""

    @pytest.mark.asyncio
    async def test_window_boundary_behavior(self, rate_limiter):
        """Test behavior at window boundaries."""
        rate_limiter.script = AsyncMock(return_value=0)

        # Fixed window should reset at specific time boundaries
        # This is more of a conceptual test since we're mocking Redis
        capacity = 10
        rate = 1.0  # 1 second window

        # Multiple requests in the same conceptual window
        for _ in range(capacity):
            result = await rate_limiter.try_acquire_token("user:boundary", capacity=capacity, rate=rate)
            assert result is True

        # All requests should use the same window configuration
        assert rate_limiter.script.call_count == capacity
        for call in rate_limiter.script.call_args_list:
            assert call[1]["args"] == [capacity, 1.0] 