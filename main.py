"""Website Intelligence APIs — FastAPI Application.

10 APIs in one deployable package...
(caching, metrics, status, health)
"""

import os
import time
import logging
from typing import Optional
from contextlib import asynccontextmanager
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

load_dotenv(override=False)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("website-intel-apis")

from engine.auth import verify_api_key, verify_admin_key, log_usage, get_usage_stats, init as auth_init, TIERS
from engine.cache import get as cache_get, set as cache_set, _normalize_key, get_ttl, stats as cache_stats

_start_time: float = time.time()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.time()
    logger.info("Website Intelligence APIs starting...")
    auth_init()
    yield
    logger.info("Website Intelligence APIs shutting down...")

app = FastAPI(title="Website Intelligence APIs", version="1.0.0", lifespan=lifespan, docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Metrics collector
_metrics_lock = __import__("threading").Lock()
_metrics = {
    "requests_total": 0,
    "requests_per_endpoint": defaultdict(lambda: {"count": 0, "errors": 0, "latencies": []}),
    "requests_per_tier": defaultdict(int),
    "cache_hits": 0,
    "cache_misses": 0,
    "rate_limited_count": 0,
    "crawl_failures": 0,
    "internal_errors": 0,
}

def _record_request(endpoint: str, tier: str, latency_ms: float, is_error: bool, from_cache: bool):
    with _metrics_lock:
        _metrics["requests_total"] += 1
        ep = _metrics["requests_per_endpoint"][endpoint]
        ep["count"] += 1
        ep["latencies"].append(latency_ms)
        if len(ep["latencies"]) > 500:
            ep["latencies"] = ep["latencies"][-500:]
        if is_error:
            ep["errors"] += 1
        _metrics["requests_per_tier"][tier] += 1
        if from_cache:
            _metrics["cache_hits"] += 1
        else:
            _metrics["cache_misses"] += 1

def _compute_percentiles(latencies):
    if not latencies:
        return {"p50": 0, "p95": 0, "p99": 0}
    s = sorted(latencies)
    n = len(s)
    return {"p50": s[n // 2], "p95": s[int(n * 0.95)], "p99": s[int(n * 0.99)]}

# Middleware
@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed_ms = int((time.time() - start) * 1000)
    rl = getattr(request.state, "rate_limit", None)
    ki = getattr(request.state, "key_info", None)
    ak = getattr(request.state, "api_key", None)
    if rl:
        response.headers["X-RateLimit-Limit"] = str(ki["limits"]["max_rps"] if ki else "?")
        response.headers["X-RateLimit-Remaining"] = str(rl.get("remaining_rps", "?"))
        response.headers["X-RateLimit-Reset"] = str(rl.get("reset_at", ""))
        response.headers["X-RateLimit-Daily-Remaining"] = str(rl.get("remaining_daily", "?"))
    if ki and ak and request.url.path not in ("/health", "/status", "/metrics", "/docs", "/redoc", "/openapi.json"):
        try:
            log_usage(api_key=ak, key_info=ki, endpoint=request.url.path, status=response.status_code,
                      response_size=int(response.headers.get("content-length", 0)), latency_ms=elapsed_ms)
        except Exception:
            pass
    return response

# Cache helper
async def _cached_api_call(api_name, url_or_domain, fetch_fn, proxy=None, **extra_kwargs):
    cache_key = _normalize_key(api_name, url_or_domain)
    cached = cache_get(cache_key)
    if cached is not None:
        cached["_from_cache"] = True
        return cached
    result = await fetch_fn(url_or_domain, proxy=proxy, **extra_kwargs)
    if result.get("success"):
        ttl = get_ttl(api_name)
        cache_set(cache_key, api_name, url_or_domain, result, ttl)
    result["_from_cache"] = False
    return result

async def _api_handler(api_name, url_or_domain, fetch_fn, tier, proxy=None, **extra_kwargs):
    start = time.time()
    try:
        result = await _cached_api_call(api_name, url_or_domain, fetch_fn, proxy=proxy, **extra_kwargs)
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        _record_request(f"/api/v1/{api_name}", tier, latency_ms, True, False)
        with _metrics_lock:
            _metrics["internal_errors"] += 1
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    latency_ms = int((time.time() - start) * 1000)
    is_error = not result.get("success", False)
    fc = result.pop("_from_cache", False) if isinstance(result, dict) else False
    _record_request(f"/api/v1/{api_name}", tier, latency_ms, is_error, fc)
    if is_error:
        with _metrics_lock:
            _metrics["crawl_failures"] += 1
        raise HTTPException(status_code=422, detail=result.get("error", "API call failed"))
    return result

# Public endpoints
@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "service": "Website Intelligence APIs", "version": "1.0.0", "endpoints": 12, "tiers": list(TIERS.keys()), "timestamp": time.time()}

@app.get("/status", tags=["System"], summary="Detailed component status")
async def system_status():
    uptime = int(time.time() - _start_time)
    cs = cache_stats()
    us = get_usage_stats(days=1)
    return {"service": "Website Intelligence APIs", "version": "1.0.0", "uptime_seconds": uptime, "status": "healthy",
            "components": {
                "api": {"status": "ok", "active_endpoints": 10, "total_endpoints": 12},
                "cache": {"status": "ok" if cs.get("status") == "ok" else "degraded", "entries": cs.get("total_entries", 0),
                          "max_entries": cs.get("max_entries", 10000), "total_hit_count": cs.get("total_hit_count", 0),
                          "per_api": cs.get("per_api", {})},
                "database": {"status": "ok", "usage_today": us.get("today_calls", 0), "usage_total": us.get("total_calls", 0)},
                "crawler": {"status": "ok", "concurrent_requests": "async (httpx)"},
            }}

@app.get("/metrics", tags=["System"], summary="Prometheus-style metrics JSON", dependencies=[Depends(verify_admin_key)])
async def system_metrics():
    uptime = int(time.time() - _start_time)
    cs = cache_stats()
    with _metrics_lock:
        ep_metrics = {}
        for name, data in _metrics["requests_per_endpoint"].items():
            latencies = data["latencies"]
            ep_metrics[name] = {"count": data["count"], "errors": data["errors"], **_compute_percentiles(latencies)}
        return {
            "requests": {"total": _metrics["requests_total"], "per_endpoint": ep_metrics, "per_tier": dict(_metrics["requests_per_tier"])},
            "cache": {"hits": _metrics["cache_hits"], "misses": _metrics["cache_misses"],
                      "hit_rate": round(_metrics["cache_hits"] / max(_metrics["cache_hits"] + _metrics["cache_misses"], 1), 3),
                      "entries": cs.get("total_entries", 0), "evictions": 0},
            "errors": {"total": _metrics["internal_errors"], "rate_limited": _metrics["rate_limited_count"], "crawl_failures": _metrics["crawl_failures"]},
            "system": {"uptime_seconds": uptime},
        }

@app.get("/dashboard", tags=["Dashboard"], summary="Usage analytics dashboard (admin key required)")
async def usage_dashboard(_admin=Depends(verify_admin_key)):
    return get_usage_stats(days=30)

@app.get("/dashboard/html", tags=["Dashboard"], summary="HTML usage dashboard", response_class=HTMLResponse)
async def usage_dashboard_html(request: Request, _admin=Depends(verify_admin_key)):
    stats = get_usage_stats(days=30)
    rows = ""; trend_rows = ""; tier_rows = ""
    for ep in stats.get("endpoints", []):
        sr = round(ep["success"] / ep["calls"] * 100, 1) if ep["calls"] > 0 else 0
        rows += f"<tr><td>{ep['endpoint']}</td><td>{ep['calls']}</td><td>{ep['success']}</td><td>{sr}%</td><td>{round(ep['avg_latency'], 0)}ms</td></tr>"
    for d in stats.get("daily_trend", [])[-14:]:
        trend_rows += f"<tr><td>{d['date']}</td><td>{d['calls']}</td></tr>"
    for t in stats.get("tiers", []):
        pct = round(t["calls"] / stats["total_calls"] * 100, 1) if stats["total_calls"] > 0 else 0
        tier_rows += f"<tr><td>{t['tier'].title()}</td><td>{t['calls']}</td><td>{pct}%</td></tr>"
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Website Intelligence APIs — Dashboard</title><style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}h2{{font-size:1.3rem;margin:1.5rem 0 .75rem;color:#94a3b8}}
.card{{background:#1e293b;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem;border:1px solid #334155}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-bottom:1.5rem}}
.stat{{background:#1e293b;border-radius:10px;padding:1.25rem;text-align:center;border:1px solid #334155}}
.stat-value{{font-size:2rem;font-weight:700;color:#38bdf8}}.stat-label{{font-size:.85rem;color:#94a3b8;margin-top:.25rem}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:.6rem .75rem;border-bottom:1px solid #334155;font-size:.9rem}}
th{{color:#94a3b8;font-weight:600;font-size:.8rem;text-transform:uppercase}}
.footer{{margin-top:2rem;text-align:center;color:#64748b;font-size:.8rem}}
</style></head><body>
<h1>🌐 Website Intelligence APIs</h1><p style="color:#94a3b8;">Real-time usage dashboard</p>
<div class="stats"><div class="stat"><div class="stat-value">{stats["total_calls"]:,}</div><div class="stat-label">Total API Calls</div></div>
<div class="stat"><div class="stat-value">{stats["today_calls"]:,}</div><div class="stat-label">Today</div></div>
<div class="stat"><div class="stat-value">{len(stats.get("endpoints", []))}</div><div class="stat-label">Endpoints Used</div></div>
<div class="stat"><div class="stat-value">{len(stats.get("tiers", []))}</div><div class="stat-label">Active Tiers</div></div></div>
<div class="card"><h2>📌 Per-Endpoint (Last 7 Days)</h2><table><thead><tr><th>Endpoint</th><th>Calls</th><th>Success</th><th>Success Rate</th><th>Avg Latency</th></tr></thead><tbody>{rows or '<tr><td colspan="5" style="text-align:center;color:#64748b;">No data yet</td></tr>'}</tbody></table></div>
<div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:1.5rem"><div><h2>📈 Daily Trend</h2><table><thead><tr><th>Date</th><th>Calls</th></tr></thead><tbody>{trend_rows or '<tr><td colspan="2" style="text-align:center;color:#64748b;">No data yet</td></tr>'}</tbody></table></div>
<div><h2>👥 By Tier</h2><table><thead><tr><th>Tier</th><th>Calls</th><th>%</th></tr></thead><tbody>{tier_rows or '<tr><td colspan="3" style="text-align:center;color:#64748b;">No data yet</td></tr>'}</tbody></table></div></div>
<div class="footer">Website Intelligence APIs v1.0.0 — Admin dashboard</div></body>"""
    return HTMLResponse(content=html)

# Pydantic models
class URLRequest(BaseModel):
    url: str = Field(..., description="Website URL to analyze")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")

class ContactRequest(BaseModel):
    url: str = Field(..., description="Website URL")
    deep_crawl: bool = Field(False, description="Deep crawl /contact and /about")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")

class SummaryRequest(BaseModel):
    url: str = Field(..., description="Website URL to summarize")
    use_ai: bool = Field(False, description="Generate AI narrative")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")

class DomainRequest(BaseModel):
    domain: str = Field(..., description="Domain name or URL")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")

# 10 API endpoints - all wired through _api_handler with caching
@app.post("/api/v1/website-to-markdown", tags=["Website → Markdown"])
async def website_to_markdown(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.website_to_markdown import convert_to_markdown
    return await _api_handler("website_to_markdown", request.url, convert_to_markdown, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/website-metadata", tags=["Website Metadata"])
async def website_metadata(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.website_metadata import extract_website_metadata
    return await _api_handler("website_metadata", request.url, extract_website_metadata, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/technology-detector", tags=["Technology Detector"])
async def technology_detector(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.technology_detector import detect_technology
    return await _api_handler("technology_detector", request.url, detect_technology, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/contact-extractor", tags=["Contact Extractor"])
async def contact_extractor(request: ContactRequest, _auth=Depends(verify_api_key)):
    from apis.contact_extractor import extract_contacts
    return await _api_handler("contact_extractor", request.url, extract_contacts, _auth.get("tier", "free"), proxy=request.proxy, deep_crawl=request.deep_crawl)

@app.post("/api/v1/ai-website-summary", tags=["AI Website Summary"])
async def ai_website_summary(request: SummaryRequest, _auth=Depends(verify_api_key)):
    from apis.ai_website_summary import generate_website_summary
    api_key = os.getenv("AI_COMPANY_SUMMARY_API_KEY", "")
    result = await _cached_api_call("ai_website_summary", request.url, lambda u, p: generate_website_summary(u, use_ai=request.use_ai, api_key=api_key, proxy=p), proxy=request.proxy)
    fc = result.pop("_from_cache", False) if isinstance(result, dict) else False
    with _metrics_lock:
        _metrics["requests_total"] += 1
        _metrics["requests_per_endpoint"]["/api/v1/ai-website-summary"]["count"] += 1
        if fc: _metrics["cache_hits"] += 1
        else: _metrics["cache_misses"] += 1
    if not result.get("success"):
        raise HTTPException(status_code=422, detail=result.get("error", "Failed to generate summary"))
    return result

@app.post("/api/v1/opengraph-extractor", tags=["OpenGraph Extractor"])
async def opengraph_extractor(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.opengraph_extractor import extract_opengraph
    return await _api_handler("opengraph_extractor", request.url, extract_opengraph, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/robots-txt-parser", tags=["Robots.txt Parser"])
async def robots_txt_parser(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.robots_txt_parser import parse_robots_txt
    return await _api_handler("robots_txt_parser", request.url, parse_robots_txt, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/sitemap-parser", tags=["Sitemap Parser"])
async def sitemap_parser(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.sitemap_parser import parse_sitemap
    return await _api_handler("sitemap_parser", request.url, parse_sitemap, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/ssl-checker", tags=["SSL Checker"])
async def ssl_checker(request: DomainRequest, _auth=Depends(verify_api_key)):
    from apis.ssl_checker import check_ssl
    return await _api_handler("ssl_checker", request.domain, check_ssl, _auth.get("tier", "free"), proxy=request.proxy)

@app.post("/api/v1/dns-lookup", tags=["DNS Lookup"])
async def dns_lookup(request: DomainRequest, _auth=Depends(verify_api_key)):
    from apis.dns_lookup import lookup_dns
    return await _api_handler("dns_lookup", request.domain, lookup_dns, _auth.get("tier", "free"), proxy=request.proxy)

@app.get("/api/v1/ssl-checker", tags=["SSL Checker"])
async def ssl_checker_get(domain: str = Query(...), _auth=Depends(verify_api_key)):
    from apis.ssl_checker import check_ssl
    return await _api_handler("ssl_checker", domain, check_ssl, _auth.get("tier", "free"))

@app.get("/api/v1/dns-lookup", tags=["DNS Lookup"])
async def dns_lookup_get(domain: str = Query(...), _auth=Depends(verify_api_key)):
    from apis.dns_lookup import lookup_dns
    return await _api_handler("dns_lookup", domain, lookup_dns, _auth.get("tier", "free"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
