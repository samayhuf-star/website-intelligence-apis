"""API Key Authentication & Rate Limiting System for Website Intelligence APIs."""
import os, json, time, logging, sqlite3, secrets
from typing import Optional, Dict
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from fastapi import Request, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

TIERS = {
    "free": {"display": "Free", "max_rps": 2, "max_daily": 100, "max_concurrent": 1, "price_monthly": 0},
    "starter": {"display": "Starter", "max_rps": 10, "max_daily": 5000, "max_concurrent": 5, "price_monthly": 29},
    "growth": {"display": "Growth", "max_rps": 30, "max_daily": 25000, "max_concurrent": 20, "price_monthly": 79},
    "enterprise": {"display": "Enterprise", "max_rps": 100, "max_daily": 100000, "max_concurrent": 100, "price_monthly": 199},
    "admin": {"display": "Admin", "max_rps": 1000, "max_daily": 999999999, "max_concurrent": 1000, "price_monthly": 0},
}

_key_store: Dict[str, dict] = {}
_admin_key: Optional[str] = None

class RateLimiter:
    def __init__(self):
        self._windows: Dict[str, list] = defaultdict(list)
        self._daily: Dict[str, date] = {}
        self._daily_count: Dict[str, int] = defaultdict(int)
    def check(self, api_key: str, key_info: dict) -> dict:
        now = time.time()
        limits = key_info.get("limits", TIERS["free"])
        max_rps = limits.get("max_rps", 10)
        max_daily = limits.get("max_daily", 5000)
        window_start = now - 1.0
        self._windows[api_key] = [t for t in self._windows[api_key] if t > window_start]
        current_rps = len(self._windows[api_key])
        if current_rps >= max_rps:
            reset_in = self._windows[api_key][0] + 1.0 - now if self._windows[api_key] else 1.0
            return {"allowed": False, "reason": f"Rate limit exceeded: {max_rps} req/s", "remaining": 0, "reset_at": now + reset_in}
        today = date.today()
        if self._daily.get(api_key) != today:
            self._daily[api_key] = today
            self._daily_count[api_key] = 0
        if self._daily_count[api_key] >= max_daily:
            return {"allowed": False, "reason": f"Daily limit exceeded: {max_daily} req/day", "remaining": 0, "reset_at": datetime.combine(today, datetime.max.time()).timestamp()}
        self._windows[api_key].append(now)
        self._daily_count[api_key] += 1
        return {"allowed": True, "remaining_rps": max_rps - current_rps - 1, "remaining_daily": max_daily - self._daily_count[api_key], "reset_at": now + 1.0}

rate_limiter = RateLimiter()

_USAGE_DB = Path(os.getenv("USAGE_DB_PATH", "/home/node/.website-intel/usage.db"))

def _generate_admin_key() -> str:
    return "wia_admin_" + secrets.token_hex(16)

def load_keys():
    global _key_store, _admin_key
    raw = os.getenv("API_KEYS", "")
    if raw.strip():
        try:
            _key_store = json.loads(raw)
            logger.info(f"Loaded {len(_key_store)} API keys from env")
        except json.JSONDecodeError:
            _key_store = {}
    else:
        _key_store = {}
    admin_key_env = os.getenv("ADMIN_API_KEY", "")
    if admin_key_env:
        _admin_key = admin_key_env
        _key_store[_admin_key] = {"tier": "admin", "owner": "admin"}
    else:
        if not _key_store:
            _admin_key = _generate_admin_key()
            _key_store[_admin_key] = {"tier": "admin", "owner": "admin"}
            print(f"Generated admin key: {_admin_key}")
        else:
            for k, v in _key_store.items():
                if v.get("tier") == "admin":
                    _admin_key = k
                    break
            if not _admin_key:
                _admin_key = _generate_admin_key()
                _key_store[_admin_key] = {"tier": "admin", "owner": "admin"}

def get_key_info(api_key: str) -> Optional[dict]:
    info = _key_store.get(api_key)
    if not info:
        return None
    tier = info.get("tier", "free")
    return {"key": api_key[:12] + "..." + api_key[-4:], "tier": tier, "owner": info.get("owner", ""), "limits": TIERS.get(tier, TIERS["free"])}

