import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import redis.asyncio as redis
from xrl import XRL


@pytest_asyncio.fixture
async def redis_client():
    """Create a mock Redis client for testing."""
    mock_redis = AsyncMock(spec=redis.Redis)
    return mock_redis


@pytest_asyncio.fixture
async def xrl_instance(redis_client):
    """Create an XRL instance with mocked Redis client."""
    return XRL(redis_client)


class TestXRLInitialization:
    """Test XRL class initialization."""

    def test_init_with_redis_client(self, redis_client):
        """Test XRL initialization with Redis client."""
        xrl = XRL(redis_client)
        assert xrl.redis == redis_client
        redis_client.register_script.assert_called_once_with(XRL.LUA_SCRIPT)

    def test_lua_script_content(self):
        """Test that the Lua script contains expected components."""
        script = XRL.LUA_SCRIPT
        assert "local key = KEYS[1]" in script
        assert "local capacity = tonumber(ARGV[1])" in script
        assert "local rate = tonumber(ARGV[2])" in script
        assert "token bucket" in script.lower() or "tokens" in script
        assert "return 0" in script  # allowed
        assert "return 1" in script  # rate limited


class TestTryAcquireToken:
    """Test the try_acquire_token method."""

    @pytest.mark.asyncio
    async def test_try_acquire_token_success(self, xrl_instance):
        """Test successful token acquisition."""
        # Mock script to return 0 (allowed)
        xrl_instance.script = AsyncMock(return_value=0)

        result = await xrl_instance.try_acquire_token("user:123", capacity=10, rate=1.0)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_rate_limited(self, xrl_instance):
        """Test token acquisition when rate limited."""
        # Mock script to return 1 (rate limited)
        xrl_instance.script = AsyncMock(return_value=1)

        result = await xrl_instance.try_acquire_token("user:456", capacity=5, rate=2.0)

        assert result is False
        xrl_instance.script.assert_called_once_with(
            keys=["user:456"], args=[5, 2.0])

    @pytest.mark.asyncio
    async def test_try_acquire_token_different_keys(self, xrl_instance):
        """Test that different keys are handled independently."""
        xrl_instance.script = AsyncMock(return_value=0)

        await xrl_instance.try_acquire_token("user:123", capacity=10, rate=1.0)
        await xrl_instance.try_acquire_token("user:456", capacity=20, rate=2.0)

        assert xrl_instance.script.call_count == 2
        calls = xrl_instance.script.call_args_list
        assert calls[0] == ((), {"keys": ["user:123"], "args": [10, 1.0]})
        assert calls[1] == ((), {"keys": ["user:456"], "args": [20, 2.0]})


