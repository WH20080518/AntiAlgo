"""Async RSS/Atom feed collector using httpx and feedparser."""

from __future__ import annotations

import asyncio
import calendar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re
from typing import Any
from urllib.parse import urlsplit

import feedparser
import httpx

from pipeline import (
    get_default_source_limit,
    get_max_summary_chars,
    get_rss_sources,
)
from pipeline.db import compute_url_hash, normalize_url, utc_now_iso

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
GOOGLE_NEWS_SUFFIX_RE = re.compile(r"\s+-\s+[^-]+$")
YOUTUBE_SHORT_HASHTAG_RE = re.compile(r"(?:^|\s)#shorts?(?:\b|$)", re.IGNORECASE)
DEFAULT_MAX_SUMMARY_CHARS = 800
DEFAULT_SAME_HOST_DELAY_SECONDS = 2.0
NON_RETRYABLE_STATUS_CODES = frozenset({401, 403, 404, 410})

DEFAULT_USER_AGENT = "AntiAlgoHub/0.1 (+https://github.com/local/anti-algo-hub)"
REDDIT_USER_AGENT = "windows:anti-algo-hub:v0.1 (by /u/personal_rss_reader)"


def _clean_text(raw: str, max_chars: int | None = None) -> str:
    if not raw:
        return ""
    stripped = TAG_RE.sub(" ", raw)
    unescaped = html.unescape(stripped)
    collapsed = WHITESPACE_RE.sub(" ", unescaped).strip()
    if max_chars is not None and len(collapsed) > max_chars:
        return collapsed[: max_chars - 1].rstrip() + "…"
    return collapsed


def clean_google_news_title(title: str, entry: Any = None) -> str:
    """Strip trailing ' - Publisher' suffix from Google News RSS titles."""
    cleaned = title.strip()
    if not cleaned:
        return ""

    if isinstance(entry, dict) or hasattr(entry, "get"):
        source_meta = entry.get("source")
        if isinstance(source_meta, dict) or hasattr(source_meta, "get"):
            pub_title = str(source_meta.get("title") or "").strip()
            suffix = f" - {pub_title}"
            if pub_title and cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 3:
                return cleaned[: -len(suffix)].rstrip()

    match = GOOGLE_NEWS_SUFFIX_RE.search(cleaned)
    if match and match.start() >= 10:
        return cleaned[: match.start()].rstrip()
    return cleaned


def _parse_entry_timestamp(entry: Any) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        parsed_struct = entry.get(attr)
        if parsed_struct:
            try:
                ts = calendar.timegm(parsed_struct)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError, OverflowError):
                pass

    for attr in ("published", "updated"):
        raw_date = entry.get(attr)
        if not raw_date:
            continue
        try:
            dt = parsedate_to_datetime(str(raw_date))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    return utc_now_iso()


def is_youtube_short(url: str, title: str) -> bool:
    """Return True when a feed entry points to or is tagged as a YouTube Short."""
    if "/shorts/" in (url or "").lower():
        return True
    return bool(YOUTUBE_SHORT_HASHTAG_RE.search(title or ""))


def matches_include_keywords(
    title: str,
    raw_summary: str,
    include_keywords: list[str] | None,
) -> bool:
    """Return True when include_keywords is empty or matches title/summary (case-insensitive)."""
    cleaned = [str(kw).strip().lower() for kw in (include_keywords or []) if str(kw).strip()]
    if not cleaned:
        return True
    haystack = f"{title}\n{raw_summary}".lower()
    return any(kw in haystack for kw in cleaned)


def _extract_raw_summary(
    entry: Any, max_chars: int = DEFAULT_MAX_SUMMARY_CHARS
) -> str:
    raw = (
        entry.get("media_description")
        or entry.get("summary")
        or entry.get("description")
        or ""
    )
    if not raw:
        content_list = entry.get("content")
        if isinstance(content_list, list) and content_list:
            first = content_list[0]
            if isinstance(first, dict):
                raw = first.get("value", "")
    return _clean_text(str(raw), max_chars=max_chars)


def build_request_headers(
    url: str, custom_headers: dict[str, Any] | None = None
) -> dict[str, str]:
    """Build polite request headers with Reddit UA handling and per-source overrides."""
    host = urlsplit(url).netloc.lower()
    if "reddit.com" in host:
        headers = {
            "User-Agent": REDDIT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        }
    else:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    if isinstance(custom_headers, dict):
        for key, val in custom_headers.items():
            k = str(key).strip()
            v = str(val).strip()
            if k and v:
                headers[k] = v
    return headers


