# Website Intelligence APIs — Architecture & Scaling Reference

> Version 1.0 — Last updated: 2026-08-06

---

## 1. Overall System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DigitalOcean Droplet                      │
│                  (s-1vcpu-1gb, $6/mo → scales vertically)        │
│                                                                  │
│  ┌──────────────┐    ┌──────────────────────────────────────┐   │
│  │   nginx:80    │───▶│   uvicorn (4 workers → 8 → 16)       │   │
│  │  (reverse     │    │   FastAPI app serving 10 endpoints   │   │
│  │   proxy)      │    │                                      │   │
│  └──────────────┘    │  ┌────────────────────────────────┐   │   │
│                      │  │  engine/                       │   │   │
│                      │  │  ├── crawler.py  (httpx async) │   │   │
│                      │  │  ├── parsers.py  (BeautifulSoup│   │   │
│                      │  │  ├── cache.py    (SQLite+TTL)  │   │   │
│                      │  │  └── auth.py     (rate limit,  │   │   │
│                      │  │                    usage track) │   │   │
│                      │  └────────────────────────────────┘   │   │
│                      │                                          │   │
│                      │  ┌────────────────────────────────┐   │   │
│                      │  │  apis/                         │   │   │
│                      │  │  10 isolated Python modules    │   │   │
│                      │  └────────────────────────────────┘   │   │
│                      │                                          │   │
│                      │  ┌────────────────────────────────┐   │   │
│                      │  │  Data Stores:                   │   │   │
│                      │  │  ├── usage.db   (SQLite)        │   │   │
│                      │  │  ├── cache.db   (SQLite)        │   │   │
│                      │  │  └── memory     (in-memory      │   │   │
│                      │  │                  rate windows)   │   │   │
│                      │  └────────────────────────────────┘   │   │
│                      └──────────────────────────────────────┘   │
│                                                                  │
│  External dependencies:                                           │
│  ├── Target websites (HTTP/HTTPS)                                 │
│  ├── DNS resolution (OS resolver + dig)                           │
│  └── OpenAI-compatible API (optional, API 5)                      │
└─────────────────────────────────────────────────────────────────┘
```

Full document covers: shared vs API-specific modules, rate limiting, caching strategy, queue architecture, retry policy, timeouts, cost per request, scaling plan (1k → 100k → 1M req/day), monitoring/alerting, testing strategy, disaster recovery.
