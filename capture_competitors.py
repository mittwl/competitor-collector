#!/usr/bin/env python3

import json
import os
import re
import sys

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


TARGETS_FILE = "targets.json"
OUTPUT_FILE = "captured.json"
SHOT_DIR = "screenshots"

NOISE_PATTERNS = [
    r"^\s*(home|menu|search|sign in|log in|register|my account|cart|basket|wishlist)\s*$",
    r"^\s*(cookie|we use cookies|accept all cookies|privacy|terms).*$",
    r"^\s*(skip to (main )?content)\s*$",
]

NOISE_RE = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in NOISE_PATTERNS
]


def clean_text(raw: str) -> str:
    """Remove obvious page noise and repeated lines."""

    lines = [
        line.strip()
        for line in raw.splitlines()
    ]

    kept = []
    seen = set()

    for line in lines:
        if not line:
            continue

        if any(pattern.match(line) for pattern in NOISE_RE):
            continue

        key = line.lower()

        if key in seen:
            continue

        seen.add(key)
        kept.append(line)

    return "\n".join(kept)[:6000]


def capture_one(page, source: str, url: str) -> dict:
    """Load one competitor page and extract visible text."""

    clean_url = url.split("?")[0]

    try:
        print(f"  Navigating to {clean_url}")

        response = page.goto(
            clean_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        if response:
            print(f"  {source}: HTTP {response.status}")

        # Allow JavaScript-driven content to render.
        page.wait_for_timeout(5000)

        # Trigger lazy-loaded homepage content.
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(2500)

        page.mouse.wheel(0, -2000)
        page.wait_for_timeout(1500)

        screenshot_path = os.path.join(
            SHOT_DIR,
            f"{source}.png",
        )

        page.screenshot(
            path=screenshot_path,
            full_page=True,
        )

        raw_text = page.inner_text(
            "body",
            timeout=10000,
        )

        content = clean_text(raw_text)

        print(f"  {source}: captured {len(content)} chars")
        print(f"  {source}: title = {page.title()}")
        print(f"  {source}: final URL = {page.url}")
        print(f"  {source}: screenshot = {screenshot_path}")

        return {
            "source": source,
            "content": content,
        }

    except Exception as error:
        print(f"  {source}: FAILED ({error})")

        failure_path = os.path.join(
            SHOT_DIR,
            f"{source}_failed.png",
        )

        try:
            page.screenshot(
                path=failure_path,
                full_page=True,
            )

            print(
                f"  {source}: saved failure screenshot "
                f"to {failure_path}"
            )

        except Exception as screenshot_error:
            print(
                f"  {source}: could not save failure screenshot "
                f"({screenshot_error})"
            )

        # A page can contain usable text even when navigation times out.
        try:
            raw_text = page.inner_text(
                "body",
                timeout=5000,
            )

            content = clean_text(raw_text)

            print(
                f"  {source}: recovered "
                f"{len(content)} chars after failure"
            )

            return {
                "source": source,
                "content": content,
            }

        except Exception as extraction_error:
            print(
                f"  {source}: could not recover page text "
                f"({extraction_error})"
            )

            return {
                "source": source,
                "content": "",
            }


def main() -> None:
    if not os.path.exists(TARGETS_FILE):
        sys.exit(
            f"ERROR: {TARGETS_FILE} was not found."
        )

    with open(TARGETS_FILE, "r", encoding="utf-8") as file:
        targets = json.load(file)

    print(f"Loaded {len(targets)} targets from {TARGETS_FILE}")

    if not targets:
        sys.exit(
            f"ERROR: {TARGETS_FILE} contains no targets."
        )

    os.makedirs(
        SHOT_DIR,
        exist_ok=True,
    )

    captured = []

    with Stealth().use_sync(sync_playwright()) as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            viewport={
                "width": 1440,
                "height": 900,
            },
            locale="en-NZ",
            timezone_id="Pacific/Auckland",
        )

        for target in targets:
            source = target.get(
                "source",
                "unknown",
            )

            url = target.get(
                "url",
                "",
            )

            if not url:
                print(
                    f"Skipping {source}: no URL provided"
                )
                continue

            print(f"Capturing {source} -> {url}")

            page = context.new_page()

            try:
                result = capture_one(
                    page,
                    source,
                    url,
                )

                captured.append(result)

            finally:
                page.close()

        context.close()
        browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            captured,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"Done. Wrote {len(captured)} sources "
        f"to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