class TestAcquireToken:
    """Test the acquire_token method."""

    @pytest.mark.asyncio
    async def test_acquire_token_immediate_success(self, xrl_instance):
        """Test immediate token acquisition without waiting."""
        xrl_instance.script = AsyncMock(return_value=0)

        result = await xrl_instance.acquire_token("user:123", capacity=10, rate=1.0)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:123"], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_acquire_token_with_retry(self, xrl_instance):
        """Test token acquisition that requires retrying."""
        # First call returns 1 (rate limited), second call returns 0 (allowed)
        xrl_instance.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await xrl_instance.acquire_token("user:123", capacity=10, rate=2.0)

        assert result is True
        assert xrl_instance.script.call_count == 2
        mock_sleep.assert_called_once_with(0.5)  # 1.0 / 2.0 = 0.5

    @pytest.mark.asyncio
    async def test_acquire_token_multiple_retries(self, xrl_instance):
        """Test token acquisition with multiple retries."""
        # Rate limited twice, then allowed
        xrl_instance.script = AsyncMock(side_effect=[1, 1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await xrl_instance.acquire_token("user:123", capacity=5, rate=5.0)

        assert result is True
        assert xrl_instance.script.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.2)  # 1.0 / 5.0 = 0.2

    @pytest.mark.asyncio
    async def test_acquire_token_retry_interval_calculation(self, xrl_instance):
        """Test that retry interval is calculated correctly for different rates."""
        test_cases = [
            (1.0, 1.0),      # rate=1.0 -> interval=1.0
            (2.0, 0.5),      # rate=2.0 -> interval=0.5
            (10.0, 0.1),     # rate=10.0 -> interval=0.1
            (100/60, 0.6),   # rate=100/60 -> interval=0.6
        ]

        for rate, expected_interval in test_cases:
            # Create fresh mock for each iteration
            xrl_instance.script = AsyncMock(side_effect=[1, 0])
            with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                await xrl_instance.acquire_token("user:test", capacity=10, rate=rate)
                mock_sleep.assert_called_once_with(expected_interval)


class TestRedisIntegration:
    """Test Redis integration aspects."""

    @pytest.mark.asyncio
    async def test_script_registration(self, redis_client):
        """Test that Lua script is registered with Redis."""
        xrl = XRL(redis_client)
        redis_client.register_script.assert_called_once_with(XRL.LUA_SCRIPT)
        assert xrl.script == redis_client.register_script.return_value

    @pytest.mark.asyncio
    async def test_script_execution_parameters(self, xrl_instance):
        """Test that script is called with correct parameters."""
        xrl_instance.script = AsyncMock(return_value=0)

        await xrl_instance.try_acquire_token("test:key", capacity=100, rate=5.5)

        xrl_instance.script.assert_called_once_with(
            keys=["test:key"],
            args=[100, 5.5]
        )


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_zero_capacity(self, xrl_instance):
        """Test behavior with zero capacity."""
        xrl_instance.script = AsyncMock(
            return_value=1)  # Should be rate limited

        result = await xrl_instance.try_acquire_token("user:123", capacity=0, rate=1.0)

        assert result is False
        xrl_instance.script.assert_called_once_with(
            keys=["user:123"], args=[0, 1.0])

    @pytest.mark.asyncio
    async def test_zero_rate(self, xrl_instance):
        """Test behavior with zero rate."""
        xrl_instance.script = AsyncMock(return_value=0)

        # This should work but with infinite retry interval
        result = await xrl_instance.try_acquire_token("user:123", capacity=10, rate=0.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_very_high_rate(self, xrl_instance):
        """Test behavior with very high rate."""
        xrl_instance.script = AsyncMock(side_effect=[1, 0])

        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            result = await xrl_instance.acquire_token("user:123", capacity=10, rate=1000.0)

        assert result is True
        mock_sleep.assert_called_once_with(0.001)  # 1.0 / 1000.0

    @pytest.mark.asyncio
    async def test_fractional_capacity(self, xrl_instance):
        """Test behavior with fractional capacity."""
        xrl_instance.script = AsyncMock(return_value=0)

        result = await xrl_instance.try_acquire_token("user:123", capacity=10.5, rate=1.0)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:123"], args=[10.5, 1.0])

    @pytest.mark.asyncio
    async def test_empty_key(self, xrl_instance):
        """Test behavior with empty key."""
        xrl_instance.script = AsyncMock(return_value=0)

        result = await xrl_instance.try_acquire_token("", capacity=10, rate=1.0)

        assert result is True
        xrl_instance.script.assert_called_once_with(keys=[""], args=[10, 1.0])

    @pytest.mark.asyncio
    async def test_special_characters_in_key(self, xrl_instance):
        """Test behavior with special characters in key."""
        xrl_instance.script = AsyncMock(return_value=0)
        special_key = "user:123@domain.com:action/test"

        result = await xrl_instance.try_acquire_token(special_key, capacity=10, rate=1.0)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=[special_key], args=[10, 1.0])


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""

    @pytest.mark.asyncio
    async def test_rpm_rate_limiting(self, xrl_instance):
        """Test requests per minute (RPM) rate limiting."""
        xrl_instance.script = AsyncMock(return_value=0)

        # 100 RPM = 100/60 ≈ 1.67 tokens per second
        rpm = 100
        rate = rpm / 60

        result = await xrl_instance.try_acquire_token("user:123", capacity=100, rate=rate)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:123"],
            args=[100, pytest.approx(1.6667, rel=1e-3)]
        )

    @pytest.mark.asyncio
    async def test_rps_rate_limiting(self, xrl_instance):
        """Test requests per second (RPS) rate limiting."""
        xrl_instance.script = AsyncMock(return_value=0)

        # 50 RPS
        rps = 50

        result = await xrl_instance.try_acquire_token("user:456", capacity=50, rate=rps)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:456"], args=[50, 50])

    @pytest.mark.asyncio
    async def test_burst_handling(self, xrl_instance):
        """Test burst capacity handling."""
        xrl_instance.script = AsyncMock(return_value=0)

        # High capacity for burst, lower rate for sustained usage
        result = await xrl_instance.try_acquire_token("user:789", capacity=1000, rate=10)

        assert result is True
        xrl_instance.script.assert_called_once_with(
            keys=["user:789"], args=[1000, 10])


class TestConcurrency:
    """Test concurrent access scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_token_acquisition(self, xrl_instance):
        """Test concurrent token acquisition for same key."""
        xrl_instance.script = AsyncMock(return_value=0)

        # Simulate concurrent requests
        tasks = [
            xrl_instance.try_acquire_token("user:123", capacity=10, rate=1.0)
            for _ in range(5)
        ]

        results = await asyncio.gather(*tasks)

        assert all(results)  # All should succeed in this mock scenario
        assert xrl_instance.script.call_count == 5

    @pytest.mark.asyncio
    async def test_concurrent_different_keys(self, xrl_instance):
        """Test concurrent token acquisition for different keys."""
        xrl_instance.script = AsyncMock(return_value=0)

        # Simulate concurrent requests for different users
        tasks = [
            xrl_instance.try_acquire_token(f"user:{i}", capacity=10, rate=1.0)
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        assert all(results)
        assert xrl_instance.script.call_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
