#!/usr/bin/env python3
"""
analyse_competitors.py
----------------------
Analyses competitor retail homepages using:

1. A full-page screenshot to determine visual hierarchy and prominence.
2. Extracted visible text to confirm exact offer wording and categories.

Reads:
    captured.json
    screenshots/<source>.png

Writes:
    observations.json

The Gemini API key is read from the GEMINI_API_KEY environment variable.
Never hard-code the API key.
"""

import base64
import json
import os
import sys
import time

import requests


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

MODEL = "gemini-2.5-flash"

API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/"
    f"models/{MODEL}:generateContent"
)

INPUT_FILE = "captured.json"
OUTPUT_FILE = "observations.json"
SCREENSHOTS_DIR = "screenshots"

# Gemini inline requests have a total size limit. Base64 increases image
# size by roughly one third, so this keeps the request comfortably below it.
MAX_SCREENSHOT_BYTES = 12_000_000


# ----------------------------------------------------------------------
# Analysis prompt
# ----------------------------------------------------------------------

INSTRUCTION = """
You are analysing one competitor retail homepage using:

1. A full-page screenshot showing the page as it appeared during capture.
2. Extracted visible text from the same page.

Use the screenshot as the authority for visual hierarchy, prominence and the
active carousel state. Use the extracted text to confirm exact wording,
discounts, category names and offer mechanics.

PROMOTIONAL HIERARCHY

Evaluate page content in this order:

1. The active hero banner or first major campaign visible in the screenshot.
2. Large visible promotional modules near the top of the page.
3. Repeated category promotions in the main page content.
4. Lower-page secondary campaign tiles.
5. Navigation, delivery messages, payment options, newsletter incentives and
   footer content.

Do not treat navigation labels, inactive carousel slides, delivery thresholds,
payment options, account messages, newsletter incentives or footer links as
the dominant campaign.

Only mention a seasonal event such as Father's Day as a secondary campaign
when it appears as a visible promotional module in the screenshot. A
navigation link alone is not evidence of a seasonal campaign.

If the extracted text contains promotional copy that is not visibly
represented in the screenshot, it may come from a hidden or inactive carousel
slide. Do not treat that content as active or prominent.

If several promotional messages are visible, identify one dominant campaign
based on size, vertical position, repetition and visual emphasis. Record other
materially visible promotions as secondary campaigns.

FIELD DEFINITIONS

- "source": use the supplied source identifier exactly.

- "primary_offer": the dominant visible campaign proposition or promotional
  offer. Prioritise the active hero and major upper-page modules. If the hero
  is a lifestyle proposition without a price discount, report that proposition
  rather than substituting a delivery or newsletter sign-up offer.

- "category_focus": the principal product category or categories represented
  by the dominant visible campaign.

- "hero_theme": a short description of the active hero campaign.

- "hook": the main creative or tactical approach, such as "price-led",
  "lifestyle", "new arrivals", "urgency", "clearance" or a concise combination.

- "secondary_campaigns": visible secondary campaigns that receive meaningful
  promotional treatment elsewhere on the page. Do not include navigation
  labels or minor utility messages.

- "utility_messages": persistent service or conversion messages such as free
  delivery, Afterpay, Click & Collect, loyalty prompts or newsletter sign-up
  incentives.

- "supporting_evidence": one concise sentence explaining which visible page
  elements support the primary_offer decision.

- "notable": one factual sentence summarising the dominant campaign and the
  most relevant visible secondary activity. Do not infer commercial strategy,
  business intent or performance beyond the supplied evidence.

- "capture_status": use "valid" when the screenshot shows a genuine retail
  homepage. Use "blocked_or_error" when it shows an error page, bot challenge,
  blank loading state, incomplete shell or unrelated fallback content.

If capture_status is "blocked_or_error":

- Set primary_offer to "Capture unavailable".
- Set category_focus, hero_theme and hook to "N/A".
- Use empty arrays for secondary_campaigns and utility_messages.
- Briefly explain the visible issue in supporting_evidence and notable.

Return only the structured JSON response. Do not include markdown,
commentary, code fences or a preamble.
""".strip()


# ----------------------------------------------------------------------
# Structured output schema
# ----------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "source": {
            "type": "STRING"
        },
        "primary_offer": {
            "type": "STRING"
        },
        "category_focus": {
            "type": "STRING"
        },
        "hero_theme": {
            "type": "STRING"
        },
        "hook": {
            "type": "STRING"
        },
        "secondary_campaigns": {
            "type": "ARRAY",
            "items": {
                "type": "STRING"
            }
        },
        "utility_messages": {
            "type": "ARRAY",
            "items": {
                "type": "STRING"
            }
        },
        "supporting_evidence": {
            "type": "STRING"
        },
        "notable": {
            "type": "STRING"
        },
        "capture_status": {
            "type": "STRING",
            "enum": [
                "valid",
                "blocked_or_error"
            ]
        }
    },
    "required": [
        "source",
        "primary_offer",
        "category_focus",
        "hero_theme",
        "hook",
        "secondary_campaigns",
        "utility_messages",
        "supporting_evidence",
        "notable",
        "capture_status"
    ]
}


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------

def fallback_observation(
    source: str,
    reason: str,
    status: str = "blocked_or_error",
) -> dict:
    """Return a complete fallback record matching the response schema."""

    return {
        "source": source,
        "primary_offer": "Capture unavailable",
        "category_focus": "N/A",
        "hero_theme": "N/A",
        "hook": "N/A",
        "secondary_campaigns": [],
        "utility_messages": [],
        "supporting_evidence": reason,
        "notable": reason,
        "capture_status": status,
    }


