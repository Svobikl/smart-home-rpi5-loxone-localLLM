#!/usr/bin/env python3
"""Fast Crawl4AI wrapper for Hermes Agent on RPi5.
Supports:
  crawl4ai_cli.py crawl <url> [--max-chars 4000]
  crawl4ai_cli.py search "<query>" [--max-results 3] [--max-chars 3500]
Uses Crawl4AI AsyncWebCrawler (with AsyncHTTPCrawlerStrategy fast path and Playwright fallback).
"""
import asyncio
import os
import sys

_LITERT_PY = "/mnt/nvme/ai_models/litert_env/bin/python3"
if os.path.exists(_LITERT_PY) and "/mnt/nvme/ai_models/litert_env" not in sys.prefix:
    os.execv(_LITERT_PY, [_LITERT_PY] + sys.argv)

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy


async def crawl_single_url(url: str, max_chars: int = 4000) -> str:
    try:
        async with AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy()) as crawler:
            res = await crawler.arun(url=url, config=CrawlerRunConfig())
            if res and res.success and res.markdown and len(res.markdown.strip()) > 80:
                return res.markdown.strip()[:max_chars]
    except Exception:
        pass
    async with AsyncWebCrawler() as crawler:
        res = await crawler.arun(url=url, config=CrawlerRunConfig())
        if res and res.success and res.markdown:
            return res.markdown.strip()[:max_chars]
        raise RuntimeError(f"Crawl4AI failed for {url}: {getattr(res, 'error_message', 'unknown error')}")


async def search_and_crawl(query: str, max_results: int = 3, max_chars: int = 3500) -> str:
    from ddgs import DDGS

    hits = list(DDGS().text(query, region="cz-cs", max_results=max_results))
    if not hits:
        return f"Žádné výsledky vyhledávání pro dotaz: {query}"

    lines = [f"# Výsledky vyhledávání (Crawl4AI) pro: {query}\n"]
    async with AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy()) as crawler:
        for idx, h in enumerate(hits, 1):
            title = h.get("title", "")
            href = h.get("href", "")
            body = h.get("body", "")
            lines.append(f"## {idx}. {title}\nURL: {href}\nSouhrn: {body}")
            if href:
                try:
                    res = await asyncio.wait_for(crawler.arun(url=href, config=CrawlerRunConfig()), timeout=10.0)
                    if res and res.success and res.markdown:
                        snippet = res.markdown.strip()[:1000]
                        lines.append(f"Extrahovaný obsah (Crawl4AI):\n{snippet}\n")
                except Exception:
                    pass
    out = "\n".join(lines).strip()
    return out[:max_chars]


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage: crawl4ai_cli.py <crawl <url>|search \"<query>\"> [--max-chars N]")
        return 0
    mode = args[0].lower()
    max_chars = 4000
    if "--max-chars" in args:
        idx = args.index("--max-chars")
        if idx + 1 < len(args):
            max_chars = int(args[idx + 1])
            args = args[:idx] + args[idx + 2 :]
    if mode == "crawl" and len(args) >= 2:
        print(asyncio.run(crawl_single_url(args[1], max_chars=max_chars)))
        return 0
    if mode == "search" and len(args) >= 2:
        query = " ".join(args[1:])
        print(asyncio.run(search_and_crawl(query, max_chars=max_chars)))
        return 0
    print("Invalid arguments. Use 'crawl <url>' or 'search <query>'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
