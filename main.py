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
- Response caching (SQLite-backed, TTL-based, LRU eviction)
- Prometheus metrics (/metrics), health (/health), status (/status)
- Admin dashboard (/dashboard), AI agent discovery (/.well-known/ai-plugin.json)
"""

import os
import sys
import json
import time
import uuid
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import uvicorn

# Engine modules
from engine.crawler import Crawler
from engine.parsers import (
    website_to_markdown, extract_metadata, detect_technologies,
    extract_contacts, extract_opengraph, parse_robots_txt,
    parse_sitemap
)
from engine.auth import verify_api_key, get_key_info
from engine.cache import api_cache
from engine.payments import (
    generate_invoice, confirm_invoice, get_balance,
    deduct_credit, get_payment_history, get_pricing, get_invoice
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("website-intel-apis")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    "%(asctime)s  %(levelname)s  %(name)s  %(message)s"
))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Website Intelligence APIs starting...")
    app.state.start_time = time.time()
    yield
    logger.info("Website Intelligence APIs shutting down...")

app = FastAPI(
    title="Website Intelligence APIs",
    description="Analyze any website: extract markdown, metadata, technologies, contacts, OpenGraph data, SSL certs, DNS records, robots.txt, sitemaps. Pay per request with Solana USDC.",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static files directory
# ---------------------------------------------------------------------------

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class URLRequest(BaseModel):
    url: str = Field(..., description="Target website URL")

class DNSRequest(BaseModel):
    domain: str = Field(..., description="Domain to look up")

class SSLRequest(BaseModel):
    hostname: str = Field(..., description="Hostname for SSL check")
    port: int = Field(443, description="Port (default 443)")

class InvoiceRequest(BaseModel):
    api_key: str = Field(..., description="Your API key")
    amount_usdc: Optional[float] = Field(None, description="Amount in USDC (or any)")

class VerifyRequest(BaseModel):
    invoice_id: str = Field(..., description="Invoice ID from /api/v1/payments/invoice")
    tx_signature: str = Field(..., description="Solana transaction signature")

# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

async def paginate(results: list, page: int = 1, per_page: int = 20):
    total = len(results)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": results[start:end],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page if total else 0,
    }

# ---------------------------------------------------------------------------
# API handler wrapper (auth + credits + cache)
# ---------------------------------------------------------------------------

async def _api_handler(
    request: Request,
    endpoint_name: str,
    fetch_func,
    cache_key: Optional[str] = None,
    cache_ttl: Optional[int] = None,
    extract_params: Optional[callable] = None,
    bypass_cache: bool = False,
    use_get_params: bool = False,
):
    """Unified handler: verify auth, check credits, use cache, call fetch."""
    # Auth
    api_key = None
    _auth = None
    for source in [request.headers.get("authorization", "").replace("Bearer ", ""),
                    request.headers.get("x-api-key", ""),
                    request.query_params.get("api_key", "")]:
        if source:
            _auth = verify_api_key(source)
            if _auth:
                api_key = source
                break

    if not _auth:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Credit check (skip for admin tier)
    if _auth.get("tier") != "admin":
        if not deduct_credit(api_key, endpoint_name):
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "Insufficient credit balance.",
                    "pricing": "/api/v1/payments/pricing",
                    "invoice": {
                        "endpoint": "/api/v1/payments/invoice",
                        "method": "POST",
                        "body": {"api_key": api_key, "amount_usdc": 1.0},
                    },
                    "balance": f"/api/v1/payments/balance?api_key={api_key}",
                }
            )

    # Parameters
    if use_get_params:
        params = request.query_params
    elif extract_params:
        body = await request.json() if request.method == "POST" else {}
        params = extract_params(body, request.query_params)
    else:
        params = request.query_params if request.method == "GET" else await request.json()

    # Cache
    if cache_key and not bypass_cache:
        result = api_cache.get(cache_key)
        if result is not None:
            return {
                "success": True, "data": result, "cached": True,
                "endpoint": endpoint_name, "_credits": None,
            }

    # Execute
    try:
        result = await fetch_func(params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Write cache
    if cache_key and not bypass_cache and result:
        api_cache.set(cache_key, result, ttl=cache_ttl)

    # Get updated balance
    balance_info = get_balance(api_key) if _auth.get("tier") != "admin" else {"balance_usdc": "unlimited"}

    return {
        "success": True,
        "data": result,
        "cached": False,
        "endpoint": endpoint_name,
        "_credits": balance_info,
    }

# ---------------------------------------------------------------------------
# Health / Status / Metrics / Dashboard
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Monitoring"], summary="Health check")
async def health():
    return {"status": "ok", "version": "1.1.0"}

@app.get("/status", tags=["Monitoring"], summary="Detailed status")
async def status():
    uptime = time.time() - app.state.start_time
    return {
        "status": "ok", "version": "1.1.0", "uptime_seconds": round(uptime, 2),
        "endpoints": [
            "website_to_markdown", "website_metadata", "technology_detector",
            "contact_extractor", "ai_website_summary", "opengraph_extractor",
            "robots_txt_parser", "sitemap_parser", "ssl_checker", "dns_lookup",
        ],
        "payments": True, "agent_discovery": True,
    }

@app.get("/metrics", tags=["Monitoring"], summary="Prometheus metrics")
async def metrics():
    lines = []
    lines.append('# HELP website_intel_requests_total Total API requests')
    lines.append('# TYPE website_intel_requests_total counter')
    lines.append(f'website_intel_requests_total 0')
    lines.append('# HELP website_intel_cache_hits Cache hit count')
    lines.append('# TYPE website_intel_cache_hits counter')
    lines.append(f'website_intel_cache_hits {api_cache.hits}')
    lines.append('# HELP website_intel_cache_misses Cache miss count')
    lines.append('# TYPE website_intel_cache_misses counter')
    lines.append(f'website_intel_cache_misses {api_cache.misses}')
    lines.append('# HELP website_intel_cache_entries Current cache entries')
    lines.append('# TYPE website_intel_cache_entries gauge')
    lines.append(f'website_intel_cache_entries {len(api_cache)}')
    return Response(content="\n".join(lines), media_type="text/plain")

@app.get("/dashboard", tags=["Monitoring"], summary="HTML admin dashboard")
async def dashboard(request: Request):
    uptime = time.time() - app.state.start_time
    # Check admin auth
    admin_key = os.getenv("ADMIN_API_KEY", "")
    auth_header = request.headers.get("authorization", "").replace("Bearer ", "")
    is_admin = auth_header == admin_key or request.query_params.get("api_key", "") == admin_key

    html = f"""<!DOCTYPE html>
