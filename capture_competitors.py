#!/usr/bin/env python3
"""
capture_competitors.py
----------------------
Renders each competitor page with a real browser (Playwright/Chromium) so
JavaScript-loaded banners and deals actually appear, then extracts clean
visible text and saves it for the analysis step.

Reads:  targets.json      -> [{ "source": "...", "url": "..." }, ...]
Writes: captured.json     -> [{ "source": "...", "content": "..." }, ...]

Also drops a full-page screenshot per source into ./screenshots/ for your
own eyes in the digest folder. The analysis step only uses the text.
"""

import json
import os
import re
import sys
from playwright.sync_api import sync_playwright

TARGETS_FILE = "targets.json"
OUTPUT_FILE = "captured.json"
SHOT_DIR = "screenshots"

# Nav/boilerplate lines worth trimming so the model sees signal, not chrome.
# Kept deliberately light: over-filtering risks binning real offers.
NOISE_PATTERNS = [
    r"^\s*(home|menu|search|sign in|log in|register|my account|cart|basket|wishlist)\s*$",
    r"^\s*(cookie|we use cookies|accept all cookies|privacy|terms).*$",
    r"^\s*(skip to (main )?content)\s*$",
]
NOISE_RE = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]


def clean_text(raw: str) -> str:
    """Collapse whitespace and drop obvious nav/cookie noise, line by line."""
    lines = [ln.strip() for ln in raw.splitlines()]
    kept = []
    seen = set()
    for ln in lines:
        if not ln:
            continue
        if any(rx.match(ln) for rx in NOISE_RE):
            continue
        # de-duplicate repeated nav labels that appear many times
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(ln)
    text = "\n".join(kept)
    # hard cap so a huge page doesn't bloat the API call; top of page = the good bit
    return text[:6000]


def capture_one(page, source: str, url: str) -> dict:
    """Load one URL, wait for content, return {source, content}."""
    try:
        # strip tracking params (e.g. ?srsltid=...) for a clean fetch
        clean_url = url.split("?")[0]
        page.goto(clean_url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2000)
        # nudge lazy-loaded content (retail SPAs often only render deals on scroll)
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(2000)
        page.mouse.wheel(0, -2000)
        page.wait_for_timeout(1000)

        os.makedirs(SHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(SHOT_DIR, f"{source}.png"), full_page=True)

        # innerText gives rendered, visible text only (not raw HTML/hidden nodes)
        raw = page.inner_text("body")
        content = clean_text(raw)
        print(f"  {source}: captured {len(content)} chars")
        return {"source": source, "content": content}

    except Exception as e:
        # never let one bad page kill the run; flag it and move on
        print(f"  {source}: FAILED ({e})")
        return {"source": source, "content": ""}


def main() -> None:
    if not os.path.exists(TARGETS_FILE):
        sys.exit(f"ERROR: {TARGETS_FILE} not found. Create your list of targets.")

    with open(TARGETS_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # a real-looking UA reduces the odds of being served a stripped page
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        for t in targets:
            source = t.get("source", "unknown")
            url = t.get("url", "")
            print(f"Capturing {source} -> {url}")
            captured.append(capture_one(page, source, url))

        browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Wrote {len(captured)} sources to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
