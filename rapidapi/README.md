# Website Intelligence APIs — RapidAPI Listing

## Overview
10 powerful website analysis APIs in one bundle. One API key gives you access to all endpoints.

## Endpoints

1. **Website → Markdown** — Convert any web page to clean Markdown
2. **Website Metadata** — Extract meta tags, headings, images, links, favicon
3. **Technology Detector** — Detect CMS, frameworks, analytics, CDN
4. **Contact Extractor** — Extract emails, phones, social links, addresses
5. **AI Website Summary** — Structured + AI-powered website summary
6. **OpenGraph Extractor** — OG tags, Twitter Cards, preview analysis
7. **Robots.txt Parser** — Parse crawl rules and discover sitemaps
8. **Sitemap Parser** — Discover and parse XML sitemaps
9. **SSL Checker** — Certificate details, expiry, security grade
10. **DNS Lookup** — A, AAAA, MX, NS, CNAME, subdomains

## Authentication
All endpoints require an API key: `Authorization: Bearer <key>`

## Pricing Tiers
- **Free**: 2 req/s, 100/day — $0
- **Starter**: 10 req/s, 5,000/day — $29/mo
- **Growth**: 30 req/s, 25,000/day — $79/mo
- **Enterprise**: 100 req/s, 100,000/day — $199/mo

## Code Examples

### cURL
```bash
curl -X POST "https://website-intelligence-apis.p.rapidapi.com/api/v1/website-to-markdown" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

### Python
```python
import requests
resp = requests.post(
    "https://website-intelligence-apis.p.rapidapi.com/api/v1/website-to-markdown",
    headers={"Authorization": "Bearer YOUR_API_KEY"},
    json={"url": "https://example.com"}
)
print(resp.json())
```

### JavaScript
```javascript
fetch('https://website-intelligence-apis.p.rapidapi.com/api/v1/website-to-markdown', {
  method: 'POST',
  headers: { 'Authorization': 'Bearer YOUR_API_KEY', 'Content-Type': 'application/json' },
  body: JSON.stringify({ url: 'https://example.com' })
})
.then(resp => resp.json())
.then(console.log)
```

## Rate Limits
Each response includes:
- `X-RateLimit-Limit`: Max requests/second
- `X-RateLimit-Remaining`: Remaining requests this second
- `X-RateLimit-Daily-Remaining`: Remaining daily requests
- `429 Too Many Requests`: Rate limit exceeded

## Error Codes
- `401`: Missing or invalid API key
- `422`: Invalid input or processing error
- `429`: Rate limit exceeded

## Support
Email: samayhuf@gmail.com