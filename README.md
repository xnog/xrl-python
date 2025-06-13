# XRL - eXtended Rate Limiter

[![PyPI version](https://badge.fury.io/py/xrl-python.svg)](https://badge.fury.io/py/xrl-python)
[![Python Support](https://img.shields.io/pypi/pyversions/xrl-python.svg)](https://pypi.org/project/xrl-python/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A distributed rate limiter with multiple algorithms (Token Bucket and Fixed Window) for Python applications.

## Features

- **Multiple Algorithms**:
  - **Token Bucket**: Smooth rate limiting with burst capacity
  - **Fixed Window**: Simple time-window based rate limiting
- **Distributed**: Works across multiple application instances using Redis
- **Async/Await Support**: Built for modern Python async applications
- **Configurable**: Flexible capacity and refill rates
- **Atomic Operations**: Uses Redis Lua scripts for consistency
- **Auto-expiring Keys**: Automatic cleanup of unused rate limit keys
- **Debug Logging**: Built-in logging for monitoring rate limit operations

## Installation

```bash
pip install xrl-python
```

## Quick Start

```python
import asyncio
import redis.asyncio as redis
from xrl import TokenBucketRateLimiter, FixedWindowRateLimiter

async def main():
    # Create Redis connection
    redis_client = redis.from_url("redis://localhost")
    
    # Choose your rate limiter implementation
    # Token Bucket (smoother rate limiting with burst capacity)
    limiter = TokenBucketRateLimiter(redis_client)
    
    # Or Fixed Window (simple time-window based)
    # limiter = FixedWindowRateLimiter(redis_client)
    
    # Acquire a token (blocks until available)
    await limiter.acquire_token("user:123", capacity=100, rate=10)
    print("✅ Request allowed!")
    
    # Try to acquire without blocking
    if await limiter.try_acquire_token("user:123", capacity=100, rate=10):
        print("✅ Token acquired immediately!")
    else:
        print("❌ Rate limited")
    
    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
```

## Usage Examples

### Token Bucket Rate Limiter

```python
import asyncio
import redis.asyncio as redis
from xrl import TokenBucketRateLimiter

async def rate_limited_api():
    redis_client = redis.from_url("redis://localhost")
    limiter = TokenBucketRateLimiter(redis_client)
    
    try:
        # 100 requests per minute (100/60 = 1.67 tokens per second)
        await limiter.acquire_token("api:endpoint", capacity=100, rate=100/60)
        
        # Your API logic here
        return {"status": "success"}
        
    finally:
        await redis_client.aclose()
```

### Fixed Window Rate Limiter

```python
import asyncio
import redis.asyncio as redis
from xrl import FixedWindowRateLimiter

async def rate_limited_api():
    redis_client = redis.from_url("redis://localhost")
    limiter = FixedWindowRateLimiter(redis_client)
    
    try:
        # 100 requests per minute
        await limiter.acquire_token("api:endpoint", capacity=100, rate=100/60)
        
        # Your API logic here
        return {"status": "success"}
        
    finally:
        await redis_client.aclose()
```

### Per-User Rate Limiting

```python
async def user_rate_limit(user_id: str):
    redis_client = redis.from_url("redis://localhost")
    limiter = TokenBucketRateLimiter(redis_client)  # or FixedWindowRateLimiter
    
    try:
        # Different limits per user
        user_key = f"user:{user_id}"
        
        # 200 requests per minute for this user
        await limiter.acquire_token(user_key, capacity=200, rate=200/60)
        
        return process_user_request(user_id)
        
    finally:
        await redis_client.aclose()
```

### Non-blocking Rate Limiting

```python
async def try_process_request(request_id: str):
    redis_client = redis.from_url("redis://localhost")
    limiter = TokenBucketRateLimiter(redis_client)  # or FixedWindowRateLimiter
    
    try:
        # Try to acquire token without waiting
        if await limiter.try_acquire_token(f"request:{request_id}", capacity=50, rate=5):
            return await process_request(request_id)
        else:
            return {"error": "Rate limit exceeded", "retry_after": 1}
            
    finally:
        await redis_client.aclose()
```

## API Reference

### BaseRateLimiter Class

Base class for all rate limiter implementations.

#### `__init__(redis_client: redis.Redis)`

Initialize the rate limiter.

**Parameters:**
- `redis_client`: An instance of `redis.asyncio.Redis`

#### `async acquire_token(key: str, capacity: int, rate: float) -> bool`

Acquire a token, waiting if necessary until one becomes available.

**Parameters:**
- `key`: Unique identifier for the rate limit bucket
- `capacity`: Maximum number of tokens/requests allowed
- `rate`: Token refill rate or requests per second

**Returns:**
- `True` when a token is successfully acquired

#### `async try_acquire_token(key: str, capacity: int, rate: float) -> bool`

Try to acquire a token without waiting.

**Parameters:**
- `key`: Unique identifier for the rate limit bucket
- `capacity`: Maximum number of tokens/requests allowed
- `rate`: Token refill rate or requests per second

**Returns:**
- `True` if token was acquired, `False` if rate limited

### TokenBucketRateLimiter

Smooth rate limiting with burst capacity. Best for:
- APIs that need to handle bursts of traffic
- Applications requiring precise rate control
- Scenarios where you want to allow some burst capacity

### FixedWindowRateLimiter

Simple time-window based rate limiting. Best for:
- Simple rate limiting needs
- When burst capacity is not required
- When you want to reset limits at fixed intervals

## Rate Calculation Examples

```python
# 100 requests per minute
rate = 100 / 60  # 1.67 tokens per second

# 500 requests per hour  
rate = 500 / 3600  # 0.139 tokens per second

# 10 requests per second
rate = 10  # 10 tokens per second

# 1 request every 5 seconds
rate = 1 / 5  # 0.2 tokens per second
```

## Redis Configuration

XRL requires a Redis server. The rate limiter uses:

- **Keys**: `{your_key}` and `{your_key}:timestamp` (Token Bucket)
- **Keys**: `{your_key}:{window_start}` (Fixed Window)
- **TTL**: Automatically set based on bucket refill time or window size
- **Memory**: Minimal - only stores token count and timestamp per key

## Error Handling

```python
import redis.exceptions

async def robust_rate_limiting():
    try:
        redis_client = redis.from_url("redis://localhost")
        limiter = TokenBucketRateLimiter(redis_client)  # or FixedWindowRateLimiter
        
        await limiter.acquire_token("key", capacity=100, rate=10)
        
    except redis.exceptions.ConnectionError:
        # Handle Redis connection issues
        print("Redis connection failed")
        
    except redis.exceptions.TimeoutError:
        # Handle Redis timeout
        print("Redis operation timed out")
        
    finally:
        await redis_client.aclose()
```

## Testing

```bash
# Install dependencies with Poetry
poetry install

# Run all tests (requires Redis server)
poetry run pytest tests/ -v

# Run only unit tests (no Redis required)
poetry run pytest tests/test_xrl.py -v

# Run only integration tests
poetry run pytest tests/test_xrl_integration.py -v
```

## Development

This project uses Poetry for dependency management:

```bash
# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Add a new dependency
poetry add package-name

# Add a development dependency
poetry add --group dev package-name

# Run commands in the Poetry environment
poetry run python your_script.py
poetry run pytest tests/

# Build the package
poetry build
```

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for your changes
4. Ensure all tests pass
5. Submit a pull request

## Changelog

### 0.1.0
- Initial release
- Token bucket algorithm implementation
- Async/await support
- Redis Lua script for atomic operations 