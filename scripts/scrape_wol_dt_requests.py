# scripts/scrape_wol_dt_requests.py

import os
import json
import time
import random
import re
import smtplib
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
from email.message import EmailMessage
from pathlib import Path

import requests

from cloudflare_image import generate_image_cloudflare


# -----------------------------
# Cloudflare Models
# -----------------------------
CLOUDFLARE_TEXT_MODEL = os.getenv(
    "CLOUDFLARE_TEXT_MODEL",
    "@cf/meta/llama-4-scout-17b-16e-instruct"
).strip()


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

WOL_TRIES = int(os.getenv("WOL_TRIES", "8"))

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SEND_TELEGRAM = os.getenv("SEND_TELEGRAM", "1").strip() == "1"

# Telegram Markdown / .md export
# Use MarkdownV2 for rendered Telegram messages. Set empty string to send plain text.
TELEGRAM_PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE", "MarkdownV2").strip()
SEND_MARKDOWN_FILE = os.getenv("SEND_MARKDOWN_FILE", "1").strip() == "1"

# Markdown Agent Memory
# This lets the script check previous wol_dt_*.md files before writing today's explainer.
# The previous files are used only as style/format examples, not as a source of doctrine.
AGENT_MEMORY_ENABLED = os.getenv("AGENT_MEMORY_ENABLED", "1").strip() == "1"
AGENT_MEMORY_LIMIT = int(os.getenv("AGENT_MEMORY_LIMIT", "3"))
AGENT_MEMORY_MAX_CHARS = int(os.getenv("AGENT_MEMORY_MAX_CHARS", "6000"))
AGENT_MEMORY_PATTERN = os.getenv("AGENT_MEMORY_PATTERN", "wol_dt_*.md").strip()

