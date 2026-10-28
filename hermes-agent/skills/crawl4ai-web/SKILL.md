---
name: crawl4ai-web
description: Ultra-fast LLM-friendly web crawling, scraping, and search on RPi5 powered by Crawl4AI (https://github.com/unclecode/crawl4AI). Converts any web page or search query into clean Markdown.
version: 1.0.0
metadata:
  hermes:
    tags: [crawl4ai, web-search, scraping, markdown, research, internet]
---

# Crawl4AI — Rychlé prohledávání webu a extrakce stránek do Markdownu

Pro vyhledávání aktuálních informací na internetu nebo stažení libovolné webové stránky volej nástroj `terminal`:

## 1. Vyhledávání na internetu + extrakce obsahu přes Crawl4AI
```bash
python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py search "<dotaz>"
```

## 2. Stažení a převod konkrétní URL do čistého Markdownu
```bash
python3 ~/.hermes/skills/crawl4ai-web/scripts/crawl4ai_cli.py crawl "https://example.com"
```

## 3. Přímé CLI `crwl`
```bash
crwl "https://example.com" -o markdown
```
