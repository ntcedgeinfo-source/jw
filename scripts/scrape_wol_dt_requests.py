# scripts/scrape_wol_dt_requests.py
import os
import json
import time
import random
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape

import requests


# -----------------------------
# Config
# -----------------------------
BASE = "https://wol.jw.org/wol/dt/r101/lp-cv/{year}/{month}/{day}"

YEAR = int(os.getenv("YEAR", "2026"))
MONTH = int(os.getenv("MONTH", "3"))
DAY = int(os.getenv("DAY", "1"))

OUT_DIR = os.getenv("OUT_DIR", "data")

CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "15"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "120"))

# WOL fetch retries
WOL_TRIES = int(os.getenv("WOL_TRIES", "8"))

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SEND_TELEGRAM = os.getenv("SEND_TELEGRAM", "1").strip() == "1"

os.makedirs(OUT_DIR, exist_ok=True)


# -----------------------------
# Helpers
# -----------------------------
def jitter_sleep(mult=1.0):
    time.sleep(mult * random.uniform(1.0, 3.0))


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        if tag in ("p", "div", "header", "h2", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "div", "header", "h2"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = TextExtractor()
    p.feed(html)
    text = unescape("".join(p.parts))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_human_readable(content_html: str) -> str:
    # Extract date from <h2>...</h2>
    m = re.search(r"<h2[^>]*>(.*?)</h2>", content_html, flags=re.IGNORECASE | re.DOTALL)
    header_text = html_to_text(m.group(0)) if m else ""

    # Extract theme scripture paragraph class="themeScrp"
    m = re.search(
        r'<p[^>]*class="[^"]*\bthemeScrp\b[^"]*"[^>]*>.*?</p>',
        content_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    theme_text = html_to_text(m.group(0)) if m else ""

    # Extract body text inside <div class="bodyTxt">...</div>
    m = re.search(
        r'<div[^>]*class="[^"]*\bbodyTxt\b[^"]*"[^>]*>(.*?)</div>',
        content_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body_text = html_to_text(m.group(1)) if m else ""
    body_text = re.sub(r"\s+", " ", body_text).strip()

    return "\n".join(
        [
            "=" * 60,
            f"DATE: {header_text}",
            "-" * 60,
            "THEME SCRIPTURE:",
            theme_text,
            "",
            "MESSAGE:",
            body_text,
            "=" * 60,
        ]
    )


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


def telegram_send_message(text: str, token: str, chat_id: str) -> None:
    """
    Send message to Telegram. Splits into chunks to avoid Telegram length limit.
    Raises RuntimeError with Telegram error body on failure.
    """
    api = f"https://api.telegram.org/bot{token}/sendMessage"

    def chunks(s: str, n: int = 3500):
        for i in range(0, len(s), n):
            yield s[i : i + n]

    for part in chunks(text):
        resp = requests.post(
            api,
            data={
                "chat_id": chat_id,  # numeric id or @channelusername
                "text": part,
                "disable_web_page_preview": True,
            },
            timeout=(15, 60),
        )

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"raw": resp.text}
            raise RuntimeError(f"Telegram error {resp.status_code}: {err}")

        time.sleep(0.7)


# -----------------------------
# Main
# -----------------------------
def main():
    url = BASE.format(year=YEAR, month=MONTH, day=DAY)
    stamp = f"{YEAR:04d}-{MONTH:02d}-{DAY:02d}"

    raw_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.json")
    cache_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.cache.json")
    log_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.log")

    cache = load_cache(cache_path)
    etag = cache.get("etag")
    last_modified = cache.get("last_modified")

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://wol.jw.org/ceb/wol/h/r101/lp-cv",
    }

    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    session = requests.Session()

    payload = None
    resp_headers = None

    # ---- Only WOL fetch is retried ----
    last_err = None
    for attempt in range(1, WOL_TRIES + 1):
        try:
            jitter_sleep(1.0 if attempt == 1 else min(5.0, attempt))

            resp = session.get(
                url,
                headers=headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )

            if resp.status_code == 304:
                if os.path.exists(raw_path):
                    print("304 Not Modified - keeping existing JSON:", raw_path)
                    # Still create/update log from existing JSON if possible
                    with open(raw_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    resp_headers = {"ETag": etag, "Last-Modified": last_modified}
                    break

                headers.pop("If-None-Match", None)
                headers.pop("If-Modified-Since", None)
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                continue

            resp.raise_for_status()
            payload = resp.json()
            resp_headers = resp.headers
            break

        except Exception as e:
            last_err = e

    if payload is None:
        raise RuntimeError(f"WOL fetch failed after {WOL_TRIES} tries: {last_err}")

    # Save JSON
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("Saved JSON:", raw_path)

    # Update cache
    new_etag = (resp_headers or {}).get("ETag")
    new_last_modified = (resp_headers or {}).get("Last-Modified")
    cache_update = {
        "url": url,
        "etag": new_etag or etag,
        "last_modified": new_last_modified or last_modified,
        "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_cache(cache_path, cache_update)
    print("Saved cache:", cache_path)

    # Build human-readable log
    daily = (payload.get("items") or [None])[0]
    if daily and daily.get("content"):
        readable = format_human_readable(daily["content"])
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(readable + "\n")
        print("Saved human-readable log:", log_path)
    else:
        readable = f"WOL Daily Text ({stamp})\n\n(No 'content' field found)"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(readable + "\n")
        print("Saved human-readable log (fallback):", log_path)

    # ---- Telegram send (NO retries on 400) ----
    if SEND_TELEGRAM and TG_TOKEN and TG_CHAT_ID:
        msg = f"WOL Daily Text ({stamp})\n\n{readable}"
        telegram_send_message(msg, TG_TOKEN, TG_CHAT_ID)
        print("Sent to Telegram.")
    else:
        print("Telegram not configured (or SEND_TELEGRAM=0).")

if __name__ == "__main__":
    main()
