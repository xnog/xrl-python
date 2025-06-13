"""
XRL (eXtended Rate Limiter) - A distributed rate limiting library for Python.

This library provides multiple rate limiting algorithms implemented using Redis
for distributed environments.
"""

from .token_bucket import TokenBucketRateLimiter
from .fixed_window import FixedWindowRateLimiter

__version__ = "0.1.0"
__all__ = [
    "TokenBucketRateLimiter",
    "FixedWindowRateLimiter",
]
