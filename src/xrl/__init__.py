"""
XRL - eXtended Rate Limiter
A distributed rate limiter library with multiple algorithms.
"""

from .base import BaseRateLimiter
from .token_bucket import TokenBucketRateLimiter
from .fixed_window import FixedWindowRateLimiter

__version__ = "0.2.0"

__all__ = [
    "BaseRateLimiter",
    "TokenBucketRateLimiter",
    "FixedWindowRateLimiter",
]