def encode_image(path: str) -> str:
    """Read a PNG file and return raw Base64 without a data URI prefix."""

    with open(path, "rb") as image_file:
        return base64.b64encode(
            image_file.read()
        ).decode("ascii")


def get_screenshot_path(source: str) -> str:
    """Return the expected screenshot path for a source."""

    return os.path.join(
        SCREENSHOTS_DIR,
        f"{source}.png",
    )


def analyse_one(
    api_key: str,
    source: str,
    content: str,
    screenshot_path: str,
) -> dict:
    """
    Analyse one competitor homepage using its screenshot and extracted text.
    """

    if not os.path.exists(screenshot_path):
        return fallback_observation(
            source=source,
            reason=(
                "No screenshot was available for this source, so the "
                "campaign hierarchy could not be assessed."
            ),
        )

    screenshot_size = os.path.getsize(screenshot_path)

    if screenshot_size == 0:
        return fallback_observation(
            source=source,
            reason=(
                "The screenshot file was empty, so no visual analysis "
                "could be completed."
            ),
        )

    if screenshot_size > MAX_SCREENSHOT_BYTES:
        return fallback_observation(
            source=source,
            reason=(
                "The screenshot exceeded the configured inline-image size "
                "limit and was not sent to Gemini."
            ),
        )

    if not content:
        content = (
            "No extracted visible text was available. Use the screenshot "
            "as the primary source and do not invent exact wording."
        )

    image_base64 = encode_image(screenshot_path)

    prompt = (
        f"{INSTRUCTION}\n\n"
        f'SOURCE IDENTIFIER:\n"{source}"\n\n'
        "EXTRACTED VISIBLE TEXT:\n"
        f"{content}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": image_base64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            # Low temperature helps keep weekly classifications consistent.
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
        },
    }

    last_status = None
    last_error = "Unknown Gemini error"

    for attempt in range(3):
        try:
            response = requests.post(
                API_URL,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )

            last_status = response.status_code

            if response.status_code == 200:
                response_json = response.json()

                response_text = (
                    response_json["candidates"][0]
                    ["content"]["parts"][0]["text"]
                )

                result = json.loads(response_text)

                # Keep the source identifier controlled by the pipeline.
                result["source"] = source

                return result

            last_error = response.text[:500]

            if response.status_code in (429, 500, 502, 503, 504):
                wait_seconds = 2 * (attempt + 1)

                print(
                    f"  {source}: Gemini returned "
                    f"{response.status_code}; retrying in "
                    f"{wait_seconds} seconds"
                )

                time.sleep(wait_seconds)
                continue

            print(
                f"  {source}: Gemini error "
                f"{response.status_code}: {last_error}"
            )

            break

        except requests.RequestException as error:
            last_error = str(error)

            if attempt < 2:
                wait_seconds = 2 * (attempt + 1)

                print(
                    f"  {source}: Gemini request failed; retrying in "
                    f"{wait_seconds} seconds ({error})"
                )

                time.sleep(wait_seconds)
                continue

    return {
        "source": source,
        "primary_offer": "Analysis failed",
        "category_focus": "N/A",
        "hero_theme": "N/A",
        "hook": "N/A",
        "secondary_campaigns": [],
        "utility_messages": [],
        "supporting_evidence": (
            "Gemini did not return a successful analysis response."
        ),
        "notable": (
            f"Gemini analysis failed with status {last_status}. "
            f"Error: {last_error}"
        ),
        "capture_status": "blocked_or_error",
    }


# ----------------------------------------------------------------------
# Main process
# ----------------------------------------------------------------------

def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        sys.exit(
            "ERROR: GEMINI_API_KEY environment variable is not set."
        )

    if not os.path.exists(INPUT_FILE):
        sys.exit(
            f"ERROR: {INPUT_FILE} was not found. "
            "Run the capture step first."
        )

    if not os.path.isdir(SCREENSHOTS_DIR):
        sys.exit(
            f"ERROR: {SCREENSHOTS_DIR} directory was not found. "
            "Run the capture step first."
        )

    with open(INPUT_FILE, "r", encoding="utf-8") as captured_file:
        captured = json.load(captured_file)

    if not isinstance(captured, list):
        sys.exit(
            f"ERROR: {INPUT_FILE} must contain a JSON array."
        )

    observations = []

    for item in captured:
        source = item.get(
            "source",
            "unknown_source",
        )

        content = (
            item.get("content") or ""
        ).strip()

        screenshot_path = get_screenshot_path(source)

        if os.path.exists(screenshot_path):
            screenshot_size = os.path.getsize(screenshot_path)
            screenshot_details = (
                f"{screenshot_size:,} bytes"
            )
        else:
            screenshot_details = "missing"

        print(
            f"Analysing: {source} "
            f"({len(content):,} text chars, "
            f"screenshot: {screenshot_details})"
        )

        observation = analyse_one(
            api_key=api_key,
            source=source,
            content=content,
            screenshot_path=screenshot_path,
        )

        observations.append(observation)

        print(
            f"  {source}: "
            f"{observation['capture_status']} | "
            f"{observation['primary_offer']}"
        )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as observations_file:
        json.dump(
            observations,
            observations_file,
            indent=2,
            ensure_ascii=False,
        )

    valid_count = sum(
        1
        for observation in observations
        if observation.get("capture_status") == "valid"
    )

    print(
        f"\nDone. Wrote {len(observations)} observations "
        f"to {OUTPUT_FILE}."
    )

    print(
        f"Valid captures: {valid_count}; "
        f"failed or blocked captures: "
        f"{len(observations) - valid_count}."
    )


if __name__ == "__main__":
    main()