def _ensure_db():
    _USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_USAGE_DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY AUTOINCREMENT, api_key TEXT NOT NULL, owner TEXT, tier TEXT, endpoint TEXT NOT NULL, status INTEGER, response_size INTEGER DEFAULT 0, latency_ms INTEGER DEFAULT 0, timestamp REAL NOT NULL, date TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_key ON usage(api_key)")
    conn.commit(); conn.close()

def log_usage(api_key, key_info, endpoint, status, response_size=0, latency_ms=0):
    try:
        _ensure_db()
        conn = sqlite3.connect(str(_USAGE_DB))
        now = time.time()
        today = date.today().isoformat()
        conn.execute("INSERT INTO usage (api_key, owner, tier, endpoint, status, response_size, latency_ms, timestamp, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (api_key[:16]+"...", key_info.get("owner",""), key_info.get("tier",""), endpoint, status, response_size, latency_ms, now, today))
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning(f"Failed to log usage: {e}")

def get_usage_stats(days=7) -> dict:
    try:
        _ensure_db()
        conn = sqlite3.connect(str(_USAGE_DB))
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) as c FROM usage").fetchone()["c"]
        today_total = conn.execute("SELECT COUNT(*) as c FROM usage WHERE date=?", (date.today().isoformat(),)).fetchone()["c"]
        endpoints = [dict(r) for r in conn.execute("SELECT endpoint, COUNT(*) as calls, AVG(latency_ms) as avg_latency, SUM(CASE WHEN status>=200 AND status<300 THEN 1 ELSE 0 END) as success FROM usage WHERE date>=date('now','-7 days') GROUP BY endpoint ORDER BY calls DESC").fetchall()]
        tiers = [dict(r) for r in conn.execute("SELECT tier, COUNT(*) as calls FROM usage WHERE date>=date('now','-30 days') GROUP BY tier ORDER BY calls DESC").fetchall()]
        daily = [dict(r) for r in conn.execute("SELECT date, COUNT(*) as calls FROM usage WHERE date>=date('now','-30 days') GROUP BY date ORDER BY date ASC").fetchall()]
        conn.close()
        return {"total_calls": total, "today_calls": today_total, "endpoints": endpoints, "tiers": tiers, "daily_trend": daily}
    except Exception as e:
        return {"error": str(e), "total_calls": 0, "today_calls": 0}

_security = HTTPBearer(auto_error=False)

def _extract_key(request, credentials):
    if credentials: return credentials.credentials
    h = request.headers.get("X-API-Key")
    if h: return h
    return request.query_params.get("api_key")

async def verify_api_key(request, credentials=Depends(_security)):
    if request.url.path in ("/health", "/status", "/docs", "/openapi.json", "/redoc"):
        return {"tier": "internal", "limits": TIERS["admin"], "owner": "internal"}
    if request.url.path.startswith("/dashboard"):
        key = _extract_key(request, credentials)
        if not key: raise HTTPException(401, "API key required for dashboard access")
        info = get_key_info(key)
        if not info or info.get("tier")!="admin": raise HTTPException(403, "Admin API key required for dashboard")
        return info
    key = _extract_key(request, credentials)
    if not key: raise HTTPException(401, "Missing API key. Use Authorization: Bearer *** or X-API-Key header")
    info = get_key_info(key)
    if not info: raise HTTPException(401, "Invalid API key")
    rl = rate_limiter.check(key, info)
    request.state.rate_limit = rl
    request.state.key_info = info
    request.state.api_key = key
    if not rl["allowed"]:
        raise HTTPException(429, rl["reason"], headers={"X-RateLimit-Limit":str(info["limits"]["max_rps"]), "X-RateLimit-Remaining":"0", "X-RateLimit-Reset":str(rl["reset_at"]), "Retry-After":str(max(1,int(rl["reset_at"]-time.time())))})
    return info

async def verify_admin_key(request, credentials=Depends(HTTPBearer(auto_error=False))):
    key = _extract_key(request, credentials)
    if not key: raise HTTPException(401, "API key required")
    info = get_key_info(key)
    if not info: raise HTTPException(401, "Invalid API key")
    if info.get("tier")!="admin": raise HTTPException(403, f"Admin API key required (yours is {info.get('tier','?')})")
    return info

def init():
    load_keys()
    _ensure_db()
