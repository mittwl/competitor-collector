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
from playwright_stealth import Stealth

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
    """Load one URL, capture diagnostics, and return visible page text."""

    clean_url = url.split("?")[0]
    os.makedirs(SHOT_DIR, exist_ok=True)

    try:
        print(f"  Navigating to {clean_url}")

        response = page.goto(
            clean_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        if response:
            print(f"  {source}: HTTP {response.status}")

        # Allow JavaScript applications and promotional content to render.
        page.wait_for_timeout(5000)

        # Trigger lazy-loaded homepage content.
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, -2000)
        page.wait_for_timeout(1500)

        # Save the screenshot before extracting text.
        page.screenshot(
            path=os.path.join(SHOT_DIR, f"{source}.png"),
            full_page=True,
        )

        raw = page.inner_text("body", timeout=10000)
        content = clean_text(raw)

        print(f"  {source}: captured {len(content)} chars")
        print(f"  {source}: title = {page.title()}")
        print(f"  {source}: final URL = {page.url}")

        return {
            "source": source,
            "content": content,
        }

    except Exception as error:
        print(f"  {source}: FAILED ({error})")

        # Still capture the current browser state when possible.
        try:
            page.screenshot(
                path=os.path.join(SHOT_DIR, f"{source}_failed.png"),
                full_page=True,
            )
            print(f"  {source}: saved failure screenshot")
        except Exception as screenshot_error:
            print(
                f"  {source}: could not save failure screenshot "
                f"({screenshot_error})"
            )

        try:
            fallback_text = page.inner_text("body", timeout=5000)
            fallback_content = clean_text(fallback_text)

            print(
                f"  {source}: recovered "
                f"{len(fallback_content)} chars after navigation failure"
            )

            return {
                "source": source,
                "content": fallback_content,
            }

        except Exception:
            return {
                "source": source,
                "content": "",
            }


def main() -> None:
    if not os.path.exists(TARGETS_FILE):
        sys.exit(f"ERROR: {TARGETS_FILE} not found. Create your list of targets.")

    with open(TARGETS_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    captured = []
    with Stealth().use_sync(sync_playwright()) as p:
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
    

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Wrote {len(captured)} sources to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
