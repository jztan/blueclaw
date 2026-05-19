"""Measure raw-HTML vs trafilatura-extracted token counts for a set of URLs.

Methodology: single fresh fetch per URL via curl_cffi (impersonate=chrome124),
token-counted with tiktoken cl100k_base. Extraction mirrors
blueclaw.tools.web._extract_main_text (favor_recall=True, title prepended).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import tiktoken
import trafilatura
from curl_cffi import requests as curl_requests

SITES = [
    ("Medium", "https://medium.com/@nikhilanandnj/the-real-reason-startups-are-falling-behind-on-ai-9b6cf9d4b1d0"),
    ("Substack (ACX)", "https://astralcodexten.substack.com/p/your-book-review-the-pale-king"),
    ("NYTimes (article)", "https://www.nytimes.com/2025/01/15/technology/biden-ai-executive-order.html"),
    ("NYTimes (homepage)", "https://www.nytimes.com/"),
    ("IEEE Spectrum", "https://spectrum.ieee.org/quantum-error-correction"),
    ("Indie blog (Simon Willison)", "https://simonwillison.net/2024/Dec/31/llms-in-2024/"),
]

ENC = tiktoken.get_encoding("cl100k_base")


def extract(html: str) -> str:
    text = trafilatura.extract(
        html, include_comments=False, include_tables=True, favor_recall=True
    )
    if not text:
        return ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "").strip()
    return f"{title}\n\n{text}" if title else text


def measure(url: str) -> dict:
    try:
        r = curl_requests.get(
            url, impersonate="chrome124", timeout=30, allow_redirects=True
        )
    except Exception as e:
        return {"url": url, "error": f"fetch_error: {e}"}
    if r.status_code >= 400:
        return {"url": url, "error": f"HTTP {r.status_code}"}
    html = r.text
    raw_tokens = len(ENC.encode(html))
    extracted = extract(html)
    if not extracted:
        return {"url": url, "raw_tokens": raw_tokens, "error": "extraction_empty"}
    ext_tokens = len(ENC.encode(extracted))
    reduction = 1.0 - (ext_tokens / raw_tokens) if raw_tokens else 0.0
    return {
        "url": url,
        "raw_tokens": raw_tokens,
        "extracted_tokens": ext_tokens,
        "reduction": reduction,
    }


def main() -> int:
    results = []
    for site, url in SITES:
        row = {"site": site, **measure(url)}
        results.append(row)

    out = Path(__file__).parent / "extraction_tokens_results.json"
    out.write_text(json.dumps(results, indent=2))

    print("| Site | URL | Raw HTML tokens | After trafilatura | Reduction |")
    print("|---|---|---:|---:|---:|")
    for r in results:
        url = r["url"]
        short = url if len(url) <= 60 else url[:57] + "..."
        if "error" in r and "raw_tokens" not in r:
            print(f"| {r['site']} | {short} | — | — | {r['error']} |")
        elif "error" in r:
            print(f"| {r['site']} | {short} | {r['raw_tokens']:,} | — | {r['error']} |")
        else:
            print(
                f"| {r['site']} | {short} | {r['raw_tokens']:,} | "
                f"{r['extracted_tokens']:,} | {r['reduction']*100:.1f}% |"
            )
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
