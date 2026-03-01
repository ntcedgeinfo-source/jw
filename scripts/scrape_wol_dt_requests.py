import os
import json
import time
import random
from datetime import datetime, timezone

import requests

BASE = "https://wol.jw.org/wol/dt/r101/lp-cv/{year}/{month}/{day}"

YEAR = int(os.getenv("YEAR", "2026"))
MONTH = int(os.getenv("MONTH", "3"))
DAY = int(os.getenv("DAY", "1"))

OUT_DIR = os.getenv("OUT_DIR", "data")
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "15"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "120"))
TRIES = int(os.getenv("TRIES", "8"))

os.makedirs(OUT_DIR, exist_ok=True)

def jitter_sleep(mult=1.0):
    time.sleep(mult * random.uniform(1.0, 3.0))

def load_cache(cache_path: str) -> dict:
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(cache_path: str, data: dict) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    url = BASE.format(year=YEAR, month=MONTH, day=DAY)
    stamp = f"{YEAR:04d}-{MONTH:02d}-{DAY:02d}"

    raw_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.json")
    cache_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.cache.json")  # stores etag/last-modified

    cache = load_cache(cache_path)
    etag = cache.get("etag")
    last_modified = cache.get("last_modified")

    headers = {
        # Mimic the browser XHR you showed
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://wol.jw.org/ceb/wol/h/r101/lp-cv",
    }

    # Conditional request -> enables 304 Not Modified
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    session = requests.Session()

    last_err = None
    resp = None

    for attempt in range(1, TRIES + 1):
        try:
            jitter_sleep(1.0 if attempt == 1 else min(5.0, attempt))

            resp = session.get(url, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))

            # 304 => keep existing JSON file (fast path)
            if resp.status_code == 304:
                if os.path.exists(raw_path):
                    print("304 Not Modified - keeping existing file:", raw_path)
                    return
                # If no existing file, we must refetch without conditional headers
                headers.pop("If-None-Match", None)
                headers.pop("If-Modified-Since", None)
                continue

            # transient errors / bot edge cases
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                continue

            resp.raise_for_status()

            payload = resp.json()

            # Save JSON
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            # Update cache headers for next run
            new_etag = resp.headers.get("ETag")
            new_last_modified = resp.headers.get("Last-Modified")
            cache_update = {
                "url": url,
                "etag": new_etag,
                "last_modified": new_last_modified,
                "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            save_cache(cache_path, cache_update)

            print("200 OK - saved:", raw_path)
            print("Cache:", cache_update)
            return

        except Exception as e:
            last_err = e

    raise RuntimeError(f"Failed after {TRIES} tries: {last_err}")

if __name__ == "__main__":
    main()
