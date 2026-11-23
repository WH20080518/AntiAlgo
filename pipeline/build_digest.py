"""Export processed intelligence from SQLite to static web/public/digest.json."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

from pipeline import (
    DAILY_STATE_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_DIGEST_PATH,
    DEFAULT_FALLBACK_DAILY_TERM,
    DEFAULT_TERMS_PATH,
    get_enabled_source_names,
    get_max_digest_items,
    get_min_digest_importance,
    get_upcoming_settings,
    get_window_hours,
    load_config,
    load_daily_state,
)
from pipeline.db import (
    backfill_daily_term_from_state,
    cleanup_expired_upcoming,
    compute_event_id,
    get_all_daily_terms,
    get_glossary_stats,
    get_recent_items,
    get_upcoming_events,
    is_upcoming_within_window,
    normalize_event_start_time,
    normalize_url,
    utc_now_iso,
)


def _truncate_summary(text: str, max_chars: int = 120) -> str:
    clean = " ".join(text.split()).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "…"


def _parse_json_or_default(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    stripped = raw.strip()
    if not stripped:
        return default
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return default


def merge_clustered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows sharing a non-empty cluster_id into one primary card with related_links."""
    if not rows:
        return []

    clustered: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []

    for row in rows:
        cid = str(row.get("cluster_id") or "").strip()
        if cid:
            clustered.setdefault(cid, []).append(row)
        else:
            standalone.append(dict(row))

    merged_rows: list[dict[str, Any]] = list(standalone)

    for group in clustered.values():
        sorted_group = sorted(
            group,
            key=lambda r: (
                1 if r.get("ai_summary") else 0,
                int(r.get("importance", 3)),
                str(r.get("published_at", "")),
            ),
            reverse=True,
        )
        primary = dict(sorted_group[0])
        primary_url = str(primary.get("url", "")).strip()
        primary_norm = normalize_url(primary_url) or primary_url

        collected_links: list[str] = []
        seen_norms: set[str] = {primary_norm}

        def _add_link(candidate: str) -> None:
            link = str(candidate or "").strip()
            if not link:
                return
            norm = normalize_url(link) or link
            if norm in seen_norms:
                return
            seen_norms.add(norm)
            collected_links.append(link)

        primary_related = _parse_json_or_default(primary.get("related_links"), [])
        if isinstance(primary_related, list):
            for link in primary_related:
                _add_link(str(link))

        inherited_spoiler = _parse_json_or_default(
            primary.get("spoiler_payload"), None
        )

        for secondary in sorted_group[1:]:
            _add_link(str(secondary.get("url", "")))
            sec_related = _parse_json_or_default(secondary.get("related_links"), [])
            if isinstance(sec_related, list):
                for link in sec_related:
                    _add_link(str(link))
            if inherited_spoiler is None:
                inherited_spoiler = _parse_json_or_default(
                    secondary.get("spoiler_payload"), None
                )

        primary["related_links"] = collected_links
        if inherited_spoiler is not None:
            primary["spoiler_payload"] = inherited_spoiler

        merged_rows.append(primary)

    merged_rows.sort(
        key=lambda r: (int(r.get("importance", 3)), str(r.get("published_at", ""))),
        reverse=True,
    )
    return merged_rows


