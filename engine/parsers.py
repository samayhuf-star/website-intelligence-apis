"""Shared HTML parsers for all Website Intelligence API endpoints."""
import re
import json
from typing import List, Optional, Dict
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

def extract_meta(soup: BeautifulSoup, url: str) -> dict:
    meta = {}
    title_tag = soup.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else None
    desc_tag = soup.find("meta", attrs={"name": "description"})
    meta["description"] = desc_tag.get("content", "").strip() if desc_tag else None
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    meta["keywords"] = kw_tag.get("content", "").strip() if kw_tag else None
    og_tags = {}
    for tag in soup.find_all("meta", property=re.compile(r"^og:")):
        og_tags[tag.get("property")] = tag.get("content", "").strip()
    meta["og"] = og_tags
    twitter_tags = {}
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        twitter_tags[tag.get("name")] = tag.get("content", "").strip()
    meta["twitter"] = twitter_tags
    canonical = soup.find("link", rel="canonical")
    meta["canonical"] = canonical.get("href") if canonical else None
    meta["url"] = url
    return meta

def extract_contacts_from_page(soup: BeautifulSoup, base_url: str) -> dict:
    emails = set()
    for a in soup.find_all("a", href=re.compile(r"mailto:")):
        email = a.get("href", "").replace("mailto:", "").split("?")[0].strip()
        if email and "@" in email:
            emails.add(email)
    raw = soup.get_text()
    for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", raw):
        if not e.endswith((".png", ".jpg", ".gif", ".css", ".js")):
            emails.add(e.lower())
    phones = set()
    for a in soup.find_all("a", href=re.compile(r"tel:")):
        p = a.get("href", "").replace("tel:", "").split("?")[0].strip()
        if p:
            phones.add(re.sub(r"[^\d+\-\.()\s]", "", p).strip())
    social_patterns = {"facebook": r"facebook\.com/[\w.]+", "instagram": r"instagram\.com/[\w_]+", "twitter": r"(?:twitter|x)\.com/[\w_]+", "linkedin": r"linkedin\.com/(?:company|in)/[\w\-]+", "youtube": r"youtube\.com/(?:c|channel|user|@)/[\w_\-]+", "github": r"github\.com/[\w\-]+"}
    social = {k: [] for k in social_patterns}
    for a in soup.find_all("a", href=True):
        h = a["href"]
        for platform, pattern in social_patterns.items():
            m = re.search(pattern, h, re.I)
            if m:
                u = m.group(0)
                if not u.startswith("http"):
                    u = "https://" + u
                if u not in social[platform]:
                    social[platform].append(u)
    return {"emails": sorted(emails), "phones": sorted(set(p for p in phones if len(p) >= 7)), "social_links": {k: v for k, v in social.items() if v}, "address": None}

def extract_headings(soup: BeautifulSoup) -> dict:
    h = {}
    for level in range(1, 7):
        t = f"h{level}"
        h[t] = [el.get_text(strip=True) for el in soup.find_all(t) if el.get_text(strip=True)]
    return h

def extract_images_meta(soup: BeautifulSoup, base_url: str) -> dict:
    imgs = soup.find_all("img")
    missing = sum(1 for img in imgs if not img.get("alt", "").strip())
    lazy = sum(1 for img in imgs if img.get("loading") == "lazy")
    return {"total_images": len(imgs), "images_missing_alt": missing, "images_with_lazy_loading": lazy}

def extract_links(soup: BeautifulSoup, base_url: str) -> dict:
    internal, external = [], []
    domain = urlparse(base_url).netloc.lower()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base_url, href)
        link_domain = urlparse(full).netloc.lower()
        t = "internal" if link_domain == domain else "external"
        entry = {"url": full, "text": a.get_text(strip=True)[:100] or "[no text]"}
        if t == "internal":
            internal.append(entry)
        else:
            external.append(entry)
    return {"internal_links": len(internal), "external_links": len(external), "internal_link_list": internal[:100], "external_link_list": external[:50]}

def detect_tech_from_html(soup: BeautifulSoup) -> dict:
    detected = {}
    html = str(soup)
    if re.search(r'wp-content|wp-includes|wordpress', html, re.I):
        detected["wordpress"] = "CMS"
    if soup.find("meta", attrs={"name": "generator", "content": re.compile(r"(?i)Shopify|Shopify")}):
        detected["shopify"] = "E-commerce"
    if re.search(r'next\.js|__NEXT_DATA__', html):
        detected["next_js"] = "Framework (React)"
    if re.search(r'__NUXT__', html):
        detected["nuxt_js"] = "Framework (Vue)"
    if re.search(r'gtag\(|google-analytics\.com|ga\.js', html):
        detected["google_analytics"] = "Analytics"
    if re.search(r'fbq\(|connect\.facebook\.net', html):
        detected["facebook_pixel"] = "Analytics"
    if re.search(r'cloudflare\.com|cf-ray|cdn-cgi/', html, re.I):
        detected["cloudflare"] = "CDN / Security"
    if re.search(r'fonts\.googleapis\.com', html):
        detected["google_fonts"] = "Fonts"
    return detected

def extract_og_data(soup: BeautifulSoup, url: str) -> dict:
    og, twitter = {}, {}
    for tag in soup.find_all("meta", property=re.compile(r"^og:")):
        prop = tag.get("property", "")[3:]
        content = tag.get("content", "").strip()
        if prop and content:
            og[prop] = content
    for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        name = tag.get("name", "")[8:]
        content = tag.get("content", "").strip()
        if name and content:
            twitter[name] = content
    return {"og": og, "twitter": twitter, "url": url}

def extract_all(soup: BeautifulSoup, url: str) -> dict:
    meta = extract_meta(soup, url)
    headings = extract_headings(soup)
    return {"metadata": meta, "headings": headings}