def group_sources_by_host(
    sources: list[dict[str, Any]],
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """Group sources by lowercase URL host while preserving original index order."""
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for idx, src in enumerate(sources):
        url = str(src.get("url", "")).strip()
        host = urlsplit(url).netloc.lower() or f"__unknown_{idx}"
        grouped.setdefault(host, []).append((idx, src))
    return grouped


async def fetch_feed(
    client: httpx.AsyncClient,
    source: dict[str, Any],
    retries: int = 2,
    max_items: int = 15,
    max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and parse a single RSS/Atom feed with smart retries."""
    name = str(source.get("name", "Unnamed Source"))
    domain = str(source.get("domain", "tech"))
    url = str(source.get("url", "")).strip()
    source_limit = int(source.get("limit") or max_items)
    exclude_shorts = bool(source.get("exclude_shorts", False))
    raw_include = source.get("include_keywords")
    include_keywords = (
        [str(kw).strip() for kw in raw_include if str(kw).strip()]
        if isinstance(raw_include, list)
        else []
    )

    if not url:
        return [], {
            "name": name,
            "domain": domain,
            "url": url,
            "ok": False,
            "fetched": 0,
            "error": "Missing source URL",
        }

    host = urlsplit(url).netloc.lower()
    is_google_news = host == "news.google.com" or host.endswith(".news.google.com")
    req_headers = build_request_headers(url, source.get("headers"))
    last_error: str | None = None
    attempts = max(1, retries + 1)

    for attempt in range(attempts):
        try:
            response = await client.get(url, headers=req_headers)
            response.raise_for_status()

            parsed = feedparser.parse(response.content)
            entries = list(parsed.get("entries") or [])
            if getattr(parsed, "bozo", False) and not entries:
                exc = getattr(parsed, "bozo_exception", "Invalid feed format")
                raise ValueError(f"Malformed feed: {exc}")

            items: list[dict[str, Any]] = []
            for entry in entries:
                link = str(entry.get("link") or "").strip()
                title = _clean_text(str(entry.get("title") or ""))
                if is_google_news and title:
                    title = clean_google_news_title(title, entry)
                if not link or not title:
                    continue
                if exclude_shorts and is_youtube_short(link, title):
                    continue

                raw_summary = _extract_raw_summary(
                    entry, max_chars=max_summary_chars
                )
                if include_keywords and not matches_include_keywords(
                    title, raw_summary, include_keywords
                ):
                    continue

                canonical_url = normalize_url(link) or link
                items.append(
                    {
                        "url_hash": compute_url_hash(canonical_url),
                        "domain": domain,
                        "source_name": name,
                        "title": title,
                        "raw_summary": raw_summary,
                        "url": canonical_url,
                        "published_at": _parse_entry_timestamp(entry),
                    }
                )
                if len(items) >= source_limit:
                    break

            return items, {
                "name": name,
                "domain": domain,
                "url": url,
                "ok": True,
                "fetched": len(items),
                "error": None,
            }
        except httpx.HTTPStatusError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if exc.response.status_code in NON_RETRYABLE_STATUS_CODES:
                break
            if attempt < attempts - 1:
                await asyncio.sleep(2**attempt)
        except Exception as exc:  # noqa: BLE001 - isolate feed failures cleanly
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                await asyncio.sleep(2**attempt)

    return [], {
        "name": name,
        "domain": domain,
        "url": url,
        "ok": False,
        "fetched": 0,
        "error": last_error or "Unknown fetch error",
    }


async def collect_rss_sources(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect enabled RSS sources concurrently across hosts and serially per host."""
    settings = config.get("settings") or {}
    timeout_seconds = float(settings.get("fetch_timeout_seconds", 15))
    retries = int(settings.get("fetch_retries", 2))
    same_host_delay = float(
        settings.get("same_host_delay_seconds", DEFAULT_SAME_HOST_DELAY_SECONDS)
    )
    default_limit = get_default_source_limit(config)
    max_summary_chars = get_max_summary_chars(config)

    sources = get_rss_sources(config)
    if not sources:
        return [], []

    grouped_by_host = group_sources_by_host(sources)
    semaphore = asyncio.Semaphore(6)
    ordered_results: list[tuple[list[dict[str, Any]], dict[str, Any]] | None] = [
        None
    ] * len(sources)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
    ) as client:

        async def _fetch_host_queue(
            host_entries: list[tuple[int, dict[str, Any]]],
        ) -> None:
            async with semaphore:
                for pos, (orig_idx, src) in enumerate(host_entries):
                    if pos > 0 and same_host_delay > 0:
                        await asyncio.sleep(same_host_delay)
                    ordered_results[orig_idx] = await fetch_feed(
                        client,
                        src,
                        retries=retries,
                        max_items=default_limit,
                        max_summary_chars=max_summary_chars,
                    )

        await asyncio.gather(
            *(_fetch_host_queue(entries) for entries in grouped_by_host.values())
        )

    all_items: list[dict[str, Any]] = []
    source_stats: list[dict[str, Any]] = []
    for result in ordered_results:
        if result is None:
            continue
        items, stat = result
        all_items.extend(items)
        source_stats.append(stat)

    return all_items, source_stats
