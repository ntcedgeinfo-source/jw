import os
import json
import time
import random
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import re
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
tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def telegram_send_message(text: str, token: str, chat_id: str) -> None:
    """
    Sends a message to a Telegram chat.
    Telegram message limit is ~4096 chars; we auto-split safely.
    """
    api = f"https://api.telegram.org/bot{token}/sendMessage"

    # Telegram limit: 4096 characters per message
    chunks = []
    while text:
        chunk = text[:4000]
        text = text[4000:]
        chunks.append(chunk)

    for i, chunk in enumerate(chunks):
        resp = requests.post(
            api,
            data={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=(15, 60),
        )
        resp.raise_for_status()
        # small delay between chunks to be safe
        if i < len(chunks) - 1:
            time.sleep(0.8)

def format_human_readable(content_html: str) -> str:
    soup = BeautifulSoup(content_html, "html.parser")

    # Header (date)
    header = soup.find("h2")
    header_text = header.get_text(strip=True) if header else ""

    # Theme scripture line
    theme = soup.find("p", class_="themeScrp")
    theme_text = theme.get_text(" ", strip=True) if theme else ""

    # Body paragraph
    body_div = soup.find("div", class_="bodyTxt")
    body_text = ""
    if body_div:
        body_text = body_div.get_text(" ", strip=True)

    # Clean excessive spaces
    body_text = re.sub(r"\s+", " ", body_text).strip()

    # Format log output
    output = []
    output.append("=" * 60)
    output.append(f"DATE: {header_text}")
    output.append("-" * 60)
    output.append("THEME SCRIPTURE:")
    output.append(theme_text)
    output.append("")
    output.append("MESSAGE:")
    output.append(body_text)
    output.append("=" * 60)

    return "\n".join(output)

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

            daily = (payload.get("items") or [None])[0]

            if daily and daily.get("content"):
                readable = format_human_readable(daily["content"])
            
                log_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.log")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(readable)
            
                print("Saved human-readable log:", log_path)
                if tg_token and tg_chat_id:
                    # optional: prefix a short header
                    message = f"WOL Daily Text ({stamp})\n\n{readable}"
                    telegram_send_message(message, tg_token, tg_chat_id)
                    print("Sent to Telegram.")
                else:
                    print("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

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
