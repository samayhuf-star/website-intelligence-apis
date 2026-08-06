"""API 3: Technology Detector."""
from engine.crawler import Crawler
from engine.parsers import detect_tech_from_html

async def detect_technology(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    headers = result["headers"]
    html_tech = detect_tech_from_html(soup)
    header_tech = {}
    server = headers.get("Server","")
    if server: header_tech["server"] = server
    if "X-Powered-By" in headers: header_tech["x-powered-by"] = headers["X-Powered-By"]
    if "CF-Ray" in headers: header_tech["cloudflare"] = "CDN"
    all_tech = {**html_tech, **header_tech}
    return {"url": url, "success": True, "technologies": all_tech, "ssl_enabled": url.startswith("https://"), "redirected": result["url"] != url, "final_url": result["url"], "http_status": result["status"], "technology_count": len(all_tech)}
