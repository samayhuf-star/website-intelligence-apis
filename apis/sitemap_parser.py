"""API 8: Sitemap Parser."""
import re, asyncio, xml.etree.ElementTree as ET
from urllib.parse import urlparse
from engine.crawler import Crawler

async def parse_sitemap(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy, timeout=15, max_retries=1)
    url = crawler.normalize_url(url)
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_urls = [f"{base}{p}" for p in ["/sitemap.xml","/sitemap_index.xml","/sitemap/","/sitemap.xml.gz","/wp-sitemap.xml","/sitemaps/sitemap.xml"]]
    all_urls = []
    async def try_fetch(sm_url):
        r = await crawler.fetch(sm_url)
        if not r["success"]:
            return []
        content = r["text"]
        if not content.strip().startswith("<"):
            return []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        if root.tag.endswith("sitemapindex"):
            urls = []
            for sm in root.findall("sm:sitemap", ns):
                loc = sm.find("sm:loc", ns)
                if loc is not None and loc.text:
                    cr = await crawler.fetch(loc.text.strip(), timeout=15)
                    if cr["success"]:
                        try:
                            cr_root = ET.fromstring(cr["text"])
                            for e in cr_root.findall("sm:url", ns):
                                l = e.find("sm:loc", ns)
                                if l is not None and l.text:
                                    urls.append({"loc": l.text.strip()})
                        except ET.ParseError:
                            pass
            return urls
        else:
            urls = []
            for e in root.findall("sm:url", ns):
                l = e.find("sm:loc", ns)
                if l is not None and l.text:
                    urls.append({"loc": l.text.strip()})
            return urls
    tasks = [try_fetch(sm) for sm in sitemap_urls]
    results = await asyncio.gather(*tasks)
    for urls in results:
        all_urls.extend(urls)
    return {"url": url, "success": True, "stats": {"total_urls": len(all_urls), "total_sitemaps": len([r for r in results if r])}, "sample_urls": [u["loc"] for u in all_urls[:25]], "all_urls": all_urls[:200]}