# Email-to-Blogger
SEND_EMAIL = os.getenv("SEND_EMAIL", "0").strip() == "1"
BLOGGER_POST_EMAIL = os.getenv("BLOGGER_POST_EMAIL", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()

os.makedirs(OUT_DIR, exist_ok=True)


# -----------------------------
# Markdown Agent Memory Helpers
# -----------------------------
def read_text_file_safe(path: Path, max_chars: int = 4000) -> str:
    """Read a text file safely and limit the size sent into the AI prompt."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""

    if not text:
        return ""

    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n...[trimmed]"

    return text


def load_previous_markdown_memory(
    out_dir: str,
    current_stamp: str,
    limit: int = AGENT_MEMORY_LIMIT,
    max_chars: int = AGENT_MEMORY_MAX_CHARS,
    pattern: str = AGENT_MEMORY_PATTERN,
) -> str:
    """
    Load previous wol_dt_*.md files as lightweight agent memory.

    This works like a simple Hermes-style memory:
    - check older .md files
    - use them as tone/format examples
    - do not copy old content
    - do not use old files as doctrine/source material
    """
    if not AGENT_MEMORY_ENABLED:
        return ""

    base_dir = Path(out_dir)
    if not base_dir.exists():
        return ""

    current_name = f"wol_dt_{current_stamp}.md"
    files = [
        p for p in base_dir.glob(pattern)
        if p.is_file() and p.name != current_name
    ]

    # File names include ISO dates, so reverse sort usually gives latest first.
    files = sorted(files, key=lambda p: p.name, reverse=True)[: max(0, limit)]

    if not files:
        return ""

    per_file_chars = max(800, max_chars // max(1, len(files)))
    blocks = []

    for path in files:
        text = read_text_file_safe(path, max_chars=per_file_chars)
        if not text:
            continue

        blocks.append(
            "\n".join(
                [
                    f"### Previous Markdown Example: {path.name}",
                    text,
                ]
            )
        )

    memory = "\n\n---\n\n".join(blocks).strip()

    if len(memory) > max_chars:
        memory = memory[:max_chars].rstrip() + "\n...[agent memory trimmed]"

    return memory


def build_agent_memory_instruction(memory_context: str) -> str:
    """Build the instruction block added to the AI prompt."""
    if not memory_context:
        return ""

    return f"""
Previous Markdown Memory:
{memory_context}

How to use the previous Markdown memory:
- Use it only as a style and formatting guide.
- Keep the same warm, simple Cebuano tone.
- Keep the same short Telegram-friendly structure.
- Do not copy previous explanations.
- Do not reuse previous illustrations unless the idea naturally fits.
- Do not use previous files as a doctrinal source.
- Today's original WOL message is still the only source for today's meaning.
"""


# -----------------------------
# Cloudflare AI Text Explainer
# -----------------------------
def run_cloudflare_text_ai(prompt: str, model: str = CLOUDFLARE_TEXT_MODEL) -> str:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    if not account_id:
        raise ValueError("Missing CLOUDFLARE_ACCOUNT_ID")
    if not api_token:
        raise ValueError("Missing CLOUDFLARE_API_TOKEN")

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Cebuano daily text explainer. "
                    "Explain the message in simple, respectful Cebuano. "
                    "Do not add new doctrine. Do not over-explain. "
                    "Keep it short, warm, and practical."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=180,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(f"Cloudflare text AI error: {data}")

    result = data.get("result", {})

    explanation = (
        result.get("response")
        or result.get("text")
        or result.get("answer")
        or result.get("output")
    )

    if not explanation:
        raise RuntimeError(f"No AI explanation returned: {data}")

    return explanation.strip()


def generate_daily_explainer(parts: dict, memory_context: str = "") -> str:
    header = parts.get("header_text", "")
    theme = parts.get("theme_text", "")
    body = parts.get("body_text", "")
    agent_memory_instruction = build_agent_memory_instruction(memory_context)

    prompt = f"""
Daily Text Date:
{header}

Theme Scripture:
{theme}

Original Message:
{body}

{agent_memory_instruction}
Create a Cebuano AI explainer for Telegram.

Very important rules:
- Do not copy the original paragraph.
- Do not quote long parts from the message.
- Use your own words only.
- Keep the meaning faithful to the original message.
- Do not add new doctrine, new interpretation, or personal opinion.
- Use simple Cebuano.
- Keep it warm, clear, and practical.
- Make it easy for ordinary readers to understand.
- Use short sentences.
- Make the illustration realistic and easy to imagine.
- The illustration should help the reader see the lesson in daily life.
- Avoid dramatic, fictional, or emotional exaggeration.

Use this exact format:

AI Explainer:

📌 Pangunang Punto:
[Write 1 short sentence that summarizes the main lesson.]

💡 Sayon nga Pasabot:
[Explain the message in 2 short sentences using your own words.]

🖼️ Imahen sa Sitwasyon:
[Give 2 to 3 short sentences as a simple life illustration.]
Start with: "Hunahunaa ang..."

✅ Aplikasyon Karon:
[Give 1 practical thing the reader can do today.]

🤔 Pangutana sa Kaugalingon:
[Write 1 simple reflection question.]

🌱 Mubo nga Hinumdoman:
[Write 1 short takeaway sentence.]
"""

    return run_cloudflare_text_ai(prompt)


# -----------------------------
# Telegram Helpers
# -----------------------------
def telegram_markdown_v2_escape(text: str) -> str:
    """Escape text for Telegram MarkdownV2 parse mode."""
    if text is None:
        return ""

    # Telegram MarkdownV2 reserved characters:
    # _ * [ ] ( ) ~ ` > # + - = | { } . ! and backslash
    return re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", str(text))


def telegram_trim(text: str, limit: int) -> str:
    """Trim text safely before sending to Telegram."""
    if len(text) <= limit:
        return text

    trimmed = text[: max(0, limit - 1)].rstrip()

    # Avoid ending a MarkdownV2 message with a dangling escape slash.
    if trimmed.endswith("\\"):
        trimmed = trimmed[:-1].rstrip()

    return trimmed + "…"


def format_telegram_caption(parts: dict, stamp: str) -> str:
    """Short rendered caption for sendPhoto."""
    header = parts.get("header_text", "")
    theme = parts.get("theme_text", "")

    if TELEGRAM_PARSE_MODE == "MarkdownV2":
        caption = "\n".join(
            [
                f"*{telegram_markdown_v2_escape(f'WOL Daily Text ({stamp})')}*",
                "",
                telegram_markdown_v2_escape(header),
                "",
                f"*{telegram_markdown_v2_escape('Theme Scripture:')}*",
                telegram_markdown_v2_escape(theme),
                "",
                telegram_markdown_v2_escape("Source: wol.jw.org"),
            ]
        ).strip()
    else:
        caption = f"""WOL Daily Text ({stamp})

{header}

Theme Scripture:
{theme}

Source: wol.jw.org""".strip()

    return telegram_trim(caption, 1024)


def format_telegram_message(parts: dict, stamp: str, readable: str, ai_explainer: str) -> str:
    """Telegram-friendly message. MarkdownV2 is escaped to avoid parse errors."""
    if TELEGRAM_PARSE_MODE == "MarkdownV2":
        return "\n".join(
            [
                f"*{telegram_markdown_v2_escape(f'WOL Daily Text ({stamp})')}*",
                "",
                f"*{telegram_markdown_v2_escape('Daily Text:')}*",
                telegram_markdown_v2_escape(readable),
                "",
                f"*{telegram_markdown_v2_escape('AI Explainer:')}*",
                telegram_markdown_v2_escape(ai_explainer or "AI explainer not available."),
            ]
        ).strip()

    return f"""WOL Daily Text ({stamp})

{readable}

AI EXPLAINER:

{ai_explainer}""".strip()


def telegram_send_photo(photo_path: str, caption: str, token: str, chat_id: str, parse_mode: str = "") -> None:
    api = f"https://api.telegram.org/bot{token}/sendPhoto"

    if not os.path.exists(photo_path):
        raise FileNotFoundError(f"Photo not found: {photo_path}")

    data = {
        "chat_id": chat_id,
        "caption": caption[:1024],
    }

    if parse_mode:
        data["parse_mode"] = parse_mode

    with open(photo_path, "rb") as f:
        resp = requests.post(
            api,
            data=data,
            files={
                "photo": f,
            },
            timeout=(15, 120),
        )

    if resp.status_code >= 400:
        try:
            err = resp.json()
        except Exception:
            err = {"raw": resp.text}
        raise RuntimeError(f"Telegram sendPhoto error {resp.status_code}: {err}")


def telegram_send_message(text: str, token: str, chat_id: str, parse_mode: str = "") -> None:
    api = f"https://api.telegram.org/bot{token}/sendMessage"

    def chunks(s: str, n: int = 3500):
        start = 0
        while start < len(s):
            end = min(start + n, len(s))

            # Avoid splitting right after a MarkdownV2 escape slash.
            if parse_mode == "MarkdownV2":
                while end > start and s[end - 1] == "\\":
                    end -= 1

            if end == start:
                end = min(start + n, len(s))

            yield s[start:end]
            start = end

    for part in chunks(text):
        data = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": True,
        }

        if parse_mode:
            data["parse_mode"] = parse_mode

        resp = requests.post(
            api,
            data=data,
            timeout=(15, 60),
        )

        if resp.status_code >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {"raw": resp.text}
            raise RuntimeError(f"Telegram error {resp.status_code}: {err}")

        time.sleep(0.7)


def telegram_send_document(document_path: str, caption: str, token: str, chat_id: str) -> None:
    api = f"https://api.telegram.org/bot{token}/sendDocument"

    if not os.path.exists(document_path):
        raise FileNotFoundError(f"Document not found: {document_path}")

    with open(document_path, "rb") as f:
        resp = requests.post(
            api,
            data={
                "chat_id": chat_id,
                "caption": caption[:1024],
            },
            files={
                "document": f,
            },
            timeout=(15, 120),
        )

    if resp.status_code >= 400:
        try:
            err = resp.json()
        except Exception:
            err = {"raw": resp.text}
        raise RuntimeError(f"Telegram sendDocument error {resp.status_code}: {err}")


# -----------------------------
# HTML/Text Helpers
# -----------------------------
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


def extract_daily_parts(content_html: str) -> dict:
    m = re.search(
        r"<h2[^>]*>(.*?)</h2>",
        content_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    header_text = html_to_text(m.group(0)) if m else ""

    m = re.search(
        r'<p[^>]*class="[^"]*\bthemeScrp\b[^"]*"[^>]*>.*?</p>',
        content_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    theme_text = html_to_text(m.group(0)) if m else ""

    m = re.search(
        r'<div[^>]*class="[^"]*\bbodyTxt\b[^"]*"[^>]*>(.*?)</div>',
        content_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body_text = html_to_text(m.group(1)) if m else ""
    body_text = re.sub(r"\s+", " ", body_text).strip()

    return {
        "header_text": header_text.strip(),
        "theme_text": theme_text.strip(),
        "body_text": body_text.strip(),
    }


def format_human_readable(content_html: str) -> str:
    parts = extract_daily_parts(content_html)

    return "\n".join(
        [
            "=" * 60,
            f"DATE: {parts['header_text']}",
            "-" * 60,
            "THEME SCRIPTURE:",
            parts["theme_text"],
            "",
            "MESSAGE:",
            parts["body_text"],
            "=" * 60,
        ]
    )


def format_markdown_post(parts: dict, stamp: str, ai_explainer: str = "", source_url: str = "") -> str:
    """Create a reusable .md version of the daily text and AI explainer."""
    header = parts.get("header_text", "").strip()
    theme = parts.get("theme_text", "").strip()
    body = parts.get("body_text", "").strip()

    lines = [
        f"# WOL Daily Text ({stamp})",
        "",
        f"**Date:** {header or stamp}",
        "",
        "## Theme Scripture",
        theme or "_No theme scripture found._",
        "",
        "## Message",
        body or "_No message found._",
        "",
    ]

    if ai_explainer:
        lines.extend(
            [
                "## AI Explainer",
                ai_explainer.strip(),
                "",
            ]
        )

    lines.extend(
        [
            "---",
            f"Source: {source_url or 'wol.jw.org'}",
        ]
    )

    return "\n".join(lines).strip() + "\n"


def format_html_post(content_html: str, stamp: str, image_url: str = "") -> str:
    parts = extract_daily_parts(content_html)

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    image_block = ""
    if image_url:
        image_block = (
            f'<p><img src="{esc(image_url)}" alt="Daily Text Image" '
            f'style="max-width:100%;height:auto;border-radius:8px;"/></p>'
        )

    return f"""\
<!doctype html>
<html>
  <body>
    <h2>{esc(parts["header_text"] or f"WOL Daily Text ({stamp})")}</h2>
    {image_block}
    <hr/>
    <h3>Theme Scripture</h3>
    <p>{esc(parts["theme_text"])}</p>
    <h3>Message</h3>
    <p>{esc(parts["body_text"])}</p>
    <hr/>
    <p style="font-size:12px;color:#666;">Source: wol.jw.org · {esc(stamp)}</p>
  </body>
</html>
"""


# -----------------------------
# Cache Helpers
# -----------------------------
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


def jitter_sleep(mult=1.0):
    time.sleep(mult * random.uniform(1.0, 3.0))


# -----------------------------
# Email Helper
# -----------------------------
def post_to_blogger(subject: str, html_body: str, attachment_path: str = "") -> bool:
    to_addr = BLOGGER_POST_EMAIL

    if not to_addr or not SMTP_USER or not SMTP_PASS:
        print("Email SMTP not configured.")
        return False

    try:
        msg = EmailMessage()
        msg["From"] = SMTP_USER
        msg["To"] = to_addr
        msg["Subject"] = subject

        msg.set_content("This post requires an HTML-capable email client.")
        msg.add_alternative(html_body, subtype="html")

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                file_data = f.read()

            filename = os.path.basename(attachment_path)
            ext = os.path.splitext(filename)[1].lower()

            if ext in (".jpg", ".jpeg"):
                maintype, subtype = "image", "jpeg"
            elif ext == ".png":
                maintype, subtype = "image", "png"
            elif ext == ".webp":
                maintype, subtype = "image", "webp"
            else:
                maintype, subtype = "application", "octet-stream"

            msg.add_attachment(
                file_data,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)

        return True

    except Exception as e:
        print(f"Email post failed: {e}")
        return False


# -----------------------------
# Main
# -----------------------------
def main():
    url = BASE.format(year=YEAR, month=MONTH, day=DAY)
    stamp = f"{YEAR:04d}-{MONTH:02d}-{DAY:02d}"

    raw_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.json")
    cache_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.cache.json")
    log_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.log")
    explainer_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}_ai_explainer.txt")
    markdown_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.md")
    agent_memory_debug_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}_agent_memory.md")

    cache = load_cache(cache_path)

    etag = cache.get("etag")
    last_modified = cache.get("last_modified")

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ceb,en-US;q=0.8,en;q=0.6",
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
                    print("304 Not Modified - using existing JSON:", raw_path)
                    with open(raw_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    resp_headers = {
                        "ETag": etag,
                        "Last-Modified": last_modified,
                    }
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

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("Saved JSON:", raw_path)

    save_cache(
        cache_path,
        {
            "url": url,
            "etag": (resp_headers or {}).get("ETag") or etag,
            "last_modified": (resp_headers or {}).get("Last-Modified") or last_modified,
            "saved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    print("Saved cache:", cache_path)

    daily = (payload.get("items") or [None])[0]

    image_path = os.path.join(OUT_DIR, f"wol_dt_{stamp}.jpg")
    ai_explainer = ""

    if daily and daily.get("content"):
        parts = extract_daily_parts(daily["content"])
        readable = format_human_readable(daily["content"])

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(readable + "\n")

        print("Saved human-readable log:", log_path)

        # 1. Generate image
        try:
            image_prompt = (
                "Create a peaceful, uplifting Christian devotional illustration "
                f"inspired by this theme: '{parts['theme_text']}'. "
                "Soft light, clean composition, warm colors, respectful, no text in image."
            )

            generate_image_cloudflare(
                prompt=image_prompt,
                output_path=image_path,
                seed=DAY + MONTH + YEAR,
                width=1024,
                height=1024,
                steps=4,
            )

            print("Saved generated image:", image_path)

        except Exception as e:
            print(f"Cloudflare image generation failed: {e}")

        # 2. Load previous Markdown files as lightweight agent memory
        memory_context = load_previous_markdown_memory(
            out_dir=OUT_DIR,
            current_stamp=stamp,
        )

        if memory_context:
            with open(agent_memory_debug_path, "w", encoding="utf-8") as f:
                f.write(memory_context + "\n")
            print("Loaded previous Markdown agent memory:", agent_memory_debug_path)
        else:
            print("No previous Markdown agent memory found.")

        # 3. Generate AI explainer using today's content + previous .md style memory
        try:
            ai_explainer = generate_daily_explainer(parts, memory_context=memory_context)

            with open(explainer_path, "w", encoding="utf-8") as f:
                f.write(ai_explainer + "\n")

            print("Saved AI explainer:", explainer_path)

        except Exception as e:
            ai_explainer = "AI explainer failed to generate."
            print(f"Cloudflare AI explainer failed: {e}")

        # 4. Save Markdown copy for future reuse and Telegram document sending
        try:
            markdown_post = format_markdown_post(
                parts=parts,
                stamp=stamp,
                ai_explainer=ai_explainer,
                source_url=url,
            )

            with open(markdown_path, "w", encoding="utf-8") as f:
                f.write(markdown_post)

            print("Saved Markdown post:", markdown_path)

        except Exception as e:
            print(f"Markdown post save failed: {e}")

        # 5. Email/Blogger optional
        if SEND_EMAIL:
            html_post = format_html_post(daily["content"], stamp)
            subject = f"WOL Daily Text ({stamp})"

            ok = post_to_blogger(
                subject=subject,
                html_body=html_post,
                attachment_path=image_path,
            )

            print("Email sent." if ok else "Email not sent.")

    else:
        parts = {
            "header_text": f"WOL Daily Text ({stamp})",
            "theme_text": "",
            "body_text": "",
        }

        readable = f"WOL Daily Text ({stamp})\n\nNo content field found."

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(readable + "\n")

        markdown_post = format_markdown_post(
            parts=parts,
            stamp=stamp,
            ai_explainer="",
            source_url=url,
        )

        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(markdown_post)

        print("Saved fallback log:", log_path)
        print("Saved fallback Markdown post:", markdown_path)

    # 6. Telegram send
    if SEND_TELEGRAM and TG_TOKEN and TG_CHAT_ID:
        try:
            caption = format_telegram_caption(parts, stamp)
            telegram_text = format_telegram_message(parts, stamp, readable, ai_explainer)
            parse_mode = TELEGRAM_PARSE_MODE if TELEGRAM_PARSE_MODE else ""

            if os.path.exists(image_path):
                telegram_send_photo(
                    photo_path=image_path,
                    caption=caption,
                    token=TG_TOKEN,
                    chat_id=TG_CHAT_ID,
                    parse_mode=parse_mode,
                )

                telegram_send_message(
                    telegram_text,
                    TG_TOKEN,
                    TG_CHAT_ID,
                    parse_mode=parse_mode,
                )

                print("Sent image, Markdown-formatted daily text, and AI explainer to Telegram.")

            else:
                telegram_send_message(
                    telegram_text,
                    TG_TOKEN,
                    TG_CHAT_ID,
                    parse_mode=parse_mode,
                )

                print("Sent Markdown-formatted daily text and AI explainer to Telegram.")

            if SEND_MARKDOWN_FILE and os.path.exists(markdown_path):
                telegram_send_document(
                    document_path=markdown_path,
                    caption=f"WOL Daily Text Markdown ({stamp})",
                    token=TG_TOKEN,
                    chat_id=TG_CHAT_ID,
                )

                print("Sent Markdown .md file to Telegram.")

        except Exception as e:
            print(f"Telegram send failed: {e}")

    else:
        print("Telegram not configured or SEND_TELEGRAM=0.")


if __name__ == "__main__":
    main()
