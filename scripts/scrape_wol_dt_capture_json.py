import os
import re
import json
import time
import random
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

BASE = "https://wol.jw.org/wol/dt/r101/lp-cv/{year}/{month}/{day}"

YEAR = os.getenv("YEAR", "2026").strip()
MONTH = os.getenv("MONTH", "3").strip()
DAY = os.getenv("DAY", "1").strip()

OUT_DIR = os.getenv("OUT_DIR", "data")
TIMEOUT = int(os.getenv("TIMEOUT", "45"))
RETRIES = int(os.getenv("RETRIES", "8"))

os.makedirs(OUT_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ceb,en-US;q=0.8,en;q=0.6",
})

def safe_int_str(x: str) -> str:
    return str(int(x))

def fetch(url: str) -> requests.Response:
    last_err = None
    for i in range(RETRIES):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            time.sleep(min(60, (2 ** i)) + random.random())
    raise RuntimeError(f"Failed after {RETRIES} retries: {last_err}")

def find_json_url(html: str, base_url: str) -> str:
    """
    Find the JSON endpoint that returns {"items":[...]}.
    We try multiple patterns because WOL can change how they embed the data URL.
    """
    patterns = [
        # common: a JSON URL embedded in scripts
        r'(\/wol\/.*?\.json[^"\'<\s]*)',
        r'(\/wol\/.*?\/json[^"\'<\s]*)',
        r'(\/wol\/.*?\/data[^"\'<\s]*)',
        r'(\/wol\/.*?\/api[^"\'<\s]*)',
        # sometimes full URL
        r'(https:\/\/wol\.jw\.org\/wol\/.*?(?:json|data|api)[^"\'<\s]*)',
    ]

    candidates = []
    for pat in patterns:
        for m in re.finditer(pat, html, flags=re.IGNORECASE):
            candidates.append(m.group(1))

    # Try candidates until one returns dict with "items"
    for c in candidates:
        u = c if c.startswith("http") else urljoin(base_url, c)
        try:
            r = fetch(u)
            if "json" in (r.headers.get("content-type") or "").lower():
                data = r.json()
                if isinstance(data, dict) and isinstance(data.get("items"), list):
                    return u
        except Exception:
            continue

    raise RuntimeError(
        "Could not locate the JSON endpoint from the HTML. "
        "Save the HTML and search for 'items' / 'json' / 'api' to update patterns."
    )

def main():
    year = safe_int_str(YEAR)
    month = safe_int_str(MONTH)
    day = safe_int_str(DAY)

    url = BASE.format(year=year, month=month, day=day)
    stamp = f"{year}-{int(month):02d}-{int(day):02d}"
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1) Fetch the DT page HTML
    r = fetch(url)
    html = r.text

    html_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 2) Find the JSON endpoint URL inside the HTML
    json_url = find_json_url(html, url)

    # 3) Fetch JSON payload
    jr = fetch(json_url)
    payload = jr.json()

    raw_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source_url": url,
                "json_url": json_url,
                "fetched_at_utc": fetched_at,
                "payload": payload,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("DT URL:", url)
    print("JSON URL:", json_url)
    print("Saved HTML:", html_path)
    print("Saved JSON:", raw_path)

if __name__ == "__main__":
    main()
