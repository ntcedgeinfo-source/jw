import os
import time
import random
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = os.getenv("TARGET_URL", "https://wol.jw.org/ceb/wol/h/r101/lp-cv")
OUT_DIR = os.getenv("OUT_DIR", "data")
TIMEOUT = int(os.getenv("TIMEOUT", "30"))
RETRIES = int(os.getenv("RETRIES", "5"))

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

def fetch(url: str) -> str:
    last_err = None
    for i in range(RETRIES):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            # exponential backoff + jitter
            time.sleep((2 ** i) + random.random())
    raise RuntimeError(f"Failed to fetch after {RETRIES} retries: {last_err}")

def main():
    html = fetch(URL)

    # Save raw HTML for inspection/debug
    html_path = os.path.join(OUT_DIR, "wol_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    soup = BeautifulSoup(html, "html.parser")

    # Extract all links under Cebuano WOL
    links = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(URL, href)
        if full.startswith("https://wol.jw.org/ceb/wol/"):
            text = " ".join(a.get_text(" ", strip=True).split())
            if text:  # skip empty anchor text
                links.append({"text": text, "url": full})

    # de-dup by url while preserving order
    seen = set()
    unique = []
    for item in links:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)

    out_json = {
        "source_url": URL,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(unique),
        "links": unique,
    }

    json_path = os.path.join(OUT_DIR, "wol_links.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)

    # Optional: also write a TSV
    tsv_path = os.path.join(OUT_DIR, "wol_links.tsv")
    with open(tsv_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(f"{item['text']}\t{item['url']}\n")

    print(f"Saved: {html_path}")
    print(f"Saved: {json_path}")
    print(f"Saved: {tsv_path}")
    print(f"Extracted links: {len(unique)}")

if __name__ == "__main__":
    main()
