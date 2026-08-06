"""API 6: OpenGraph Extractor."""
import re
from engine.crawler import Crawler
from engine.parsers import extract_og_data

async def extract_opengraph(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    data = extract_og_data(soup, url)
    og = data.get("og", {})
    twitter = data.get("twitter", {})
    issues = []
    if not og.get("title"): issues.append("Missing og:title - important for social previews")
    if not og.get("description"): issues.append("Missing og:description")
    if not og.get("image"): issues.append("Missing og:image - shared links won't show a preview image")
    if not og.get("type"): issues.append("Missing og:type - should be 'website' or 'article'")
    if not og.get("url"): issues.append("Missing og:url")
    if not twitter.get("card"): issues.append("Missing twitter:card - Twitter/X previews may be suboptimal")
    if not twitter.get("site"): issues.append("Missing twitter:site")
    preview = {"title": og.get("title"), "description": og.get("description"), "image": og.get("image"), "image_alt": og.get("image:alt"), "favicon": None}
    return {"url": url, "success": True, "opengraph": og, "twitter_card": twitter, "preview": preview, "issues": issues, "issue_count": len(issues), "suggestions": ["Add og:image for link previews", "Ensure og:title and og:description are set", "Add twitter:card for X/Twitter previews"] if len(issues) > 2 else []}
