import os
import json
import time
import random
from datetime import datetime, timezone

import requests

BASE = "https://wol.jw.org/wol/dt/r101/lp-cv/{year}/{month}/{day}"

YEAR = os.getenv("YEAR", "2026")
MONTH = os.getenv("MONTH", "3")
DAY = os.getenv("DAY", "1")

OUT_DIR = os.getenv("OUT_DIR", "data")
TIMEOUT = int(os.getenv("TIMEOUT", "45"))
RETRIES = int(os.getenv("RETRIES", "5"))

os.makedirs(OUT_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
})

def fetch_json(url):
    last_err = None
    for i in range(RETRIES):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep((2 ** i) + random.random())
    raise RuntimeError(f"Failed after {RETRIES} retries: {last_err}")

def main():
    url = BASE.format(year=int(YEAR), month=int(MONTH), day=int(DAY))
    stamp = f"{int(YEAR):04d}-{int(MONTH):02d}-{int(DAY):02d}"
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = fetch_json(url)

    # Save raw JSON
    raw_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Extract first item (Daily Text)
    daily = payload.get("items", [None])[0]

    parsed = {
        "date": stamp,
        "source_url": url,
        "fetched_at_utc": fetched_at,
        "daily": daily,
    }

    parsed_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.parsed.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print("Scraped:", url)
    print("Saved:", raw_path)
    print("Saved:", parsed_path)

if __name__ == "__main__":
    main()
