"""API 2: Website Metadata."""
import re
from urllib.parse import urljoin, urlparse
from engine.crawler import Crawler
from engine.parsers import extract_meta, extract_headings, extract_images_meta, extract_links, extract_contacts_from_page

async def extract_website_metadata(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    meta = extract_meta(soup, url)
    headings = extract_headings(soup)
    images = extract_images_meta(soup, url)
    links = extract_links(soup, url)
    contacts = extract_contacts_from_page(soup, url)
    html_tag = soup.find("html")
    lang = html_tag.get("lang", "") if html_tag else ""
    favicon = None
    for link in soup.find_all("link", rel=re.compile(r"(icon|shortcut icon|apple-touch-icon)", re.I)):
        h = link.get("href","")
        if h: favicon = urljoin(url, h); break
    if not favicon:
        p = urlparse(url); favicon = f"{p.scheme}://{p.netloc}/favicon.ico"
    return {"url": url, "success": True, "metadata": meta, "headings": headings, "images": images, "links": links, "contacts": contacts, "language": lang or "unknown", "favicon": favicon, "page_size_bytes": len(result["text"]), "http_status": result["status"]}
