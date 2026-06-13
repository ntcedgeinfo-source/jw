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
            time.sleep((2 ** i) + random.random())

    raise RuntimeError(f"Failed to fetch after {RETRIES} retries: {last_err}")


def clean_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def escape_markdown_link_text(text: str) -> str:
    return (
        clean_text(text)
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def escape_markdown_url(url: str) -> str:
    return (
        (url or "").strip()
        .replace("(", "%28")
        .replace(")", "%29")
    )


def write_links_markdown(source_url: str, links: list, path: str) -> None:
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    lines = [
        "# WOL Cebuano Links Memory",
        "",
        f"**Source:** {source_url}",
        f"**Fetched At UTC:** {fetched_at}",
        f"**Total Links:** {len(links)}",
        "",
        "## Purpose",
        "",
        "This Markdown file works as a simple memory/index file for the WOL Cebuano scraper.",
        "It helps future scripts or AI agents check previous WOL links and page structure.",
        "",
        "## Links",
        "",
    ]

    for i, item in enumerate(links, start=1):
        text = escape_markdown_link_text(item.get("text", ""))
        url = escape_markdown_url(item.get("url", ""))

        if text and url:
            lines.append(f"{i}. [{text}]({url})")

    lines.extend([
        "",
        "---",
        "",
        "## Agent Memory Rules",
        "",
        "- Use this file only as a link/index memory.",
        "- Do not use this file as doctrine.",
        "- Do not invent explanations from links alone.",
        "- For Daily Text meaning, always fetch the actual daily text content.",
        "- Use previous Markdown files only to understand structure, tone, and formatting.",
        "- Keep future Telegram replies simple, respectful, and Cebuano-friendly.",
    ])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


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
            text = clean_text(a.get_text(" ", strip=True))

            if text:
                links.append({
                    "text": text,
                    "url": full,
                })

    # De-duplicate by URL while preserving order
    seen = set()
    unique = []

    for item in links:
        url = item["url"]

        if url in seen:
            continue

        seen.add(url)
        unique.append(item)

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    out_json = {
        "source_url": URL,
        "fetched_at_utc": fetched_at,
        "count": len(unique),
        "links": unique,
    }

    # Save JSON
    json_path = os.path.join(OUT_DIR, "wol_links.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)

    # Save TSV
    tsv_path = os.path.join(OUT_DIR, "wol_links.tsv")
    with open(tsv_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(f"{item['text']}\t{item['url']}\n")

    # Save Markdown memory/index file
    md_path = os.path.join(OUT_DIR, "wol_links.md")
    write_links_markdown(URL, unique, md_path)

    print(f"Saved: {html_path}")
    print(f"Saved: {json_path}")
    print(f"Saved: {tsv_path}")
    print(f"Saved: {md_path}")
    print(f"Extracted links: {len(unique)}")


if __name__ == "__main__":
    main()
