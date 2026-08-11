"""Website Intelligence APIs — FastAPI Application.

10 APIs in one deployable package:
1. Website → Markdown
2. Website Metadata
3. Technology Detector
4. Contact Extractor
5. AI Website Summary
6. OpenGraph Extractor
7. Robots.txt Parser
8. Sitemap Parser
9. SSL Checker
10. DNS Lookup

Features:
- API key authentication (Bearer, X-API-Key, or query param)
- Tier-based rate limiting (free / starter / growth / enterprise / admin)
- Crypto payments (Solana USDC) — AI agents can pay per request
- Prepaid credit system — generate invoice, send USDC, get API credits
- AI agent discovery at /.well-known/ai-plugin.json
- Usage tracking with SQLite backend
- Response caching (SQLite-backed, TTL per endpoint, LRU eviction)
- Real-time usage dashboard + metrics + status endpoints
"""

import os
import time
import logging
import json
from typing import Optional
from contextlib import asynccontextmanager
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

load_dotenv(override=False)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("website-intel-apis")

# ---------------------------------------------------------------------------
# Auth & usage system
# ---------------------------------------------------------------------------
from engine.auth import (
    verify_api_key, verify_admin_key, log_usage, get_usage_stats,
    init as auth_init, TIERS,
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
from engine.cache import (
    _normalize_key, _cached_api_call, get as cache_get, set as cache_set,
    get_ttl, stats as cache_stats,
)

# ---------------------------------------------------------------------------
# Payment system
# ---------------------------------------------------------------------------
from engine.payments import (
    generate_invoice, confirm_invoice, get_balance,
    get_payment_history, get_pricing, deduct_credit,
)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
_start_time: float = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.time()
    logger.info("Website Intelligence APIs starting...")
    auth_init()
    yield
    logger.info("Website Intelligence APIs shutting down...")


app = FastAPI(
    title="Website Intelligence APIs",
    description="""## 10 Powerful Website Analysis APIs — Crypto-Powered for AI Agents

A complete website intelligence toolkit for developers, marketers, security professionals, **and AI agents**.

### 🔑 Authentication
All API endpoints require a valid API key. Get credits by sending Solana USDC to the treasury wallet.

### 💰 Crypto Payments (Solana USDC)
1. `POST /api/v1/payments/invoice` — Get a treasury wallet + memo
2. Send USDC on Solana to that wallet with the memo
3. `POST /api/v1/payments/verify` — Confirm transaction, get credits
4. Use your API key normally — credits auto-deduct per call

### 🤖 AI Agent Discovery
`GET /.well-known/ai-plugin.json` — OpenAI GPT Action manifest
`GET /.well-known/openapi.json` — AI-compatible OpenAPI spec

### Endpoints

| # | API | Description | Price |
|---|-----|-------------|-------|
| 1 | **Website → Markdown** | Convert any web page to clean, structured Markdown | $0.0005 |
| 2 | **Website Metadata** | Extract meta tags, headings, images, links, favicon, language | $0.0002 |
| 3 | **Technology Detector** | Detect CMS, frameworks, analytics, CDN, server tech | $0.0003 |
| 4 | **Contact Extractor** | Extract emails, phones, social links, addresses | $0.0005 |
| 5 | **AI Website Summary** | Structured summary + optional AI-powered narrative | $0.002 |
| 6 | **OpenGraph Extractor** | OG tags, Twitter Cards, social preview analysis | $0.0002 |
| 7 | **Robots.txt Parser** | Fetch & parse robots.txt with crawl rules & key page access | $0.0002 |
| 8 | **Sitemap Parser** | Discover & parse XML sitemaps with URL analysis | $0.0005 |
| 9 | **SSL Checker** | Certificate details, expiry, security grade | $0.0002 |
| 10 | **DNS Lookup** | A, AAAA, MX, NS, CNAME, subdomain discovery | $0.0002 |

### Rate Limits (per key)
| Plan | Requests/sec | Daily limit | Price |
|------|-------------|-------------|-------|
| Free | 2 | 100 | $0 |
| Starter | 10 | 5,000 | $29/mo |
| Growth | 30 | 25,000 | $79/mo |
| Enterprise | 100 | 100,000 | $199/mo |

### Features
- ✅ Crypto payments (Solana USDC) for AI agents
- ✅ OpenAI GPT Action manifest at /.well-known/ai-plugin.json
- ✅ API key authentication with tiered rate limiting
- ✅ Response caching (repeated requests served from cache)
- ✅ Usage dashboard at /dashboard (admin key required)
- ✅ /metrics and /status monitoring endpoints
- ✅ All endpoints return structured JSON
- ✅ Rate limit headers on every response
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files for AI agent discovery
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# In-memory metrics collector
# ---------------------------------------------------------------------------
_metrics_lock = __import__("threading").Lock()
_metrics: dict = {
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


# ---------------------------------------------------------------------------
# AI Agent Discovery Endpoints
# ---------------------------------------------------------------------------
@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def ai_plugin_manifest():
    manifest_path = static_dir / "ai-plugin.json"
    if manifest_path.exists():
        raw = manifest_path.read_text()
        # Substitute environment variables (e.g. ${TREASURY_WALLET}) with real values
        import re
        def _sub(m):
            key = m.group(1)
            return os.getenv(key, "") if key in ("TREASURY_WALLET", "OPENAI_VERIFICATION_TOKEN") else m.group(0)
        resolved = re.sub(r"\$\{([A-Z0-9_]+)\}", _sub, raw)
        return JSONResponse(
            content=json.loads(resolved),
            media_type="application/json",
        )
    raise HTTPException(status_code=404, detail="Plugin manifest not available")


@app.get("/.well-known/openapi.json", tags=["AI Agent Discovery"],
         summary="OpenAPI spec for AI agent consumption",
         include_in_schema=False)
async def ai_openapi_spec():
    """Return OpenAPI spec suitable for AI agent consumption."""
    base_url = os.getenv("PUBLIC_BASE_URL", "http://167.71.22.95")
    openapi_path = Path(__file__).parent / "rapidapi" / "openapi.json"
    if openapi_path.exists():
        spec = json.loads(openapi_path.read_text())
        spec["servers"] = [{"url": base_url, "description": "Production"}]
        return JSONResponse(content=spec, media_type="application/json")
    return {"openapi": "3.1.0", "info": {"title": "Website Intelligence APIs", "version": "1.0.0"}}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class URLRequest(BaseModel):
    url: str = Field(..., description="Website URL to analyze")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")


class ContactRequest(BaseModel):
    url: str = Field(..., description="Website URL to extract contacts from")
    deep_crawl: bool = Field(False, description="Enable deep crawl of /contact and /about pages")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")


class SummaryRequest(BaseModel):
    url: str = Field(..., description="Website URL to summarize")
    use_ai: bool = Field(False, description="Generate AI-powered narrative summary")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")


class DomainRequest(BaseModel):
    domain: str = Field(..., description="Domain name or URL to check")
    proxy: Optional[str] = Field(None, description="Optional proxy URL")


# ---------------------------------------------------------------------------
# Helper to wire caching + payments + metrics into each endpoint
# ---------------------------------------------------------------------------
async def _api_handler(api_name: str, url_or_domain: str, fetch_fn, tier: str,
                       proxy: Optional[str] = None, from_cache: Optional[bool] = None,
                       api_key: Optional[str] = None, **extra_kwargs):
    """Wrap an API call with caching, payment deduction, and metrics recording."""
    start = time.time()

    # Check credits for non-internal tiers
    if tier not in ("admin", "internal") and api_key:
        has_credits = deduct_credit(api_key, api_name)
        if not has_credits:
            pricing = get_pricing()
            ep_price = pricing.get(api_name, {})
            price_str = ep_price.get("price_display", "$0.0002")
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Insufficient credits. Please add funds.",
                    "message": f"This endpoint costs {price_str} per request. "
                               f"Generate an invoice at POST /api/v1/payments/invoice "
                               f"and send USDC to the treasury wallet.",
                    "endpoint": api_name,
                    "price": price_str,
                    "payment_url": "/api/v1/payments/invoice",
                },
            )

    try:
        if from_cache is not None:
            result = await fetch_fn(url_or_domain, proxy=proxy, **extra_kwargs)
        else:
            result = await _cached_api_call(api_name, url_or_domain, fetch_fn, proxy=proxy, **extra_kwargs)
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        _record_request(f"/api/v1/{api_name}", tier, latency_ms, is_error=True, from_cache=False)
        _metrics_lock.acquire()
        _metrics["internal_errors"] += 1
        _metrics_lock.release()
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

    latency_ms = int((time.time() - start) * 1000)
    is_error = not result.get("success", False)
    fc = result.pop("_from_cache", False) if isinstance(result, dict) else False

    _record_request(f"/api/v1/{api_name}", tier, latency_ms, is_error=is_error, from_cache=fc)

    if is_error:
        _metrics_lock.acquire()
        _metrics["crawl_failures"] += 1
        _metrics_lock.release()
        raise HTTPException(status_code=422, detail=result.get("error", "API call failed"))

    # Add balance info to response for non-admin keys
    if "balance_info" not in result and tier not in ("admin", "internal") and api_key:
        bal = get_balance(api_key)
        result["_credits"] = {
            "balance_usdc": bal.get("balance_usdc", 0),
            "this_call_cost": get_pricing().get(api_name, {}).get("price_usdc", 0.02),
        }

    return result


# ---------------------------------------------------------------------------
# Crypto Payment Routes
# ---------------------------------------------------------------------------
class InvoiceRequest(BaseModel):
    api_key: str = Field(..., description="Your existing API key to associate credits with")
    amount_usdc: Optional[float] = Field(None, description="Amount in USDC (optional = any amount)")


class VerifyRequest(BaseModel):
    invoice_id: str = Field(..., description="Invoice ID from /payments/invoice")
    tx_signature: str = Field(..., description="Solana transaction signature to verify")


@app.post("/api/v1/payments/invoice", tags=["Crypto Payments"],
          summary="Generate payment invoice for AI agent credits")
async def create_invoice(request: InvoiceRequest):
    result = generate_invoice(request.api_key, request.amount_usdc or 0.0)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/v1/payments/verify", tags=["Crypto Payments"],
          summary="Verify crypto payment and add credits")
async def verify_payment(request: VerifyRequest):
    result = confirm_invoice(request.invoice_id, request.tx_signature)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/api/v1/payments/balance", tags=["Crypto Payments"],
         summary="Check credit balance")
async def payment_balance(api_key: str = Query(..., description="Your API key")):
    return get_balance(api_key)


@app.get("/api/v1/payments/history", tags=["Crypto Payments"],
         summary="View payment and usage history")
async def payment_history(api_key: str = Query(..., description="Your API key"),
                          limit: int = Query(20, description="Number of records")):
    return get_payment_history(api_key, limit)


@app.get("/api/v1/payments/pricing", tags=["Crypto Payments"],
         summary="View per-endpoint pricing")
async def payment_pricing():
    return get_pricing()


# ---------------------------------------------------------------------------
# API 1: Website to Markdown
# ---------------------------------------------------------------------------
@app.post("/api/v1/website-to-markdown", tags=["Website to Markdown"],
          summary="Convert any web page to clean Markdown")
async def website_to_markdown(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.website_to_markdown import convert_to_markdown
    return await _api_handler("website_to_markdown", request.url, convert_to_markdown,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 2: Website Metadata
# ---------------------------------------------------------------------------
@app.post("/api/v1/website-metadata", tags=["Website Metadata"],
          summary="Extract comprehensive website metadata")
async def website_metadata(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.website_metadata import extract_website_metadata
    return await _api_handler("website_metadata", request.url, extract_website_metadata,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 3: Technology Detector
# ---------------------------------------------------------------------------
@app.post("/api/v1/technology-detector", tags=["Technology Detector"],
          summary="Detect technologies used by a website")
async def technology_detector(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.technology_detector import detect_technology
    return await _api_handler("technology_detector", request.url, detect_technology,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 4: Contact Extractor
# ---------------------------------------------------------------------------
@app.post("/api/v1/contact-extractor", tags=["Contact Extractor"],
          summary="Extract contacts from a website")
async def contact_extractor(request: ContactRequest, _auth=Depends(verify_api_key)):
    from apis.contact_extractor import extract_contacts
    return await _api_handler("contact_extractor", request.url, extract_contacts,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              deep_crawl=request.deep_crawl,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 5: AI Website Summary
# ---------------------------------------------------------------------------
@app.post("/api/v1/ai-website-summary", tags=["AI Website Summary"],
          summary="Generate AI-powered website summary")
async def ai_website_summary(request: SummaryRequest, _auth=Depends(verify_api_key)):
    from apis.ai_website_summary import generate_website_summary
    start = time.time()
    ai_api_key = os.getenv("AI_COMPANY_SUMMARY_API_KEY", "")
    tier = _auth.get("tier", "free")
    req_api_key = _auth.get("api_key")

    if tier not in ("admin", "internal") and req_api_key:
        has = deduct_credit(req_api_key, "ai_website_summary")
        if not has:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Insufficient credits. Please add funds.",
                    "message": "This endpoint costs $0.002 per request. "
                               "Generate an invoice at POST /api/v1/payments/invoice.",
                    "endpoint": "ai_website_summary",
                    "price": "$0.002",
                    "payment_url": "/api/v1/payments/invoice",
                },
            )

    result = await _cached_api_call("ai_website_summary", request.url,
                                    lambda u, proxy=None: generate_website_summary(
                                        u, use_ai=request.use_ai, api_key=ai_api_key, proxy=proxy
                                    ),
                                    proxy=request.proxy)
    latency_ms = int((time.time() - start) * 1000)
    fc = result.pop("_from_cache", False) if isinstance(result, dict) else False
    _record_request("/api/v1/ai-website-summary", tier, latency_ms,
                    is_error=not result.get("success", False), from_cache=fc)
    if not result.get("success"):
        raise HTTPException(status_code=422, detail=result.get("error", "Failed to generate summary"))
    return result


# ---------------------------------------------------------------------------
# API 6: OpenGraph Extractor
# ---------------------------------------------------------------------------
@app.post("/api/v1/opengraph-extractor", tags=["OpenGraph Extractor"],
          summary="Extract Open Graph and Twitter Card tags")
async def opengraph_extractor(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.opengraph_extractor import extract_opengraph
    return await _api_handler("opengraph_extractor", request.url, extract_opengraph,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 7: Robots.txt Parser
# ---------------------------------------------------------------------------
@app.post("/api/v1/robots-txt-parser", tags=["Robots.txt Parser"],
          summary="Fetch and parse robots.txt")
async def robots_txt_parser(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.robots_txt_parser import parse_robots_txt
    return await _api_handler("robots_txt_parser", request.url, parse_robots_txt,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 8: Sitemap Parser
# ---------------------------------------------------------------------------
@app.post("/api/v1/sitemap-parser", tags=["Sitemap Parser"],
          summary="Discover and parse XML sitemaps")
async def sitemap_parser(request: URLRequest, _auth=Depends(verify_api_key)):
    from apis.sitemap_parser import parse_sitemap
    return await _api_handler("sitemap_parser", request.url, parse_sitemap,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 9: SSL Checker
# ---------------------------------------------------------------------------
@app.post("/api/v1/ssl-checker", tags=["SSL Checker"],
          summary="Check SSL certificate details and security")
async def ssl_checker(request: DomainRequest, _auth=Depends(verify_api_key)):
    from apis.ssl_checker import check_ssl
    return await _api_handler("ssl_checker", request.domain, check_ssl,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# API 10: DNS Lookup
# ---------------------------------------------------------------------------
@app.post("/api/v1/dns-lookup", tags=["DNS Lookup"],
          summary="Perform comprehensive DNS lookup")
async def dns_lookup(request: DomainRequest, _auth=Depends(verify_api_key)):
    from apis.dns_lookup import lookup_dns
    return await _api_handler("dns_lookup", request.domain, lookup_dns,
                              _auth.get("tier", "free"), proxy=request.proxy,
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# GET variants for simple endpoints
# ---------------------------------------------------------------------------
@app.get("/api/v1/ssl-checker", tags=["SSL Checker"],
         summary="Check SSL certificate (GET)")
async def ssl_checker_get(domain: str = Query(..., description="Domain name"),
                          _auth=Depends(verify_api_key)):
    from apis.ssl_checker import check_ssl
    return await _api_handler("ssl_checker", domain, check_ssl, _auth.get("tier", "free"),
                              api_key=_auth.get("api_key"))


@app.get("/api/v1/dns-lookup", tags=["DNS Lookup"],
         summary="Perform DNS lookup (GET)")
async def dns_lookup_get(domain: str = Query(..., description="Domain name"),
                         _auth=Depends(verify_api_key)):
    from apis.dns_lookup import lookup_dns
    return await _api_handler("dns_lookup", domain, lookup_dns, _auth.get("tier", "free"),
                              api_key=_auth.get("api_key"))


# ---------------------------------------------------------------------------
# Health / Status / Metrics / Dashboard
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Monitoring"], summary="Health check")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/status", tags=["Monitoring"], summary="Detailed service status")
async def status():
    from engine.cache import stats as cache_stats
    cs = cache_stats()
    payments_ok = bool(os.getenv("TREASURY_WALLET", ""))
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - _start_time),
        "components": {
            "cache": {
                "status": "ok" if cs.get("status") == "ok" else "degraded",
                "entries": cs.get("total_entries", 0),
            },
            "api": {"status": "ok", "version": "1.0.0"},
            "auth": {"status": "ok", "rate_limiter_active": True},
            "payments": {
                "status": "ok" if payments_ok else "degraded",
                "treasury_configured": payments_ok,
                "chain": "solana",
                "token": "USDC",
            },
        },
    }


@app.get("/metrics", tags=["Monitoring"], summary="Prometheus-style metrics")
async def metrics(_auth=Depends(verify_admin_key)):
    with _metrics_lock:
        m = dict(_metrics)

    lines = [
        "# HELP website_intel_requests_total Total API requests",
        "# TYPE website_intel_requests_total counter",
        f'website_intel_requests_total {m["requests_total"]}',
        "",
        "# HELP website_intel_cache_hits Total cache hits",
        "# TYPE website_intel_cache_hits counter",
        f'website_intel_cache_hits {m["cache_hits"]}',
        "",
        "# HELP website_intel_cache_misses Total cache misses",
        "# TYPE website_intel_cache_misses counter",
        f'website_intel_cache_misses {m["cache_misses"]}',
        "",
        "# HELP website_intel_errors Total errors",
        "# TYPE website_intel_errors counter",
        f'website_intel_crawl_failures {m["crawl_failures"]}',
        f'website_intel_internal_errors {m["internal_errors"]}',
        "",
        "# HELP website_intel_rate_limited Total rate-limited requests",
        "# TYPE website_intel_rate_limited counter",
        f'website_intel_rate_limited {m["rate_limited_count"]}',
        "",
    ]

    for endpoint, ep in sorted(m["requests_per_endpoint"].items()):
        lats = ep["latencies"]
        lines.append(f'# HELP website_intel_endpoint_requests Requests per endpoint "{endpoint}"')
        lines.append(f'# TYPE website_intel_endpoint_requests gauge')
        lines.append(f'website_intel_endpoint_requests{{endpoint="{endpoint}"}} {ep["count"]}')
        lines.append(f'website_intel_endpoint_errors{{endpoint="{endpoint}"}} {ep["errors"]}')
        if lats:
            sorted_lats = sorted(lats)
            n = len(sorted_lats)
            lines.append(f'website_intel_endpoint_p50_ms{{endpoint="{endpoint}"}} {sorted_lats[n // 2]}')
            lines.append(f'website_intel_endpoint_p95_ms{{endpoint="{endpoint}"}} {sorted_lats[int(n * 0.95)]}')
            lines.append(f'website_intel_endpoint_p99_ms{{endpoint="{endpoint}"}} {sorted_lats[int(n * 0.99)]}')
        lines.append("")

    for tier, count in sorted(m["requests_per_tier"].items()):
        lines.append(f'website_intel_requests_by_tier{{tier="{tier}"}} {count}')

    return JSONResponse(
        content={"text/plain": "\n".join(lines)},
        media_type="text/plain; version=0.4.0",
    )


@app.get("/dashboard", tags=["Monitoring"], summary="Usage dashboard (HTML)")
async def dashboard(_auth=Depends(verify_admin_key)):
    with _metrics_lock:
        m = dict(_metrics)
    usage = get_usage_stats()
    cs = cache_stats()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Website Intel APIs - Dashboard</title>
<style>body{{font-family:sans-serif;background:#111;color:#eee;margin:20px}}
h1,h2{{color:#4fc3f7}}table{{border-collapse:collapse;width:100%;margin:10px 0}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid #333}}
th{{background:#1a237e;color:#fff}}tr:hover{{background:#222}}
.card{{background:#1e1e1e;padding:15px;margin:10px 0;border-radius:8px}}
.mono{{font-family:monospace;font-size:13px}}</style></head>
<body>
<h1>Website Intelligence APIs - Dashboard</h1>
<div class="card">
<h2>System</h2>
<p>Uptime: <b>{int(time.time() - _start_time)}s</b></p>
<p>Total Requests: <b>{m["requests_total"]}</b></p>
<p>Cache: <b>{m["cache_hits"]}</b> hits / <b>{m["cache_misses"]}</b> misses</p>
<p>Cache Entries: <b>{cs.get("total_entries", 0)}/{cs.get("max_entries", 10000)}</b></p>
</div>
<div class="card">
<h2>Usage (last 7 days)</h2>
<p>Total calls: <b>{usage.get("total_calls", 0)}</b> | Today: <b>{usage.get("today_calls", 0)}</b></p>
<table><tr><th>Endpoint</th><th>Calls</th><th>Avg Latency</th><th>Success</th></tr>
"""
    for ep in usage.get("endpoints", []):
        html += f"<tr><td>{ep.get('endpoint','?')}</td><td>{ep.get('calls',0)}</td><td>{ep.get('avg_latency',0):.0f}ms</td><td>{ep.get('success',0)}/{ep.get('calls',1)}</td></tr>"

    html += """</table></div><div class="card"><h2>Pricing</h2><table><tr><th>Endpoint</th><th>Price (USDC)</th></tr>"""
    from engine.payments import PRICING
    for ep, price in sorted(PRICING.items()):
        html += f"<tr><td>{ep}</td><td>${price/100:.6f}</td></tr>"

    html += f"""</table></div><div class="card"><h2>Treasury Wallet</h2>
<p class="mono">{os.getenv('TREASURY_WALLET','Not configured')}</p></div>
</body></html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(422)
async def validation_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation Error", "detail": str(exc.detail) if hasattr(exc, 'detail') else str(exc)},
    )


@app.exception_handler(500)
async def internal_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": "An unexpected error occurred. Please try again later."},
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "false").lower() == "true"
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info",
    )
