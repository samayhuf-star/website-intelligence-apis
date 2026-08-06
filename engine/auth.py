"""API Key Authentication + Rate Limiting + Usage Tracking."""
import os, json, time, sqlite3, secrets, logging, threading
from pathlib import Path

logger = logging.getLogger(__name__)

TIERS = {
    "free": {"max_rps": 2, "rate_window_s": 1, "max_daily": 100},
    "starter": {"max_rps": 10, "rate_window_s": 1, "max_daily": 5000},
    "growth": {"max_rps": 30, "rate_window_s": 1, "max_daily": 25000},
    "enterprise": {"max_rps": 100, "rate_window_s": 1, "max_daily": 100000},
    "admin": {"max_rps": 9999, "rate_window_s": 1, "max_daily": 99999999},
}

_keys = {}
_lock = threading.Lock()
_usage_db = None

def init():
    global _keys, _usage_db
    db_path = os.getenv("USAGE_DB_PATH", str(Path.home() / ".website-intel" / "usage.db"))
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _usage_db = db_path
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_key TEXT, tier TEXT, owner TEXT,
        endpoint TEXT, status INTEGER, response_size INTEGER,
        latency_ms REAL, timestamp TEXT
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_ts ON usage_log(timestamp)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_key ON usage_log(api_key)""")
    conn.commit()
    conn.close()
    api_keys_str = os.getenv("API_KEYS", "{}")
    try:
        parsed = json.loads(api_keys_str)
    except json.JSONDecodeError:
        parsed = {}
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if admin_key:
        parsed[admin_key] = {"tier": "admin", "owner": "admin"}
    if not parsed:
        admin_key = "wia_admin_" + secrets.token_hex(16)
        parsed[admin_key] = {"tier": "admin", "owner": "admin"}
        logger.info(f"Generated admin key: {admin_key}")
    _keys = parsed
    logger.info(f"Auth initialized with {len(_keys)} key(s)")

def get_key_info(api_key: str) -> dict:
    raw = _keys.get(api_key)
    if not raw:
        return None
    tier_name = raw.get("tier", "free")
    tier = TIERS.get(tier_name, TIERS["free"])
    return {"tier": tier_name, "limits": tier, "owner": raw.get("owner", "")}

def log_usage(api_key: str, key_info: dict, endpoint: str, status: int, response_size: int, latency_ms: float):
    if not _usage_db:
        return
    try:
        conn = sqlite3.connect(_usage_db)
        conn.execute("INSERT INTO usage_log (api_key, tier, owner, endpoint, status, response_size, latency_ms, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (api_key[:16], key_info.get("tier", "?"), key_info.get("owner", "?"), endpoint, status, response_size, latency_ms, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Usage log error: {e}")

def get_usage_stats(days=30):
    if not _usage_db:
        return {"total_calls": 0}
    conn = sqlite3.connect(_usage_db)
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM usage_log WHERE timestamp > datetime('now', ? || ' days')", (-days,)).fetchone()[0]
    today = cur.execute("SELECT COUNT(*) FROM usage_log WHERE date(timestamp) = date('now')").fetchone()[0]
    endpoints = [dict(zip(["endpoint","calls","success","avg_latency"], r)) for r in cur.execute("SELECT endpoint, COUNT(*), SUM(CASE WHEN status < 400 THEN 1 ELSE 0 END), AVG(latency_ms) FROM usage_log WHERE timestamp > datetime('now', '-7 days') GROUP BY endpoint ORDER BY COUNT(*) DESC").fetchall()]
    daily = [dict(zip(["date","calls"], r)) for r in cur.execute("SELECT date(timestamp), COUNT(*) FROM usage_log WHERE timestamp > datetime('now', '-30 days') GROUP BY date(timestamp) ORDER BY date(timestamp)").fetchall()]
    tiers = [dict(zip(["tier","calls"], r)) for r in cur.execute("SELECT tier, COUNT(*) FROM usage_log WHERE timestamp > datetime('now', '-30 days') GROUP BY tier ORDER BY COUNT(*) DESC").fetchall()]
    conn.close()
    return {"total_calls": total, "today_calls": today, "endpoints": endpoints, "daily_trend": daily, "tiers": tiers}

class RateLimiter:
    def __init__(self, max_rps, max_daily):
        self.max_rps = max_rps
        self.max_daily = max_daily
        self.sliding = []
        self.daily = None
        self.daily_count = 0

    def check(self):
        now = time.time()
        self.sliding = [t for t in self.sliding if now - t < 1]
        if len(self.sliding) >= self.max_rps:
            return False, {"remaining_rps": 0, "reset_at": self.sliding[0] + 1 if self.sliding else now}
        today = time.strftime("%Y-%m-%d")
        if self.daily != today:
            self.daily = today
            self.daily_count = 0
        if self.daily_count >= self.max_daily:
            return False, {"remaining_rps": self.max_rps - len(self.sliding), "reset_at": None}
        self.sliding.append(now)
        self.daily_count += 1
        return True, {"remaining_rps": max(0, self.max_rps - len(self.sliding)), "remaining_daily": max(0, self.max_daily - self.daily_count), "reset_at": self.sliding[0] + 1 if self.sliding else now}

_raters = {}

def get_ratelimiter(tier: str) -> RateLimiter:
    if tier not in _raters:
        t = TIERS.get(tier, TIERS["free"])
        _raters[tier] = RateLimiter(t["max_rps"], t["max_daily"])
    return _raters[tier]

def verify_api_key(request, x_api_key: str = None):
    from fastapi import HTTPException, Header
    api_key = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        api_key = auth[7:]
    if not api_key and x_api_key:
        api_key = x_api_key
    if not api_key:
        api_key = request.query_params.get("api_key")
    if not api_key:
        raise HTTPException(401, "Missing API key. Provide via Authorization: Bearer <key> or X-API-Key or ?api_key=")
    key_info = get_key_info(api_key)
    if not key_info:
        raise HTTPException(401, "Invalid API key")
    rl = get_ratelimiter(key_info["tier"])
    allowed, info = rl.check()
    if not allowed:
        raise HTTPException(429, f"Rate limit exceeded for {key_info['tier']} tier")
    request.state.rate_limit = info
    request.state.key_info = key_info
    request.state.api_key = api_key

def verify_admin_key(request):
    from fastapi import HTTPException
    api_key = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        api_key = auth[7:]
    if not api_key:
        api_key = request.query_params.get("api_key")
    if not api_key:
        raise HTTPException(401, "Missing API key for admin access")
    key_info = get_key_info(api_key)
    if not key_info or key_info["tier"] != "admin":
        raise HTTPException(403, "Admin API key required")
    return api_key
