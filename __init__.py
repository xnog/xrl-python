"""
XRL - eXtended Rate Limiter

A distributed rate limiter using Redis token bucket algorithm.
"""

from .xrl import XRL

__version__ = "0.1.0"
__all__ = ["XRL"]
