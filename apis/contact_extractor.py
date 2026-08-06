"""API 4: Contact Extractor."""
from engine.crawler import Crawler
from engine.parsers import extract_contacts_from_page

async def extract_contacts(url: str, deep_crawl: bool = False, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    contacts = extract_contacts_from_page(soup, url)
    if deep_crawl:
        for page_path in ["/contact", "/about", "/contact-us", "/about-us", "/support"]:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            page_url = f"{parsed.scheme}://{parsed.netloc}{page_path}"
            page_result = await crawler.fetch(page_url, timeout=15)
            if page_result["success"]:
                page_soup = crawler.soup(page_result["text"])
                page_contacts = extract_contacts_from_page(page_soup, page_url)
                for key in ["emails", "phones"]:
                    contacts[key] = list(set(contacts[key] + page_contacts[key]))
                if page_contacts["social_links"]:
                    contacts["social_links"] = {**contacts["social_links"], **page_contacts["social_links"]}
                if not contacts["address"] and page_contacts["address"]:
                    contacts["address"] = page_contacts["address"]
    return {"url": url, "success": True, **contacts, "deep_crawl": deep_crawl, "sources": [url] + ([f"{urlparse(url).scheme}://{urlparse(url).netloc}{p}" for p in ["/contact","/about","/contact-us","/about-us","/support"] if deep_crawl]) if deep_crawl else [url]}
