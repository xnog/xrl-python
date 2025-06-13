"""
Example demonstrating proper usage of XRL rate limiters with correct parameters.
"""
import asyncio
import redis.asyncio as redis
from xrl import TokenBucketRateLimiter, FixedWindowRateLimiter


async def main():
    # Connect to Redis
    redis_client = redis.from_url("redis://localhost:6379")
    
    # Initialize rate limiters
    token_bucket = TokenBucketRateLimiter(redis_client)
    fixed_window = FixedWindowRateLimiter(redis_client)
    
    print("=== Token Bucket Rate Limiter ===")
    print("Parameters: capacity (max tokens) + refill_rate (tokens/second)")
    
    # Token bucket: 5 tokens capacity, refill at 1 token per second
    print("\nExample: 5 tokens capacity, 1 token/second refill rate")
    for i in range(7):
        allowed = await token_bucket.try_acquire_token("user:bucket", capacity=5, refill_rate=1.0)
        print(f"Request {i+1}: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    print("\n=== Fixed Window Rate Limiter ===")
    print("Parameters: limit (max requests per window) + window_size (seconds)")
    
    # Fixed window: 3 requests per 5-second window
    print("\nExample: 3 requests per 5-second window")
    for i in range(5):
        allowed = await fixed_window.try_acquire_request("user:window", limit=3, window_size=5)
        print(f"Request {i+1}: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    print("\n=== Real-world Examples ===")
    
    # API rate limiting: 1000 requests per hour
    print("\nAPI rate limiting: 1000 requests per hour")
    allowed = await fixed_window.try_acquire_request("api:user123", limit=1000, window_size=3600)
    print(f"API request: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    # Smooth rate limiting: 100 requests capacity, 10 requests/second refill
    print("\nSmooth rate limiting: 100 capacity, 10 requests/second refill")
    allowed = await token_bucket.try_acquire_token("smooth:user456", capacity=100, refill_rate=10.0)
    print(f"Smooth request: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    print("\n=== Use Cases ===")
    print("Token Bucket: Best for allowing bursts while maintaining average rate")
    print("  - API rate limiting with burst allowance")
    print("  - Smooth traffic shaping")
    print("  - Credit-based systems")
    
    print("\nFixed Window: Best for hard limits per time period")
    print("  - Strict quotas (e.g., 1000 requests/hour)")
    print("  - Billing-based limits")
    print("  - Simple rate limiting")
    
    print("\n=== Backward Compatibility (DEPRECATED) ===")
    print("Old methods still work but are deprecated:")
    
    # Old token bucket method (deprecated)
    allowed = await token_bucket.acquire_token_old("old:user", capacity=10, rate=2.0)
    print(f"Old token bucket method: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    # Old fixed window method (deprecated) 
    allowed = await fixed_window.acquire_token("old:window", capacity=5, rate=1.0)
    print(f"Old fixed window method: {'✓ Allowed' if allowed else '✗ Rate limited'}")
    
    await redis_client.close()


if __name__ == "__main__":
    asyncio.run(main()) 