def load_merged_digest_items(
    config: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    filter_enabled_sources: bool = True,
    require_processed: bool = True,
    min_importance: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Load active 72h items from enabled sources, merge clusters, and return (items, merged_count)."""
    max_age_hours = get_window_hours(config)
    max_digest_items = get_max_digest_items(config)
    eff_min_importance = (
        int(min_importance)
        if min_importance is not None
        else (get_min_digest_importance(config) if require_processed else 1)
    )
    enabled_sources = (
        get_enabled_source_names(config) if filter_enabled_sources else None
    )

    raw_rows = get_recent_items(
        max_age_hours=max_age_hours,
        limit=None,
        enabled_sources=enabled_sources,
        min_importance=eff_min_importance,
        require_processed=require_processed,
        db_path=db_path,
    )
    merged_rows = merge_clustered_rows(raw_rows)
    clusters_merged = len(raw_rows) - len(merged_rows)
    items = [format_digest_item(row) for row in merged_rows[:max_digest_items]]
    return items, clusters_merged


def build_top_briefings(items: list[dict[str, Any]], max_count: int = 3) -> list[str]:
    """Select up to max_count top headlines (importance >= 3) with domain diversity."""
    eligible = [item for item in items if int(item.get("importance", 0)) >= 3]
    if not eligible or max_count <= 0:
        return []

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_domains: set[str] = set()

    for item in eligible:
        domain = str(item.get("domain", ""))
        item_id = str(item.get("id", ""))
        if domain not in seen_domains and item_id not in seen_ids:
            selected.append(item)
            seen_domains.add(domain)
            seen_ids.add(item_id)
            if len(selected) >= max_count:
                break

    if len(selected) < max_count:
        for item in eligible:
            item_id = str(item.get("id", ""))
            if item_id not in seen_ids:
                selected.append(item)
                seen_ids.add(item_id)
                if len(selected) >= max_count:
                    break

    return [str(entry["title"]) for entry in selected[:max_count]]


def _format_upcoming_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": str(row.get("domain") or "tech").strip().lower(),
        "title": " ".join(str(row.get("title") or "").split()).strip(),
        "subtitle": " ".join(str(row.get("subtitle") or "").split()).strip(),
        "start_time": str(row.get("start_time") or "").strip(),
        "precision": (
            "date_only"
            if str(row.get("precision") or "").strip() == "date_only"
            else "datetime"
        ),
        "tag": str(row.get("tag") or "").strip(),
        "source": str(row.get("source") or "manual").strip(),
        "url": str(row.get("url") or "").strip(),
    }


def _normalize_manual_upcoming(
    entries: list[dict[str, Any]],
    *,
    esports_window_hours: int,
    music_window_days: int,
    default_window_hours: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = " ".join(str(entry.get("title") or "").split()).strip()
        raw_start = str(entry.get("start_time") or "").strip()
        if not title or not raw_start:
            continue

        domain = str(entry.get("domain") or "tech").strip().lower()
        explicit_prec = str(entry.get("precision") or "").strip()
        if explicit_prec in {"datetime", "date_only"}:
            precision = explicit_prec
        elif len(raw_start) == 10 or (
            domain == "music" and "T00:00:00" in raw_start
        ):
            precision = "date_only"
        else:
            precision = "datetime"

        norm_start = normalize_event_start_time(raw_start, precision=precision)
        if not norm_start:
            continue

        row = {
            "event_id": str(entry.get("event_id") or "")
            or compute_event_id(domain, title, norm_start),
            "domain": domain,
            "title": title,
            "subtitle": str(entry.get("subtitle") or "").strip(),
            "start_time": norm_start,
            "precision": precision,
            "tag": str(entry.get("tag") or "").strip(),
            "source": str(entry.get("source") or "manual").strip(),
            "url": str(entry.get("url") or "").strip(),
            "priority": int(entry.get("priority", 5)),
        }
        if is_upcoming_within_window(
            row,
            esports_window_hours=esports_window_hours,
            music_window_days=music_window_days,
            default_window_hours=default_window_hours,
        ):
            normalized.append(row)
    return normalized


def _upcoming_sort_key(
    row: dict[str, Any], now_utc: datetime
) -> tuple[int, str, int]:
    precision = str(row.get("precision") or "datetime")
    start_time = str(row.get("start_time") or "")
    is_live_first = 1
    if precision == "datetime":
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if dt <= now_utc:
                is_live_first = 0
        except ValueError:
            pass
    return (is_live_first, start_time, -int(row.get("priority", 3)))


def load_merged_upcoming(
    config: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    upcoming_override: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Load non-expired upcoming events merged with manual config entries."""
    up_cfg = get_upcoming_settings(config)
    esports_hours = int(up_cfg["esports_window_hours"])
    music_days = int(up_cfg["music_window_days"])
    default_hours = int(up_cfg["default_window_hours"])
    max_items = int(up_cfg["max_upcoming_items"])
    now_utc = datetime.now(timezone.utc)

    if upcoming_override is not None:
        override_rows = _normalize_manual_upcoming(
            upcoming_override,
            esports_window_hours=esports_hours,
            music_window_days=music_days,
            default_window_hours=default_hours,
        )
        override_rows.sort(key=lambda r: _upcoming_sort_key(r, now_utc))
        return [_format_upcoming_entry(r) for r in override_rows[:max_items]]

    cleanup_expired_upcoming(db_path=db_path)
    db_rows = get_upcoming_events(
        esports_window_hours=esports_hours,
        music_window_days=music_days,
        default_window_hours=default_hours,
        db_path=db_path,
    )
    manual_rows = _normalize_manual_upcoming(
        list(config.get("upcoming") or []),
        esports_window_hours=esports_hours,
        music_window_days=music_days,
        default_window_hours=default_hours,
    )

    by_id: dict[str, dict[str, Any]] = {}
    for row in db_rows:
        by_id[str(row["event_id"])] = row
    for row in manual_rows:
        by_id[str(row["event_id"])] = row

    merged = list(by_id.values())
    merged.sort(key=lambda r: _upcoming_sort_key(r, now_utc))
    return [_format_upcoming_entry(r) for r in merged[:max_items]]


def filter_upcoming(
    upcoming_entries: list[dict[str, Any]],
    window_hours: int = 48,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Filter upcoming events within [now, now + window_hours] sorted ascending."""
    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    cutoff = ref_now + timedelta(hours=window_hours)

    valid: list[tuple[datetime, dict[str, Any]]] = []
    for entry in upcoming_entries:
        if not isinstance(entry, dict):
            continue
        raw_start = str(entry.get("start_time", "")).strip()
        if not raw_start:
            continue
        try:
            start_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if ref_now <= start_dt <= cutoff:
            valid.append((start_dt, _format_upcoming_entry(entry)))

    valid.sort(key=lambda pair: pair[0])
    return [item for _, item in valid]


def format_digest_item(row: dict[str, Any]) -> dict[str, Any]:
    """Map an intel_items database row to the frontend digest.json item contract."""
    title = str(row.get("ai_title") or row.get("title") or "").strip()
    raw_summary = str(row.get("raw_summary") or "")
    summary = str(row.get("ai_summary") or _truncate_summary(raw_summary, 120)).strip()

    related_links = _parse_json_or_default(row.get("related_links"), [])
    if not isinstance(related_links, list):
        related_links = []

    spoiler_payload = _parse_json_or_default(row.get("spoiler_payload"), None)

    return {
        "id": str(row["url_hash"]),
        "domain": str(row["domain"]),
        "source_name": str(row["source_name"]),
        "title": title,
        "summary": summary,
        "url": str(row["url"]),
        "published_at": str(row["published_at"]),
        "importance": int(row.get("importance", 3)),
        "related_links": related_links,
        "spoiler_payload": spoiler_payload,
    }


def _resolve_daily_term(override: dict[str, Any] | None) -> dict[str, str]:
    candidate = override
    if not isinstance(candidate, dict) or not (
        candidate.get("term_en")
        and candidate.get("term_zh")
        and candidate.get("plain_explain")
    ):
        cached = load_daily_state()
        cached_term = cached.get("daily_term") if isinstance(cached, dict) else None
        candidate = cached_term if isinstance(cached_term, dict) else None

    if isinstance(candidate, dict) and (
        candidate.get("term_en")
        and candidate.get("term_zh")
        and candidate.get("plain_explain")
    ):
        return {
            "term_en": str(candidate["term_en"]),
            "term_zh": str(candidate["term_zh"]),
            "domain": str(candidate.get("domain") or "tech"),
            "phonetic_or_abbr": str(candidate.get("phonetic_or_abbr") or ""),
            "plain_explain": str(candidate["plain_explain"]),
            "in_context": str(candidate.get("in_context") or ""),
        }
    return dict(DEFAULT_FALLBACK_DAILY_TERM)


def export_terms_json(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = DEFAULT_TERMS_PATH,
) -> dict[str, Any]:
    """Export complete daily_terms glossary and stats to static web/public/terms.json."""
    payload = {
        "generated_at": utc_now_iso(),
        "stats": get_glossary_stats(db_path=db_path),
        "terms": get_all_daily_terms(db_path=db_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(output_path)
    return payload


def build_digest(
    config: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = DEFAULT_DIGEST_PATH,
    upcoming_override: list[dict[str, Any]] | None = None,
    top_briefings_override: list[str] | None = None,
    daily_term_override: dict[str, Any] | None = None,
    filter_enabled_sources: bool = True,
    require_processed: bool = True,
    min_importance: int | None = None,
    terms_output_path: Path | None = None,
    daily_state_path: Path | None = None,
) -> dict[str, Any]:
    """Query active intelligence from SQLite, merge clusters, and write digest.json + terms.json."""
    eff_state_path = (
        daily_state_path
        if daily_state_path is not None
        else (DAILY_STATE_PATH if db_path == DEFAULT_DB_PATH else None)
    )
    if eff_state_path is not None and eff_state_path.exists():
        backfill_daily_term_from_state(db_path, eff_state_path)

    items, _ = load_merged_digest_items(
        config,
        db_path=db_path,
        filter_enabled_sources=filter_enabled_sources,
        require_processed=require_processed,
        min_importance=min_importance,
    )

    valid_override = [
        str(line).strip()
        for line in (top_briefings_override or [])
        if str(line).strip()
    ][:3]
    top_briefings = (
        valid_override if valid_override else build_top_briefings(items, max_count=3)
    )
    daily_term = _resolve_daily_term(daily_term_override)

    upcoming = load_merged_upcoming(
        config,
        db_path=db_path,
        upcoming_override=upcoming_override,
    )
    glossary_stats = get_glossary_stats(db_path=db_path)

    digest = {
        "generated_at": utc_now_iso(),
        "top_briefings": top_briefings,
        "daily_term": daily_term,
        "glossary_stats": glossary_stats,
        "items": items,
        "upcoming": upcoming,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(digest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    eff_terms_path = (
        terms_output_path
        if terms_output_path is not None
        else (output_path.parent / "terms.json")
    )
    export_terms_json(db_path=db_path, output_path=eff_terms_path)

    return digest


def main() -> None:
    """CLI entrypoint for building digest.json from an existing SQLite DB."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Build static digest.json from SQLite intel_items."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_DIGEST_PATH)
    parser.add_argument(
        "--allow-raw",
        action="store_true",
        help="Allow exporting unprocessed raw entries (used for --skip-llm fallback).",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    digest = build_digest(
        config,
        db_path=args.db,
        output_path=args.output,
        require_processed=not args.allow_raw,
    )
    print(
        f"Built digest: {len(digest['items'])} items, "
        f"{len(digest['top_briefings'])} briefings, "
        f"{len(digest['upcoming'])} upcoming -> {args.output}"
    )


if __name__ == "__main__":
    main()
