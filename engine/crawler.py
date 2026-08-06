"""Shared web crawler using httpx with retries and UA rotation."""

import asyncio
import logging
import random
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


class Crawler:
    """Async crawler with retry, timeout, and robust error handling."""

    def __init__(self, timeout: int = 30, max_retries: int = 2, proxy: Optional[str] = None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy

    async def _get_client(self) -> httpx.AsyncClient:
        kwargs = {"timeout": httpx.Timeout(self.timeout), "follow_redirects": True}
        if self.proxy:
            kwargs["proxies"] = self.proxy
        return httpx.AsyncClient(**kwargs)

    async def fetch(self, url: str, headers: Optional[dict] = None, timeout: Optional[int] = None) -> dict:
        default_headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
        }
        if headers:
            default_headers.update(headers)
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with await self._get_client() as client:
                    resp = await client.get(url, headers=default_headers, timeout=timeout or self.timeout)
                    return {"success": True, "status": resp.status_code, "url": str(resp.url), "headers": dict(resp.headers), "text": resp.text, "error": None}
            except Exception as e:
                last_error = f"{e} (attempt {attempt}/{self.max_retries})"
                logger.warning(last_error)
                await asyncio.sleep(2 ** attempt)
        return {"success": False, "status": 0, "url": url, "headers": {}, "text": "", "error": last_error}

    def soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def normalize_url(url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url
