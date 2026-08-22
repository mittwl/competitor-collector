#!/usr/bin/env python3
"""
analyse_competitors.py
----------------------
Takes captured competitor marketing text (web pages / EDM bodies) and asks
Gemini for a simple, structured observation per source.

Reads:  captured.json   -> [{ "source": "...", "content": "..." }, ...]
Writes: observations.json -> [{ six-field record }, ...]

The Gemini API key is read from the GEMINI_API_KEY environment variable,
which in GitHub Actions comes from a repository secret. Never hard-code it.
"""

import os
import json
import sys
import time
import requests

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
MODEL = "gemini-2.5-flash"  
API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
)

INPUT_FILE = "captured.json"
OUTPUT_FILE = "observations.json"

# ----------------------------------------------------------------------
# The prompt (same wording proven in the AI Builder test)
# ----------------------------------------------------------------------
INSTRUCTION = """You are analysing a single piece of competitor marketing content (a web page or a marketing email). Read only the text provided. Do not infer strategy, intent, or anything not explicitly present in the text.

Use exactly these fields:
- "source": the identifier passed to you for this content
- "primary_offer": the dominant campaign or hero promotion shown in the main page content. Prioritise hero banners, major promotional headings and repeated campaign messages over navigation bars, delivery thresholds, loyalty messages and newsletter sign-up offers. Use delivery or sign-up incentives only if no campaign promotion is present.
- "category_focus": the product category being pushed (e.g. "apparel", "beauty", "homeware", "seasonal"). If unclear, use "General".
- "hero_theme": the hero product or campaign theme in a short phrase.
- "hook": the emotional or tactical angle (e.g. "urgency", "price-led", "lifestyle", "clearance", "new arrival").
- "notable": one plain-English sentence describing what stands out. Keep it factual and grounded in the text.

If the text is empty, junk, or unreadable, set "primary_offer" to "No readable content" and the other analysis fields to "N/A"."""

# ----------------------------------------------------------------------
# Structured output schema -> forces clean JSON, no fences, no preamble
# ----------------------------------------------------------------------
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source": {"type": "STRING"},
        "primary_offer": {"type": "STRING"},
        "category_focus": {"type": "STRING"},
        "hero_theme": {"type": "STRING"},
        "hook": {"type": "STRING"},
        "notable": {"type": "STRING"},
    },
    "required": [
        "source",
        "primary_offer",
        "category_focus",
        "hero_theme",
        "hook",
        "notable",
    ],
}


def analyse_one(api_key: str, source: str, content: str) -> dict:
    """Send one source's text to Gemini, return the parsed observation dict."""
    prompt = (
        f"{INSTRUCTION}\n\n"
        f'Source identifier: "{source}"\n\n'
        f"Content to analyse:\n{content}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,  # low = consistent, comparable week-on-week
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
        },
    }

    # Simple retry for transient errors / rate limits
    for attempt in range(3):
        resp = requests.post(
            API_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if resp.status_code == 200:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)  # guaranteed clean JSON via schema
        if resp.status_code in (429, 500, 503):
            time.sleep(2 * (attempt + 1))
            continue
        # Any other error: fail this source, don't kill the whole run
        break

    # Fallback record so one bad source never collapses the digest
    return {
        "source": source,
        "primary_offer": "Analysis failed",
        "category_focus": "N/A",
        "hero_theme": "N/A",
        "hook": "N/A",
        "notable": f"Gemini call failed (last status {resp.status_code}).",
    }


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY environment variable not set.")

    if not os.path.exists(INPUT_FILE):
        sys.exit(f"ERROR: {INPUT_FILE} not found. Run the capture step first.")

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        captured = json.load(f)

    observations = []
    for item in captured:
        source = item.get("source", "unknown_source")
        content = (item.get("content") or "").strip()
        print(f"Analysing: {source} ({len(content)} chars)")
        observations.append(analyse_one(api_key, source, content))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(observations, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Wrote {len(observations)} observations to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
