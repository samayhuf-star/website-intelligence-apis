"""API 5: AI Website Summary."""
from engine.crawler import Crawler
from engine.parsers import extract_meta, extract_headings, extract_contacts_from_page, detect_tech_from_html

async def generate_website_summary(url: str, use_ai: bool = False, api_key: str = "", proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url, timeout=20)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    meta = extract_meta(soup, url)
    headings = extract_headings(soup)
    contacts = extract_contacts_from_page(soup, url)
    tech = detect_tech_from_html(soup)
    body = soup.get_text(strip=True)[:5000]
    word_count = len(body.split())
    summary = {"url": url, "success": True, "title": meta.get("title"), "description": meta.get("description"), "word_count": word_count, "estimated_headings": sum(len(v) for v in headings.values()), "technologies": list(tech.keys()), "contacts": {"emails": len(contacts["emails"]), "phones": len(contacts["phones"]), "social_profiles": sum(len(v) for v in contacts.get("social_links",{}).values())}, "http_status": result["status"], "page_size_kb": round(len(result["text"])/1024, 1), "first_words": body[:500]}
    if use_ai and api_key:
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": f"Summarize this website content in 2-3 sentences:\n\n{body[:3000]}"}], "max_tokens": 200}, timeout=15)
                if r.status_code == 200:
                    summary["ai_summary"] = r.json()["choices"][0]["message"]["content"].strip()
                else:
                    summary["ai_summary"] = None
        except Exception:
            summary["ai_summary"] = None
    return summary
