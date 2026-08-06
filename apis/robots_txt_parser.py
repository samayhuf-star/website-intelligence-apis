"""API 7: Robots.txt Parser."""
from urllib.parse import urlparse
from engine.crawler import Crawler

async def parse_robots_txt(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy, timeout=15)
    url = crawler.normalize_url(url)
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    result = await crawler.fetch(robots_url)
    if not result["success"]:
        return {"url": url, "robots_url": robots_url, "success": False, "error": f"No robots.txt found: {result['error']}"}
    content = result["text"]
    rules = {}
    current_agent = None
    sitemaps = []
    crawl_delay = None
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("user-agent:"):
            current_agent = line.split(":", 1)[1].strip() or "*"
            if current_agent not in rules:
                rules[current_agent] = {"allow": [], "disallow": []}
        elif line.lower().startswith("sitemap:"):
            sitemaps.append(line.split(":", 1)[1].strip())
        elif line.lower().startswith("crawl-delay:"):
            try:
                crawl_delay = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.lower().startswith("disallow:") and current_agent:
            path = line.split(":", 1)[1].strip()
            rules[current_agent]["disallow"].append(path if path else "/")
        elif line.lower().startswith("allow:") and current_agent:
            path = line.split(":", 1)[1].strip()
            if path:
                rules[current_agent]["allow"].append(path)
    key_pages = {"/": None, "/admin": None, "/wp-admin": None, "/login": None, "/api": None}
    for page in key_pages:
        if "*" in rules:
            blocked = any(p == page or (p.endswith("/") and page.startswith(p.rstrip("/"))) for p in rules["*"]["disallow"])
            key_pages[page] = "Blocked" if blocked else "Allowed"
    return {"url": url, "robots_url": robots_url, "success": True, "exists": True, "content_length": len(content), "rules": rules, "sitemaps": sitemaps, "crawl_delay": crawl_delay, "key_page_access": key_pages, "total_user_agents": len(rules), "has_wildcard": "*" in rules, "has_sitemaps": len(sitemaps) > 0}
