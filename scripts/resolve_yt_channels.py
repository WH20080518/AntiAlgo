"""Resolve official YouTube channel UC... IDs from @handle URLs for Atom RSS setup."""

from __future__ import annotations

import html
import re
import sys
import time
from typing import Any

import httpx

DEFAULT_HANDLES: list[str] = [
    "@BillieEilish",
    "@OliviaRodrigo",
    "@TaylorSwift",
    "@SabrinaCarpenter",
    "@LanaDelRey",
    "@ArianaGrande",
    "@JYPEntertainment",
    "@HYBELABELS",
    "@SMTOWN",
    "@RESCENE_official",
]

CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
    re.IGNORECASE,
)
EXTERNAL_ID_RE = re.compile(r'"externalId"\s*:\s*"(UC[\w-]{22})"')
BROWSE_ID_RE = re.compile(r'"browseId"\s*:\s*"(UC[\w-]{22})"')

OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]*)"',
    re.IGNORECASE,
)
META_TITLE_RE = re.compile(
    r'<meta\s+name="title"\s+content="([^"]*)"',
    re.IGNORECASE,
)
HTML_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _normalize_handle(raw: str) -> str:
    handle = raw.strip()
    if handle.startswith("https://") or handle.startswith("http://"):
        return handle.rstrip("/")
    if not handle.startswith("@"):
        handle = f"@{handle}"
    return handle


def _extract_channel_name(page_html: str) -> str:
    for pattern in (OG_TITLE_RE, META_TITLE_RE):
        match = pattern.search(page_html)
        if match and match.group(1).strip():
            return html.unescape(match.group(1).strip())

    match = HTML_TITLE_RE.search(page_html)
    if match:
        title = html.unescape(match.group(1).strip())
        if title.endswith(" - YouTube"):
            title = title[: -len(" - YouTube")].strip()
        if title:
            return title
    return ""


def _extract_channel_id(page_html: str) -> tuple[str | None, str | None]:
    for source_label, pattern in (
        ("canonical", CANONICAL_RE),
        ("externalId", EXTERNAL_ID_RE),
        ("browseId", BROWSE_ID_RE),
    ):
        match = pattern.search(page_html)
        if match:
            return match.group(1), source_label
    return None, None


def resolve_handle(client: httpx.Client, raw_handle: str) -> dict[str, Any]:
    """Fetch a single YouTube @handle page and extract its real UC... channel ID."""
    normalized = _normalize_handle(raw_handle)
    url = (
        normalized
        if normalized.startswith("http")
        else f"https://www.youtube.com/{normalized}"
    )

    try:
        response = client.get(url)
    except Exception as exc:  # noqa: BLE001
        return {
            "handle": normalized,
            "ok": False,
            "name": "",
            "channel_id": None,
            "source": None,
            "final_url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }

    final_url = str(response.url)
    if response.status_code != 200:
        return {
            "handle": normalized,
            "ok": False,
            "name": "",
            "channel_id": None,
            "source": None,
            "final_url": final_url,
            "error": f"HTTP {response.status_code}",
        }

    page_html = response.text
    if "consent.youtube.com" in final_url.lower() and not (
        CANONICAL_RE.search(page_html) or EXTERNAL_ID_RE.search(page_html)
    ):
        return {
            "handle": normalized,
            "ok": False,
            "name": "",
            "channel_id": None,
            "source": None,
            "final_url": final_url,
            "error": "Redirected to consent interstitial without channel HTML",
        }

    channel_id, extract_source = _extract_channel_id(page_html)
    channel_name = _extract_channel_name(page_html)

    if not channel_id:
        return {
            "handle": normalized,
            "ok": False,
            "name": channel_name,
            "channel_id": None,
            "source": None,
            "final_url": final_url,
            "error": "Channel ID (UC...) not found in page HTML",
        }

    return {
        "handle": normalized,
        "ok": True,
        "name": channel_name or normalized,
        "channel_id": channel_id,
        "source": extract_source,
        "final_url": final_url,
        "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        "error": None,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = argv if argv is not None else sys.argv[1:]
    handles = args if args else DEFAULT_HANDLES

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    results: list[dict[str, Any]] = []
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(15.0),
    ) as client:
        for idx, handle in enumerate(handles):
            if idx > 0:
                time.sleep(0.5)
            results.append(resolve_handle(client, handle))

    print(
        f"{'Handle':<22} | {'Status':<6} | {'Channel Name':<28} | "
        f"{'Channel ID':<24} | {'Via':<10} | Atom RSS URL / Error"
    )
    print("-" * 145)
    failures = 0
    for res in results:
        if res["ok"]:
            print(
                f"{res['handle']:<22} | {'OK':<6} | {res['name'][:28]:<28} | "
                f"{res['channel_id']:<24} | {str(res['source']):<10} | {res['rss_url']}"
            )
        else:
            failures += 1
            print(
                f"{res['handle']:<22} | {'FAIL':<6} | {res['name'][:28]:<28} | "
                f"{'-':<24} | {'-':<10} | {res['error']} ({res['final_url']})"
            )

    print(
        f"\nResolved {len(results) - failures}/{len(results)} handles"
        + (f" ({failures} failed)." if failures else ".")
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
