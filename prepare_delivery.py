#!/usr/bin/env python3

import base64
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo


OBSERVATIONS_FILE = "observations.json"
SCREENSHOTS_DIR = "screenshots"
OUTPUT_FILE = "delivery.json"


def encode_file(path: str) -> str:
    with open(path, "rb") as file:
        return base64.b64encode(file.read()).decode("ascii")


def main() -> None:
    with open(OBSERVATIONS_FILE, "r", encoding="utf-8") as file:
        observations = json.load(file)

    captures = []

    for file_name in sorted(os.listdir(SCREENSHOTS_DIR)):
        if not file_name.lower().endswith(".png"):
            continue

        file_path = os.path.join(SCREENSHOTS_DIR, file_name)

        captures.append(
            {
                "source": os.path.splitext(file_name)[0],
                "fileName": file_name,
                "contentType": "image/png",
                "contentBase64": encode_file(file_path),
            }
        )

    run_date = datetime.now(
        ZoneInfo("Pacific/Auckland")
    ).isoformat(timespec="seconds")

    delivery = {
        "runDate": run_date,
        "observations": observations,
        "captures": captures,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(delivery, file, ensure_ascii=False)

    print(
        f"Created {OUTPUT_FILE} with "
        f"{len(observations)} observations and "
        f"{len(captures)} captures"
    )


if __name__ == "__main__":
    main()
