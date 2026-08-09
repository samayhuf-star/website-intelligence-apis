"""API Key Authentication & Rate Limiting System for Website Intelligence APIs."""

import os
import json
import time
import hashlib
import logging
from typing import Optional, Dict
from collections import defaultdict
from datetime import datetime, date

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API Key Configuration
# ---------------------------------------------------------------------------

# Load API keys from environment variable (JSON object: key -> info dict)
_API_KEYS_ENV = os.getenv("API_KEYS", "{}")
try:
    _KEYS: Dict[str, dict] = json.loads(_API_KEYS_ENV)
except json.JSONDecodeError:
    _KEYS = {}

logger.info(f"Loaded {len(_KEYS)} API keys from env")

# Tiers with their limits (requests per second/minute/day)
TIERS = {
    "free": {"rps": 2, "rpm": 60, "rpd": 1000, "daily_cost_usd": 0},
    "starter": {"rps": 10, "rpm": 600, "rpd": 10000, "daily_cost_usd": 0.97},
    "growth": {"rps": 50, "rpm": 3000, "rpd": 50000, "daily_cost_usd": 2.63},
    "enterprise": {"rps": 200, "rpm": 12000, "rpd": 200000, "daily_cost_usd": 6.63},
    "admin": {"rps": 999999, "rpm": 999999, "rpd": 999999, "daily_cost_usd": 0},
}

# Admin key (from env, separate from API_KEYS)
_ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")
if _ADMIN_KEY:
    _KEYS[_ADMIN_KEY] = {"tier": "admin", "owner": "admin"}

# ---------------------------------------------------------------------------
# In-memory rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Sliding-window rate limiter per API key."""
    def __init__(self):
        self._windows: Dict[str, list] = defaultdict(list)

    def check(self, api_key: str) -> tuple:
        """Check rate limit for the given key.
        Returns (allowed: bool, remaining: int, reset_time: float).
        """
        info = _KEYS.get(api_key, {})
        tier = info.get("tier", "free")
        limits = TIERS.get(tier, TIERS["free"])

        now = time.time()
        windows = self._windows[api_key]

        # Purge entries older than 1 day
        cutoff = now - 86400
        windows[:] = [t for t in windows if t > cutoff]

        # Count in current windows
        second_ago = now - 1
        minute_ago = now - 60
        sec_count = sum(1 for t in windows if t > second_ago)
        min_count = sum(1 for t in windows if t > minute_ago)
        day_count = len(windows)

        if sec_count >= limits["rps"]:
            return False, max(0, limits["rps"] - sec_count), second_ago + 1
        if min_count >= limits["rpm"]:
            return False, max(0, limits["rpm"] - min_count), minute_ago + 60
        if day_count >= limits["rpd"]:
            next_midnight = int(now - (now % 86400) + 86400)
            return False, 0, next_midnight

        windows.append(now)
        remaining = min(
            limits["rps"] - sec_count - 1,
            limits["rpm"] - min_count - 1,
            limits["rpd"] - day_count - 1,
        )
        return True, max(0, remaining), now + 1

_rate_limiter = RateLimiter()

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

security = HTTPBearer(auto_error=False)

async def get_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Extract API key from:
    1. Authorization: Bearer <key>
    2. X-API-Key header
    3. api_key query parameter
    """
    if credentials:
        return credentials.credentials

    api_key = request.headers.get("x-api-key", "")
    if api_key:
        return api_key

    api_key = request.query_params.get("api_key", "")
    if api_key:
        return api_key

    return None

def _key_store() -> dict:
    return _KEYS

def get_key_info(api_key: str) -> Optional[dict]:
    info = _key_store().get(api_key)
    if not info:
        return None
    tier = info.get("tier", "free")
    tier_limits = TIERS.get(tier, TIERS["free"])
    return {
        **info,
        "tier": tier,
        "api_key": api_key,
        "limits": tier_limits,
    }

def verify_api_key(api_key: str) -> Optional[dict]:
    """Verify an API key and return key info, or None if invalid."""
    key_info = get_key_info(api_key)
    if not key_info:
        return None
    allowed, remaining, reset = _rate_limiter.check(api_key)
    if not allowed:
        return None  # Caller should handle rate-limit separately
    return key_info

async def rate_limit_middleware(request: Request, call_next):
    """Middleware that applies rate limiting to all requests."""
    # Skip rate limiting for public endpoints
    if request.url.path in ("/health", "/status", "/metrics", "/"):
        return await call_next(request)

    api_key = request.headers.get("x-api-key", "") or \
              request.headers.get("authorization", "").replace("Bearer ", "") or \
              request.query_params.get("api_key", "")

    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing API key. Provide via X-API-Key header or Authorization: Bearer <key>"},
        )

    key_info = get_key_info(api_key)
    if not key_info:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key"},
        )

    allowed, remaining, reset = _rate_limiter.check(api_key)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "retry_after": max(1, int(reset - time.time())),
                "remaining": remaining,
            },
            headers={
                "X-RateLimit-Remaining": str(remaining),
                "Retry-After": str(max(1, int(reset - time.time()))),
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response