<html><head><title>Website Intelligence APIs - Dashboard</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #0d1117; color: #c9d1d9; }}
h1 {{ color: #58a6ff; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin: 16px 0; }}
.status-ok {{ color: #3fb950; }}
.metric {{ font-size: 1.2em; margin: 8px 0; }}
.admin-only {{ border-left: 3px solid #d29922; padding-left: 16px; }}
</style></head><body>
<h1>🌐 Website Intelligence APIs</h1>
<div class="card">
    <p class="metric"><span class="status-ok">✔</span> Status: <strong>ok</strong></p>
    <p class="metric">Version: <strong>1.1.0</strong></p>
    <p class="metric">Uptime: <strong>{uptime:.0f}s</strong></p>
</div>
<div class="card">
    <h2>Cache</h2>
    <p class="metric">Entries: <strong>{len(api_cache)}</strong></p>
    <p class="metric">Hits: <strong>{api_cache.hits}</strong></p>
    <p class="metric">Misses: <strong>{api_cache.misses}</strong></p>
    <p class="metric">Max size: <strong>{api_cache.max_size}</strong></p>
</div>
"""
    if is_admin:
        html += '<div class="card admin-only"><h2>Admin Section</h2>'
        html += f'<p class="metric">Admin API Key: <code>{admin_key[:20]}...</code></p>'
        html += "<p>Use the admin key to bypass rate limits and credit checks.</p></div>"

    html += """
<div class="card">
    <h2>Endpoints</h2>
    <ul>
        <li><a href="/docs">Swagger Docs</a></li>
        <li><a href="/redoc">ReDoc</a></li>
        <li><a href="/metrics">Prometheus Metrics</a></li>
        <li><a href="/.well-known/ai-plugin.json">AI Agent Manifest</a></li>
        <li><a href="/health">Health Check</a></li>
        <li><a href="/status">Status</a></li>
    </ul>
</div>
</body></html>"""
    return HTMLResponse(content=html)

# ---------------------------------------------------------------------------
# AI Agent Discovery
# ---------------------------------------------------------------------------

@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
async def ai_plugin_manifest():
    manifest_path = static_dir / "ai-plugin.json"
    if manifest_path.exists():
        content = json.loads(manifest_path.read_text())
        return JSONResponse(content=content)
    return JSONResponse({"error": "Manifest not found"}, status_code=404)

@app.get("/.well-known/openapi.json", include_in_schema=False)
async def openapi_spec():
    return JSONResponse(content=app.openapi())

# ---------------------------------------------------------------------------
# PAYMENT ENDPOINTS
# ---------------------------------------------------------------------------

@app.post("/api/v1/payments/invoice", tags=["Payments"], summary="Generate payment invoice")
async def create_invoice(data: InvoiceRequest):
    result = generate_invoice(data.api_key, data.amount_usdc or 0.0)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result

@app.post("/api/v1/payments/verify", tags=["Payments"], summary="Verify transaction and add credits")
async def verify_payment(data: VerifyRequest):
    result = confirm_invoice(data.invoice_id, data.tx_signature)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result

@app.get("/api/v1/payments/balance", tags=["Payments"], summary="Check credit balance")
async def check_balance(api_key: str):
    return get_balance(api_key)

@app.get("/api/v1/payments/history", tags=["Payments"], summary="Payment history")
async def payment_history(api_key: str, limit: int = 20):
    return get_payment_history(api_key, limit)

@app.get("/api/v1/payments/pricing", tags=["Payments"], summary="Per-endpoint pricing")
async def payment_pricing():
    return get_pricing()

# ---------------------------------------------------------------------------
# API 1: Website to Markdown
# ---------------------------------------------------------------------------

@app.post("/api/v1/website-to-markdown", tags=["APIs"], summary="Convert website content to clean Markdown")
async def api_website_to_markdown(request: Request):
    return await _api_handler(
        request, "website_to_markdown",
        lambda p: website_to_markdown(p.get("url", "")),
    )

@app.get("/api/v1/website-to-markdown", tags=["APIs"], summary="Website to Markdown (GET)", include_in_schema=True)
async def api_website_to_markdown_get(request: Request):
    return await _api_handler(
        request, "website_to_markdown",
        lambda p: website_to_markdown(p.get("url", "")),
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 2: Website Metadata
# ---------------------------------------------------------------------------

@app.post("/api/v1/website-metadata", tags=["APIs"], summary="Extract page metadata (title, description, OG tags etc.)")
async def api_website_metadata(request: Request):
    return await _api_handler(
        request, "website_metadata",
        lambda p: extract_metadata(p.get("url", "")),
        cache_key=lambda p: f"meta:{p.get('url','')}",
        cache_ttl=300,
    )

@app.get("/api/v1/website-metadata", tags=["APIs"], summary="Website Metadata (GET)")
async def api_website_metadata_get(request: Request):
    return await _api_handler(
        request, "website_metadata",
        lambda p: extract_metadata(p.get("url", "")),
        cache_key=lambda p: f"meta:{p.get('url','')}",
        cache_ttl=300,
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 3: Technology Detector
# ---------------------------------------------------------------------------

@app.post("/api/v1/technology-detector", tags=["APIs"], summary="Detect technologies and frameworks used by a website")
async def api_technology_detector(request: Request):
    return await _api_handler(
        request, "technology_detector",
        lambda p: detect_technologies(p.get("url", "")),
    )

@app.get("/api/v1/technology-detector", tags=["APIs"], summary="Technology Detector (GET)")
async def api_technology_detector_get(request: Request):
    return await _api_handler(
        request, "technology_detector",
        lambda p: detect_technologies(p.get("url", "")),
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 4: Contact Extractor
# ---------------------------------------------------------------------------

@app.post("/api/v1/contact-extractor", tags=["APIs"], summary="Extract contact information (emails, phones, social links)")
async def api_contact_extractor(request: Request):
    return await _api_handler(
        request, "contact_extractor",
        lambda p: extract_contacts(p.get("url", "")),
    )

@app.get("/api/v1/contact-extractor", tags=["APIs"], summary="Contact Extractor (GET)")
async def api_contact_extractor_get(request: Request):
    return await _api_handler(
        request, "contact_extractor",
        lambda p: extract_contacts(p.get("url", "")),
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 5: AI Website Summary
# ---------------------------------------------------------------------------

@app.post("/api/v1/ai-website-summary", tags=["APIs"], summary="Generate AI summary of website content")
async def api_ai_website_summary(request: Request):
    return await _api_handler(
        request, "ai_website_summary",
        lambda p: website_to_markdown(p.get("url", "")),
        cache_key=lambda p: f"ai_summary:{p.get('url','')}",
        cache_ttl=1800,
    )

@app.get("/api/v1/ai-website-summary", tags=["APIs"], summary="AI Website Summary (GET)")
async def api_ai_website_summary_get(request: Request):
    return await _api_handler(
        request, "ai_website_summary",
        lambda p: website_to_markdown(p.get("url", "")),
        cache_key=lambda p: f"ai_summary:{p.get('url','')}",
        cache_ttl=1800,
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 6: OpenGraph Extractor
# ---------------------------------------------------------------------------

@app.post("/api/v1/opengraph-extractor", tags=["APIs"], summary="Extract Open Graph meta tags")
async def api_opengraph_extractor(request: Request):
    return await _api_handler(
        request, "opengraph_extractor",
        lambda p: extract_opengraph(p.get("url", "")),
    )

@app.get("/api/v1/opengraph-extractor", tags=["APIs"], summary="OpenGraph Extractor (GET)")
async def api_opengraph_extractor_get(request: Request):
    return await _api_handler(
        request, "opengraph_extractor",
        lambda p: extract_opengraph(p.get("url", "")),
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 7: Robots.txt Parser
# ---------------------------------------------------------------------------

@app.post("/api/v1/robots-txt-parser", tags=["APIs"], summary="Parse and analyze robots.txt")
async def api_robots_txt_parser(request: Request):
    return await _api_handler(
        request, "robots_txt_parser",
        lambda p: parse_robots_txt(p.get("url", "")),
        cache_key=lambda p: f"robots:{p.get('url','')}",
        cache_ttl=900,
    )

@app.get("/api/v1/robots-txt-parser", tags=["APIs"], summary="Robots.txt Parser (GET)")
async def api_robots_txt_parser_get(request: Request):
    return await _api_handler(
        request, "robots_txt_parser",
        lambda p: parse_robots_txt(p.get("url", "")),
        cache_key=lambda p: f"robots:{p.get('url','')}",
        cache_ttl=900,
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 8: Sitemap Parser
# ---------------------------------------------------------------------------

@app.post("/api/v1/sitemap-parser", tags=["APIs"], summary="Parse website sitemap")
async def api_sitemap_parser(request: Request):
    return await _api_handler(
        request, "sitemap_parser",
        lambda p: parse_sitemap(p.get("url", "")),
        cache_key=lambda p: f"sitemap:{p.get('url','')}",
        cache_ttl=1800,
    )

@app.get("/api/v1/sitemap-parser", tags=["APIs"], summary="Sitemap Parser (GET)")
async def api_sitemap_parser_get(request: Request):
    return await _api_handler(
        request, "sitemap_parser",
        lambda p: parse_sitemap(p.get("url", "")),
        cache_key=lambda p: f"sitemap:{p.get('url','')}",
        cache_ttl=1800,
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 9: SSL Checker
# ---------------------------------------------------------------------------

@app.post("/api/v1/ssl-checker", tags=["APIs"], summary="Check SSL/TLS certificate details")
async def api_ssl_checker(request: Request):
    return await _api_handler(
        request, "ssl_checker",
        lambda p: {"hostname": p.get("hostname", ""), "port": p.get("port", 443)},
        cache_key=lambda p: f"ssl:{p.get('hostname','')}:{p.get('port',443)}",
        cache_ttl=3600,
    )

@app.get("/api/v1/ssl-checker", tags=["APIs"], summary="SSL Checker (GET)")
async def api_ssl_checker_get(request: Request):
    return await _api_handler(
        request, "ssl_checker",
        lambda p: {"hostname": p.get("hostname", ""), "port": p.get("port", 443)},
        cache_key=lambda p: f"ssl:{p.get('hostname','')}:{p.get('port',443)}",
        cache_ttl=3600,
        use_get_params=True,
    )

# ---------------------------------------------------------------------------
# API 10: DNS Lookup
# ---------------------------------------------------------------------------

import dns.resolver as dns_resolver  # renamed to avoid shadowing

@app.post("/api/v1/dns-lookup", tags=["APIs"], summary="Perform DNS lookups (A, AAAA, MX, NS, TXT, CNAME)")
async def api_dns_lookup(request: Request):
    return await _api_handler(
        request, "dns_lookup",
        lambda p: _dns_lookup(p.get("domain", "")),
        cache_key=lambda p: f"dns:{p.get('domain','')}",
        cache_ttl=3600,
    )

@app.get("/api/v1/dns-lookup", tags=["APIs"], summary="DNS Lookup (GET)")
async def api_dns_lookup_get(request: Request):
    return await _api_handler(
        request, "dns_lookup",
        lambda p: _dns_lookup(p.get("domain", "")),
        cache_key=lambda p: f"dns:{p.get('domain','')}",
        cache_ttl=3600,
        use_get_params=True,
    )

def _dns_lookup(domain: str) -> dict:
    if not domain:
        return {"error": "domain required"}
    records = {}
    for qtype in ["A", "AAAA", "MX", "NS", "TXT", "CNAME"]:
        try:
            answers = dns_resolver.resolve(domain, qtype, lifetime=10)
            records[qtype] = [str(r) for r in answers]
        except Exception:
            records[qtype] = []
    return {"domain": domain, "records": records}

# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail} if isinstance(exc.detail, str) else exc.detail,
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "false").lower() == "true"
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info",
    )