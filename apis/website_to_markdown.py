"""API 1: Website to Markdown."""
import re
from urllib.parse import urljoin
from engine.crawler import Crawler

async def convert_to_markdown(url: str, proxy: str = None) -> dict:
    crawler = Crawler(proxy=proxy)
    url = crawler.normalize_url(url)
    result = await crawler.fetch(url)
    if not result["success"]:
        return {"url": url, "success": False, "error": result["error"]}
    soup = crawler.soup(result["text"])
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "form", "svg", "button", "input"]):
        tag.decompose()
    lines = []
    _process_node(soup.body or soup, lines, url)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return {"url": url, "success": True, "title": soup.title.get_text(strip=True) if soup.title else "", "word_count": len(text.split()), "char_count": len(text), "markdown": text}

def _process_node(node, lines, base_url):
    for child in node.children:
        if child.name is None:
            t = child.strip()
            if t:
                lines.append(t)
            continue
        tag = child.name.lower()
        text = child.get_text(strip=True)
        if tag in ("h1","h2","h3","h4","h5","h6"):
            if text: lines.append(f"\n{'#'*int(tag[1])} {text}\n")
        elif tag == "p":
            if text: lines.append(f"\n{text}\n")
        elif tag in ("ul","ol"):
            for i, li in enumerate(child.find_all("li", recursive=False)):
                t = li.get_text(strip=True)
                if t: lines.append(f"{'  ' if li.find_parent(['ul','ol']) else ''}{"" if True else ""}{f'{i+1}.' if tag=='ol' else '-'} {t}")
        elif tag == "blockquote":
            for l in text.split("\n"):
                if l.strip(): lines.append(f"> {l.strip()}")
        elif tag == "pre":
            lines.append(f"\n```\n{child.get_text()}\n```\n")
        elif tag == "a":
            h = child.get("href","")
            if text and h and not h.startswith(("#","javascript:")):
                lines.append(f"[{text}]({urljoin(base_url,h)})")
        elif tag == "img":
            s = child.get("src","")
            if s: lines.append(f"![{child.get('alt','')}]({urljoin(base_url,s)})")
        elif tag in ("div","section","article","main","span"):
            _process_node(child, lines, base_url)
        elif tag == "hr":
            lines.append("\n---\n")
        elif tag == "br":
            lines.append("  \n")
