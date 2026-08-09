"""TTL-based response cache with LRU eviction, SQLite-backed.

Reduces latency and cost by serving repeat requests from cache.
Cache keys: ``{api_name}:{normalized_url_or_domain}``

TTL per endpoint (seconds):
  - Website→Markdown:      300  (5 min)
  - Website Metadata:       300  (5 min)
  - Technology Detector:    900  (15 min)
  - Contact Extractor:      900  (15 min)
  - AI Website Summary:    1800  (30 min)
  - OpenGraph Extractor:    900  (15 min)
  - Robots.txt Parser:     3600  (1 hour)
  - Sitemap Parser:        3600  (1 hour)
  - SSL Checker:           3600  (1 hour)
  - DNS Lookup:            3600  (1 hour)
"""

import json
import time
import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path("/home/node/.website-intel/cache.db")
_MAX_ENTRIES = 10_000
_WRITE_COUNTER = 0
_WRITE_LOCK = threading.Lock()


def _ensure_db(db_path: Path = _DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            cache_key TEXT PRIMARY KEY,
            api_name  TEXT NOT NULL,
            url       TEXT NOT NULL,
            response  TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            hit_count  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_key ON cache(cache_key)
    """)
    conn.commit()
    return conn


def _normalize_key(api_name: str, url_or_domain: str) -> str:
    """Normalize a URL/domain into a deterministic cache key (always uses https)."""
    url_or_domain = url_or_domain.strip().lower()
    if not url_or_domain.startswith(("http://", "https://")):
        url_or_domain = "https://" + url_or_domain
    # Use https for all cache keys (http→https redirect is almost universal)
    if url_or_domain.startswith("http://"):
        url_or_domain = "https://" + url_or_domain[7:]
    parsed = urlparse(url_or_domain)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return f"{api_name}:{normalized}"


def _maybe_cleanup(conn: sqlite3.Connection):
    """Delete expired entries. Runs on every 100th write."""
    global _WRITE_COUNTER
    with _WRITE_LOCK:
        _WRITE_COUNTER += 1
        if _WRITE_COUNTER >= 100:
            _WRITE_COUNTER = 0
            deleted = conn.execute(
                "DELETE FROM cache WHERE expires_at < ?",
                (time.time(),),
            ).rowcount
            if deleted:
                logger.info(f"Cache cleanup: removed {deleted} expired entries")
            conn.commit()


def _enforce_max_entries(conn: sqlite3.Connection):
    """LRU eviction: delete oldest entries when over max."""
    count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    if count > _MAX_ENTRIES:
        to_delete = count - _MAX_ENTRIES
        conn.execute("""
            DELETE FROM cache WHERE rowid IN (
                SELECT rowid FROM cache ORDER BY hit_count ASC, expires_at ASC LIMIT ?
            )
        """, (to_delete,))
        conn.commit()
        logger.info(f"Cache LRU eviction: removed {to_delete} entries (total {count})")


def get(cache_key: str) -> Optional[Any]:
    """Retrieve cached response. Returns deserialized data or None."""
    try:
        conn = _ensure_db()
        row = conn.execute(
            "SELECT response, expires_at FROM cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        response_json, expires_at = row
        if time.time() > expires_at:
            conn.execute("DELETE FROM cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            conn.close()
            return None
        # Increment hit count
        conn.execute(
            "UPDATE cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (cache_key,),
        )
        conn.commit()
        conn.close()
        return json.loads(response_json)
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
        return None


def set(cache_key: str, api_name: str, url: str, response_data: Any, ttl_seconds: int):
    """Store response in cache with TTL."""
    try:
        now = time.time()
        conn = _ensure_db()
        _maybe_cleanup(conn)
        conn.execute("""
            INSERT OR REPLACE INTO cache (cache_key, api_name, url, response, created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT hit_count FROM cache WHERE cache_key = ?), 0))
        """, (
            cache_key,
            api_name,
            url,
            json.dumps(response_data, default=str),
            now,
            now + ttl_seconds,
            cache_key,
        ))
        conn.commit()
        _enforce_max_entries(conn)
        conn.close()
    except Exception as e:
        logger.warning(f"Cache write error: {e}")


# ┌─ TTL lookup ───────────────────────────────────────────────────────────────────────
TTLS = {
    "website_to_markdown":   300,
    "website_metadata":      300,
    "technology_detector":   900,
    "contact_extractor":     900,
    "ai_website_summary":   1800,
    "opengraph_extractor":   900,
    "robots_txt_parser":    3600,
    "sitemap_parser":       3600,
    "ssl_checker":          3600,
    "dns_lookup":           3600,
}


async def _cached_api_call(api_name: str, url_or_domain: str, fetch_fn, proxy=None, **extra_kwargs):
    """Wrap an async fetch with cache check/set.

    Returns the response dict with _from_cache marker.
    """
    ck = _normalize_key(api_name, url_or_domain)
    cached = get(ck)
    if cached is not None:
        cached["_from_cache"] = True
        return cached

    result = await fetch_fn(url_or_domain, proxy=proxy, **extra_kwargs)
    if isinstance(result, dict) and result.get("success", False):
        ttl = get_ttl(api_name)
        set(ck, api_name, url_or_domain, result, ttl)
    if isinstance(result, dict):
        result["_from_cache"] = False
    return result


def get_ttl(api_name: str) -> int:
    """Return TTL in seconds for the given API name."""
    return TTLS.get(api_name, 300)


# ┌─ Stats ──────────────────────────────────────────────────────────────────────

def stats(db_path: Path = _DEFAULT_DB) -> dict:
    """Return cache statistics."""
    try:
        conn = _ensure_db(db_path)
        total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        expired = conn.execute(
            "SELECT COUNT(*) FROM cache WHERE expires_at < ?",
            (time.time(),),
        ).fetchone()[0]
        total_hits = conn.execute(
            "SELECT COALESCE(SUM(hit_count), 0) FROM cache"
        ).fetchone()[0]
        per_api = {}
        rows = conn.execute(
            "SELECT api_name, COUNT(*) as c FROM cache GROUP BY api_name ORDER BY c DESC"
        ).fetchall()
        for row in rows:
            per_api[row[0]] = row[1]
        conn.close()
        return {
            "status": "ok",
            "total_entries": total,
            "expired_entries": expired,
            "total_hit_count": total_hits,
            "max_entries": _MAX_ENTRIES,
            "per_api": per_api,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
