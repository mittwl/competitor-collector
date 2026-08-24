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
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{MODEL}:generateContent"
)

INPUT_FILE = "captured.json"
OUTPUT_FILE = "observations.json"
SCREENSHOTS_DIR = "screenshots"

# Gemini inline requests have a total size limit. Base64 increases image
# size by roughly one third, so this keeps the request comfortably below it.
MAX_SCREENSHOT_BYTES = 12_000_000

# Keep the output focused rather than turning it into a page inventory.
MAX_SECONDARY_CAMPAIGNS = 3


# ----------------------------------------------------------------------
# Analysis prompt
# ----------------------------------------------------------------------

INSTRUCTION = """
You are analysing one competitor retail homepage using:

1. A full-page screenshot showing the page as it appeared during capture.
2. Extracted visible text from the same page.

Use the screenshot as the authority for visual hierarchy, prominence,
visibility and the active carousel state.

Use the extracted text only to confirm exact wording, discounts, category
names and offer mechanics that are supported by the screenshot.

PROMOTIONAL HIERARCHY

Evaluate page content in this order:

1. The active hero banner or first major campaign visible in the screenshot.
2. Large visible promotional modules near the top of the page.
3. Repeated category promotions in the main page content.
4. Lower-page secondary campaign banners or tiles.
5. Navigation, delivery messages, payment options, newsletter incentives and
   footer content.

The screenshot overrides the extracted text when determining whether a
campaign is visible.

Extracted text may contain:

- hidden carousel slides
- inactive carousel content
- accessibility text
- off-screen promotional copy
- navigation labels
- product carousel labels
- routine website functions

Never include that material as an active or secondary campaign unless the
corresponding promotion is clearly visible in the screenshot.

Do not treat navigation labels, inactive carousel slides, delivery thresholds,
payment options, account messages, newsletter incentives or footer links as
the dominant campaign.

Only mention a seasonal event such as Father's Day as a secondary campaign
when a substantial Father's Day banner, tile or promotional module is clearly
visible in the screenshot.

A navigation link or hidden carousel message alone is not evidence of an
active seasonal campaign.

If several promotional messages are visible, identify one dominant campaign
based on:

- visual size
- vertical position
- prominence
- repetition
- strength of promotional treatment

Record only the most meaningful visible activity as secondary campaigns.

FIELD DEFINITIONS

- "source": use the supplied source identifier exactly.

- "primary_offer": the dominant visible campaign proposition or promotional
  offer.

  Prioritise the active hero and major upper-page modules.

  If the hero is a lifestyle proposition without a price discount, report the
  lifestyle proposition rather than substituting a delivery threshold,
  payment option or newsletter sign-up offer.

- "category_focus": the principal product category or categories represented
  by the dominant visible campaign.

- "hero_theme": a short description of the active hero campaign.

- "hook": the main creative or tactical approach, such as "price-led",
  "lifestyle", "new arrivals", "urgency", "clearance" or a concise combination.

- "secondary_campaigns": include no more than three meaningful secondary
  advertising campaigns that are clearly visible in the screenshot.

  A secondary campaign must have substantial visual treatment, such as:

  - a large promotional banner
  - a campaign tile
  - a clearly grouped promotional section
  - a repeated, visually prominent offer mechanic

  Do not include:

  - navigation links
  - category tabs
  - product carousel labels
  - individual product cards
  - inactive or hidden carousel slides
  - text found only in the extracted page content
  - routine merchandising headings such as "New In", "Top Brands",
    "Latest Arrivals" or "Hot Deals This Week"
  - generic category names without a meaningful campaign proposition

  Consolidate closely related activity into one secondary campaign rather
  than listing each individual category or product offer separately.

  If no meaningful secondary campaign is clearly visible, return an empty
  array.

- "utility_messages": include only persistent commercial service or conversion
  messages that could influence shopping behaviour, such as:

  - free delivery thresholds
  - Afterpay or another payment option
  - Click & Collect
  - quantified loyalty benefits
  - quantified newsletter sign-up incentives
  - returns or price guarantees

  Exclude routine website functions such as:

  - Contact Us
  - Store Locator
  - search
  - account access
  - privacy links
  - terms and conditions
  - footer navigation
  - social media links

- "supporting_evidence": write one concise sentence explaining which visible
  page elements support the primary_offer decision.

- "notable": write one concise factual sentence covering the dominant visible
  campaign and, at most, one or two clearly visible secondary themes.

  Do not mention a campaign in notable unless that campaign also appears in
  secondary_campaigns.

  Do not list routine categories, navigation entries, utility messages or
  hidden carousel content.

  Do not infer commercial strategy, business intent or performance beyond the
  supplied evidence.

- "capture_status": use "valid" when the screenshot shows a genuine retail
  homepage.

  Use "blocked_or_error" when the screenshot shows:

  - an error page
  - a bot challenge
  - a blank loading state
  - an incomplete shell
  - unrelated fallback content
  - a page that is too incomplete to assess reliably

If capture_status is "blocked_or_error":

- Set primary_offer to "Capture unavailable".
- Set category_focus, hero_theme and hook to "N/A".
- Use empty arrays for secondary_campaigns and utility_messages.
- Briefly explain the visible issue in supporting_evidence and notable.

Return only the structured JSON response.

Do not include markdown, commentary, code fences or a preamble.
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


def clean_string_list(values) -> list:
    """
    Remove blank and duplicate values while preserving the original order.
    """

    if not isinstance(values, list):
        return []

    cleaned = []
    seen = set()

    for value in values:
        if not isinstance(value, str):
            continue

        value = value.strip()

        if not value:
            continue

        key = value.casefold()

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(value)

    return cleaned


def normalise_observation(
    result: dict,
    source: str,
) -> dict:
    """
    Ensure Gemini's response has the expected fields and enforce output limits.
    """

    if not isinstance(result, dict):
        return {
            "source": source,
            "primary_offer": "Analysis failed",
            "category_focus": "N/A",
            "hero_theme": "N/A",
            "hook": "N/A",
            "secondary_campaigns": [],
            "utility_messages": [],
            "supporting_evidence": (
                "Gemini returned an invalid response structure."
            ),
            "notable": (
                "Gemini returned an invalid response structure."
            ),
            "capture_status": "blocked_or_error",
        }

    capture_status = result.get(
        "capture_status",
        "blocked_or_error",
    )

    if capture_status not in (
        "valid",
        "blocked_or_error",
    ):
        capture_status = "blocked_or_error"

    secondary_campaigns = clean_string_list(
        result.get("secondary_campaigns", [])
    )[:MAX_SECONDARY_CAMPAIGNS]

    utility_messages = clean_string_list(
        result.get("utility_messages", [])
    )

    normalised = {
        "source": source,
        "primary_offer": str(
            result.get(
                "primary_offer",
                "Analysis unavailable",
            )
        ).strip(),
        "category_focus": str(
            result.get(
                "category_focus",
                "N/A",
            )
        ).strip(),
        "hero_theme": str(
            result.get(
                "hero_theme",
                "N/A",
            )
        ).strip(),
        "hook": str(
            result.get(
                "hook",
                "N/A",
            )
        ).strip(),
        "secondary_campaigns": secondary_campaigns,
        "utility_messages": utility_messages,
        "supporting_evidence": str(
            result.get(
                "supporting_evidence",
                "No supporting evidence was returned.",
            )
        ).strip(),
        "notable": str(
            result.get(
                "notable",
                "No observation was returned.",
            )
        ).strip(),
        "capture_status": capture_status,
    }

    if capture_status == "blocked_or_error":
        normalised["primary_offer"] = "Capture unavailable"
        normalised["category_focus"] = "N/A"
        normalised["hero_theme"] = "N/A"
        normalised["hook"] = "N/A"
        normalised["secondary_campaigns"] = []
        normalised["utility_messages"] = []

    return normalised


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
            # Low temperature supports comparable weekly classifications.
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

                return normalise_observation(
                    result=result,
                    source=source,
                )

            last_error = response.text[:500]

            if response.status_code in (
                429,
                500,
                502,
                503,
                504,
            ):
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

        except (
            requests.RequestException,
            KeyError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            last_error = str(error)

            if attempt < 2:
                wait_seconds = 2 * (attempt + 1)

                print(
                    f"  {source}: Gemini response failed; retrying in "
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

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8",
    ) as captured_file:
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
            screenshot_size = os.path.getsize(
                screenshot_path
            )

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
            f"{observation['primary_offer']} | "
            f"{len(observation['secondary_campaigns'])} "
            f"secondary campaigns"
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
