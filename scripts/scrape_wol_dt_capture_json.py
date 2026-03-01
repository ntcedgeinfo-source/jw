import os
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

BASE = "https://wol.jw.org/wol/dt/r101/lp-cv/{year}/{month}/{day}"

YEAR = os.getenv("YEAR", "2026").strip()
MONTH = os.getenv("MONTH", "3").strip()
DAY = os.getenv("DAY", "1").strip()

OUT_DIR = os.getenv("OUT_DIR", "data")
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "90000"))

os.makedirs(OUT_DIR, exist_ok=True)

def safe_int_str(x: str) -> str:
    return str(int(x))

def normalize_ws(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    txt = soup.get_text("\n", strip=True)
    return normalize_ws(txt)

def extract_daily_item(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    From payload like {"items":[...]} pick the item that looks like the Daily Text entry.
    Usually it contains a header with a date like "Dominggo, Marso 1" and a themeScrp paragraph.
    """
    items = payload.get("items") or []
    if not isinstance(items, list):
        return None

    best = None
    for it in items:
        content = it.get("content") or ""
        classes = it.get("articleClasses") or ""
        # Heuristic: "today" + themeScrp or bodyTxt often indicates the daily text
        score = 0
        if "today" in classes:
            score += 2
        if "themeScrp" in content:
            score += 3
        if "bodyTxt" in content:
            score += 2
        if it.get("publicationTitle", "").startswith("Pagsusi sa Kasulatan"):
            score += 3

        if best is None or score > best[0]:
            best = (score, it)

    return best[1] if best else None

def main():
    year = safe_int_str(YEAR)
    month = safe_int_str(MONTH)
    day = safe_int_str(DAY)
    url = BASE.format(year=year, month=month, day=day)
    stamp = f"{year}-{int(month):02d}-{int(day):02d}"

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    captured_json: Optional[Dict[str, Any]] = None
    captured_from: Optional[str] = None

    def maybe_capture_json(response):
        nonlocal captured_json, captured_from
        if captured_json is not None:
            return
        try:
            ct = (response.headers.get("content-type") or "").lower()
            # Some servers send "application/json" or "application/json; charset=utf-8"
            if "json" not in ct:
                return
            data = response.json()
            if isinstance(data, dict) and "items" in data and isinstance(data.get("items"), list):
                captured_json = data
                captured_from = response.url
        except Exception:
            # ignore non-json or parsing errors
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.on("response", maybe_capture_json)

        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

        # wait a bit to allow XHR to complete
        page.wait_for_timeout(5000)

        # If still not captured, try waiting for network idle then another pause
        if captured_json is None:
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(3000)

        title = page.title()
        rendered_html = page.content()

        browser.close()

    # Save rendered HTML (debug)
    html_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    if captured_json is None:
        # Fail hard so Actions shows it clearly (and you can inspect the HTML artifact)
        raise RuntimeError(
            "Did not capture JSON payload with an 'items' array. "
            "Check saved HTML artifact and/or increase timeouts."
        )

    # Save raw captured JSON
    raw_json_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.raw.json")
    with open(raw_json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source_url": url,
                "captured_from": captured_from,
                "fetched_at_utc": fetched_at,
                "page_title": title,
                "payload": captured_json,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Parse out the daily text entry into a friendlier JSON
    daily = extract_daily_item(captured_json)
    parsed = {
        "source_url": url,
        "captured_from": captured_from,
        "fetched_at_utc": fetched_at,
        "page_title": title,
        "date": stamp,
        "daily": None,
    }

    if daily:
        parsed["daily"] = {
            "did": daily.get("did"),
            "title": daily.get("title"),
            "reference": daily.get("reference"),
            "publicationTitle": daily.get("publicationTitle"),
            "url": daily.get("url"),
            "imageUrl": daily.get("imageUrl"),
            "caption": daily.get("caption"),
            "content_html": daily.get("content"),
            "content_text": html_to_text(daily.get("content") or ""),
        }

    parsed_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.parsed.json")
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    # Also save just the plain text for quick reading
    txt_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        if parsed["daily"] and parsed["daily"]["content_text"]:
            f.write(parsed["daily"]["content_text"] + "\n")
        else:
            f.write("(No daily content parsed)\n")

    print("URL:", url)
    print("Saved:", html_path)
    print("Saved:", raw_json_path)
    print("Saved:", parsed_path)
    print("Saved:", txt_path)

if __name__ == "__main__":
    main()
