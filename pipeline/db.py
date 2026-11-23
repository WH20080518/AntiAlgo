"""SQLite persistence layer for Anti-Algorithm Personal Hub."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pipeline import DAILY_STATE_PATH, DEFAULT_DB_PATH

TRACKING_QUERY_KEYS = frozenset(
    {"ref", "fbclid", "gclid", "mc_cid", "mc_eid", "spm"}
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intel_items (
    url_hash TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    source_name TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    ai_title TEXT,
    ai_summary TEXT,
    importance INTEGER NOT NULL DEFAULT 3,
    cluster_id TEXT,
    related_links TEXT NOT NULL DEFAULT '[]',
    spoiler_payload TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intel_published_at
    ON intel_items (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_intel_domain_published
    ON intel_items (domain, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_intel_cluster_id
    ON intel_items (cluster_id);

CREATE TABLE IF NOT EXISTS upcoming_events (
    event_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    start_time TEXT NOT NULL,
    precision TEXT NOT NULL DEFAULT 'datetime',
    tag TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    url TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 3,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_upcoming_start_time
    ON upcoming_events (start_time ASC);

CREATE TABLE IF NOT EXISTS notification_log (
    dedup_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_sent_at
    ON notification_log (sent_at DESC);

CREATE TABLE IF NOT EXISTS daily_terms (
    term_key TEXT PRIMARY KEY,
    term_en TEXT NOT NULL,
    term_zh TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'tech',
    phonetic_or_abbr TEXT NOT NULL DEFAULT '',
    plain_explain TEXT NOT NULL,
    in_context TEXT NOT NULL DEFAULT '',
    first_seen_date TEXT NOT NULL,
    last_seen_date TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    is_mastered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_terms_last_seen
    ON daily_terms (last_seen_date DESC, updated_at DESC);
"""


def utc_now_iso() -> str:
    """Return current UTC timestamp formatted as ISO-8601 Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_url(url: str) -> str:
    """Canonicalize a URL by stripping fragments, tracking params, and trailing slashes."""
    raw = url.strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    if netloc:
        host, sep, port = netloc.rpartition(":")
        if sep and port.isdigit():
            if (scheme == "http" and port == "80") or (
                scheme == "https" and port == "443"
            ):
                netloc = host

    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_QUERY_KEYS
    ]
    filtered_query.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(filtered_query, doseq=True)

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    return urlunsplit((scheme, netloc, path, query, ""))


def compute_url_hash(url: str) -> str:
    """Compute SHA-256 hex digest of the canonicalized URL."""
    canonical = normalize_url(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Initialize SQLite database file, WAL mode, and schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(SCHEMA_SQL)
        conn.commit()


def _serialize_json_field(value: Any, default: str | None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def upsert_raw_items(
    items: list[dict[str, Any]], db_path: Path = DEFAULT_DB_PATH
) -> tuple[int, int]:
    """Insert raw items via INSERT OR IGNORE; return (inserted_count, skipped_count)."""
    if not items:
        return 0, 0

    init_db(db_path)
    now_iso = utc_now_iso()
    inserted = 0

    sql = """
    INSERT OR IGNORE INTO intel_items (
        url_hash,
        domain,
        source_name,
        title,
        raw_summary,
        url,
        published_at,
        ai_title,
        ai_summary,
        importance,
        cluster_id,
        related_links,
        spoiler_payload,
        is_read,
        created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    with _connect(db_path) as conn:
        for item in items:
            raw_url = str(item.get("url", "")).strip()
            canonical_url = normalize_url(raw_url)
            url_hash = item.get("url_hash") or compute_url_hash(canonical_url)
            related_json = _serialize_json_field(item.get("related_links"), "[]") or "[]"
            spoiler_json = _serialize_json_field(item.get("spoiler_payload"), None)

            cur = conn.execute(
                sql,
                (
                    url_hash,
                    str(item.get("domain", "tech")),
                    str(item.get("source_name", "Unknown")),
                    str(item.get("title", "")).strip(),
                    str(item.get("raw_summary", "")).strip(),
                    canonical_url or raw_url,
                    str(item.get("published_at") or now_iso),
                    item.get("ai_title"),
                    item.get("ai_summary"),
                    int(item.get("importance", 3)),
                    item.get("cluster_id"),
                    related_json,
                    spoiler_json,
                    int(item.get("is_read", 0)),
                    str(item.get("created_at") or now_iso),
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
        conn.commit()

    skipped = len(items) - inserted
    return inserted, skipped


def _cutoff_iso(max_age_hours: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_source_filter(
    enabled_sources: set[str] | None,
) -> tuple[str, list[Any]]:
    if enabled_sources is None:
        return "", []
    ordered = sorted(enabled_sources)
    placeholders = ",".join("?" for _ in ordered)
    return f" AND source_name IN ({placeholders})", ordered


def get_recent_items(
    max_age_hours: int = 72,
    limit: int | None = 40,
    enabled_sources: set[str] | None = None,
    min_importance: int = 1,
    require_processed: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Fetch active items published within max_age_hours ordered by importance and recency."""
    if not db_path.exists():
        return []
    if enabled_sources is not None and not enabled_sources:
        return []

    cutoff = _cutoff_iso(max_age_hours)
    src_clause, src_params = _build_source_filter(enabled_sources)
    processed_clause = (
        " AND ai_summary IS NOT NULL AND ai_summary != ''"
        if require_processed
        else ""
    )
    sql = f"""
    SELECT *
    FROM intel_items
    WHERE importance >= ?
      AND published_at >= ?{processed_clause}{src_clause}
    ORDER BY importance DESC, published_at DESC
    """
    params: list[Any] = [int(min_importance), cutoff, *src_params]
    if limit is not None:
        sql += "\n    LIMIT ?"
        params.append(limit)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_unprocessed_items(
    max_age_hours: int = 72,
    limit: int | None = None,
    enabled_sources: set[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Fetch active items that have not yet been processed by the LLM stage."""
    if not db_path.exists():
        return []
    if enabled_sources is not None and not enabled_sources:
        return []

    cutoff = _cutoff_iso(max_age_hours)
    src_clause, src_params = _build_source_filter(enabled_sources)
    sql = f"""
    SELECT *
    FROM intel_items
    WHERE ai_summary IS NULL
      AND importance > 0
      AND published_at >= ?{src_clause}
    ORDER BY published_at DESC
    """
    params: list[Any] = [cutoff, *src_params]
    if limit is not None:
        sql += "\n    LIMIT ?"
        params.append(limit)

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def reset_processed_items(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Clear previously LLM-processed columns so active entries can be re-evaluated."""
    if not db_path.exists():
        return 0

    sql = """
    UPDATE intel_items
    SET ai_title = NULL,
        ai_summary = NULL,
        importance = 3,
        cluster_id = NULL,
        spoiler_payload = NULL
    WHERE ai_summary IS NOT NULL
    """
    with _connect(db_path) as conn:
        cur = conn.execute(sql)
        conn.commit()
        return int(cur.rowcount or 0)


def mark_items_filtered(
    url_hashes: list[str], db_path: Path = DEFAULT_DB_PATH
) -> int:
    """Mark hard-filtered items with importance=0 so they are skipped in LLM & digest."""
    if not url_hashes or not db_path.exists():
        return 0

    placeholders = ",".join("?" for _ in url_hashes)
    sql = f"UPDATE intel_items SET importance = 0 WHERE url_hash IN ({placeholders})"
    with _connect(db_path) as conn:
        cur = conn.execute(sql, url_hashes)
        conn.commit()
        return int(cur.rowcount or 0)


def get_recent_clusters(
    max_age_hours: int = 72,
    limit: int = 25,
    enabled_sources: set[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, str]]:
    """Return distinct recent cluster slugs for cross-batch/cross-run event merging."""
    if not db_path.exists():
        return []
    if enabled_sources is not None and not enabled_sources:
        return []

    cutoff = _cutoff_iso(max_age_hours)
    src_clause, src_params = _build_source_filter(enabled_sources)
    sql = f"""
    SELECT cluster_id,
           MAX(domain) AS domain,
           MAX(COALESCE(ai_title, title)) AS title
    FROM intel_items
    WHERE cluster_id IS NOT NULL
      AND cluster_id != ''
      AND importance > 0
      AND published_at >= ?{src_clause}
    GROUP BY cluster_id
    ORDER BY MAX(published_at) DESC
    LIMIT ?
    """
    with _connect(db_path) as conn:
        rows = conn.execute(sql, (cutoff, *src_params, limit)).fetchall()
    return [
        {
            "cluster_id": str(row["cluster_id"]),
            "domain": str(row["domain"]),
            "title": str(row["title"]),
        }
        for row in rows
    ]


def get_items_by_hashes(
    url_hashes: list[str], db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    """Fetch full rows for a list of url_hash identifiers."""
    if not url_hashes or not db_path.exists():
        return []

    placeholders = ",".join("?" for _ in url_hashes)
    sql = f"SELECT * FROM intel_items WHERE url_hash IN ({placeholders})"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, url_hashes).fetchall()
    return [dict(row) for row in rows]


def update_item_processing(
    url_hash: str,
    *,
    ai_title: str | None = None,
    ai_summary: str | None = None,
    importance: int = 3,
    cluster_id: str | None = None,
    related_links: list[str] | str | None = None,
    spoiler_payload: dict[str, Any] | list[Any] | str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Update LLM-processed fields for an existing item by url_hash."""
    related_json = _serialize_json_field(related_links, "[]") or "[]"
    spoiler_json = _serialize_json_field(spoiler_payload, None)

    sql = """
    UPDATE intel_items
    SET ai_title = ?,
        ai_summary = ?,
        importance = ?,
        cluster_id = ?,
        related_links = ?,
        spoiler_payload = ?
    WHERE url_hash = ?
    """
    with _connect(db_path) as conn:
        conn.execute(
            sql,
            (
                ai_title,
                ai_summary,
                int(importance),
                cluster_id,
                related_json,
                spoiler_json,
                url_hash,
            ),
        )
        conn.commit()


def normalize_event_start_time(
    raw_start: str, precision: str = "datetime"
) -> str | None:
    """Normalize event timestamp/date string to ISO-8601 UTC format."""
    cleaned = str(raw_start or "").strip()
    if not cleaned:
        return None

    prec = "date_only" if str(precision).strip() == "date_only" else "datetime"
    if prec == "date_only":
        date_prefix = cleaned[:10]
        try:
            parsed_date = date.fromisoformat(date_prefix)
            return f"{parsed_date.isoformat()}T00:00:00Z"
        except ValueError:
            pass

    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        if prec == "date_only":
            return f"{dt_utc.date().isoformat()}T00:00:00Z"
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def compute_event_id(domain: str, title: str, start_time: str) -> str:
    """Compute SHA-256 event_id from domain, normalized title, and YYYY-MM-DD date."""
    norm_domain = str(domain or "tech").strip().lower()
    norm_title = " ".join(str(title or "").lower().split()).strip()
    date_part = str(start_time or "").strip()[:10]
    raw = f"{norm_domain}|{norm_title}|{date_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upsert_upcoming_events(
    events: list[dict[str, Any]], db_path: Path = DEFAULT_DB_PATH
) -> int:
    """Idempotently insert or update upcoming_events rows."""
    if not events:
        return 0

    init_db(db_path)
    now_iso = utc_now_iso()
    written = 0

    sql = """
    INSERT INTO upcoming_events (
        event_id,
        domain,
        title,
        subtitle,
        start_time,
        precision,
        tag,
        source,
        url,
        priority,
        updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO UPDATE SET
        title = excluded.title,
        subtitle = CASE
            WHEN excluded.subtitle != '' THEN excluded.subtitle
            ELSE upcoming_events.subtitle
        END,
        start_time = excluded.start_time,
        precision = excluded.precision,
        tag = CASE
            WHEN excluded.tag != '' THEN excluded.tag
            ELSE upcoming_events.tag
        END,
        source = excluded.source,
        url = CASE
            WHEN excluded.url != '' THEN excluded.url
            ELSE upcoming_events.url
        END,
        priority = MAX(upcoming_events.priority, excluded.priority),
        updated_at = excluded.updated_at
    """

    with _connect(db_path) as conn:
        for ev in events:
            title = " ".join(str(ev.get("title") or "").split()).strip()
            domain = str(ev.get("domain") or "tech").strip().lower()
            precision = (
                "date_only"
                if str(ev.get("precision") or "").strip() == "date_only"
                else "datetime"
            )
            norm_start = normalize_event_start_time(
                str(ev.get("start_time") or ""), precision=precision
            )
            if not title or not norm_start:
                continue

            event_id = str(ev.get("event_id") or "") or compute_event_id(
                domain, title, norm_start
            )
            conn.execute(
                sql,
                (
                    event_id,
                    domain,
                    title,
                    " ".join(str(ev.get("subtitle") or "").split()).strip(),
                    norm_start,
                    precision,
                    str(ev.get("tag") or "").strip(),
                    str(ev.get("source") or "manual").strip(),
                    str(ev.get("url") or "").strip(),
                    int(ev.get("priority", 3)),
                    now_iso,
                ),
            )
            written += 1
        conn.commit()
    return written


def cleanup_expired_upcoming(
    db_path: Path = DEFAULT_DB_PATH,
    now_utc: datetime | None = None,
    today_local: date | None = None,
) -> int:
    """Remove expired upcoming_events (datetime >3h past start; date_only < today)."""
    if not db_path.exists():
        return 0

    ref_now = now_utc or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    dt_cutoff = (ref_now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    ref_today = today_local or datetime.now().astimezone().date()
    date_cutoff = ref_today.isoformat()

    with _connect(db_path) as conn:
        cur1 = conn.execute(
            "DELETE FROM upcoming_events WHERE precision = 'datetime' AND start_time < ?",
            (dt_cutoff,),
        )
        cur2 = conn.execute(
            "DELETE FROM upcoming_events WHERE precision = 'date_only' AND substr(start_time, 1, 10) < ?",
            (date_cutoff,),
        )
        conn.commit()
        return int((cur1.rowcount or 0) + (cur2.rowcount or 0))


def is_upcoming_within_window(
    row: dict[str, Any],
    *,
    esports_window_hours: int = 48,
    music_window_days: int = 14,
    default_window_hours: int = 72,
    now_utc: datetime | None = None,
    today_local: date | None = None,
) -> bool:
    """Check whether an upcoming event fits its domain-specific asymmetric window."""
    ref_now = now_utc or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    ref_today = today_local or datetime.now().astimezone().date()

    domain = str(row.get("domain") or "tech").strip().lower()
    precision = (
        "date_only"
        if str(row.get("precision") or "").strip() == "date_only"
        else "datetime"
    )
    raw_start = str(row.get("start_time") or "").strip()
    if not raw_start:
        return False

    if precision == "date_only":
        try:
            event_date = date.fromisoformat(raw_start[:10])
        except ValueError:
            return False
        if event_date < ref_today:
            return False
        if domain == "music":
            end_date = ref_today + timedelta(days=music_window_days)
        elif domain == "esports":
            end_date = ref_today + timedelta(
                days=max(1, (esports_window_hours + 23) // 24)
            )
        else:
            end_date = ref_today + timedelta(
                days=max(1, (default_window_hours + 23) // 24)
            )
        return event_date <= end_date

    try:
        start_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        start_dt = start_dt.astimezone(timezone.utc)
    except ValueError:
        return False

    if start_dt < ref_now - timedelta(hours=3):
        return False

    if domain == "esports":
        end_dt = ref_now + timedelta(hours=esports_window_hours)
    elif domain == "music":
        end_dt = ref_now + timedelta(days=music_window_days)
    else:
        end_dt = ref_now + timedelta(hours=default_window_hours)
    return start_dt <= end_dt


def get_upcoming_events(
    esports_window_hours: int = 48,
    music_window_days: int = 14,
    default_window_hours: int = 72,
    db_path: Path = DEFAULT_DB_PATH,
    now_utc: datetime | None = None,
    today_local: date | None = None,
) -> list[dict[str, Any]]:
    """Query active upcoming_events filtered by asymmetric domain windows."""
    if not db_path.exists():
        return []

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM upcoming_events ORDER BY start_time ASC, priority DESC"
        ).fetchall()

    return [
        dict(row)
        for row in rows
        if is_upcoming_within_window(
            dict(row),
            esports_window_hours=esports_window_hours,
            music_window_days=music_window_days,
            default_window_hours=default_window_hours,
            now_utc=now_utc,
            today_local=today_local,
        )
    ]


def has_been_notified(dedup_key: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Return True if dedup_key is already recorded in notification_log."""
    if not dedup_key or not db_path.exists():
        return False
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE dedup_key = ? LIMIT 1",
            (dedup_key,),
        ).fetchone()
    return row is not None


def record_notification(
    dedup_key: str,
    category: str,
    title: str,
    sent_at: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Idempotently record one sent notification into notification_log."""
    return (
        record_notifications_batch(
            [(dedup_key, category, title)],
            sent_at=sent_at,
            db_path=db_path,
        )
        > 0
    )


def record_notifications_batch(
    entries: list[tuple[str, str, str]],
    sent_at: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Idempotently batch-insert notification records; return count of new rows."""
    cleaned = [
        (str(k).strip(), str(cat).strip(), str(t).strip())
        for k, cat, t in entries
        if str(k).strip()
    ]
    if not cleaned:
        return 0

    init_db(db_path)
    ts = sent_at or utc_now_iso()
    inserted = 0
    sql = """
    INSERT OR IGNORE INTO notification_log (
        dedup_key,
        category,
        title,
        sent_at
    ) VALUES (?, ?, ?, ?)
    """
    with _connect(db_path) as conn:
        for dedup_key, category, title in cleaned:
            cur = conn.execute(sql, (dedup_key, category, title, ts))
            if cur.rowcount == 1:
                inserted += 1
        conn.commit()
    return inserted


def cleanup_old_notifications(
    max_age_days: int = 30,
    db_path: Path = DEFAULT_DB_PATH,
    now_utc: datetime | None = None,
) -> int:
    """Delete notification_log entries older than max_age_days."""
    if not db_path.exists():
        return 0
    init_db(db_path)
    ref_now = now_utc or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    cutoff = (ref_now - timedelta(days=max_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM notification_log WHERE sent_at < ?",
            (cutoff,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def get_last_notification_sent_at(db_path: Path = DEFAULT_DB_PATH) -> str | None:
    """Return latest sent_at timestamp from notification_log, or None."""
    if not db_path.exists():
        return None
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT MAX(sent_at) AS last_sent FROM notification_log").fetchone()
    if row and row["last_sent"]:
        return str(row["last_sent"])
    return None


def get_recent_music_releases(
    *,
    since_published_iso: str,
    enabled_since_iso: str,
    min_importance: int = 4,
    enabled_sources: set[str] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Query processed high-signal music items published & ingested after cold-start."""
    if not db_path.exists():
        return []
    if enabled_sources is not None and not enabled_sources:
        return []

    init_db(db_path)
    src_clause, src_params = _build_source_filter(enabled_sources)
    sql = f"""
    SELECT *
    FROM intel_items
    WHERE domain = 'music'
      AND importance >= ?
      AND ai_summary IS NOT NULL
      AND ai_summary != ''
      AND published_at >= ?
      AND created_at >= ?{src_clause}
    ORDER BY importance DESC, published_at DESC
    """
    params: list[Any] = [
        int(min_importance),
        since_published_iso,
        enabled_since_iso,
        *src_params,
    ]
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def normalize_term_key(term_en: str) -> str:
    """Normalize an English term into a whitespace-free lowercase primary key."""
    return re.sub(r"\s+", "", str(term_en or "").strip().lower())


def _normalize_term_fields(term_dict: dict[str, Any]) -> dict[str, str] | None:
    if not isinstance(term_dict, dict):
        return None
    term_en = " ".join(str(term_dict.get("term_en") or "").split()).strip()
    term_zh = " ".join(str(term_dict.get("term_zh") or "").split()).strip()
    plain_explain = " ".join(str(term_dict.get("plain_explain") or "").split()).strip()
    in_context = " ".join(str(term_dict.get("in_context") or "").split()).strip()
    if not (term_en and term_zh and plain_explain):
        return None

    raw_domain = str(term_dict.get("domain") or "tech").strip().lower()
    domain = raw_domain if raw_domain in {"tech", "esports", "music"} else "tech"
    abbr = " ".join(str(term_dict.get("phonetic_or_abbr") or "").split()).strip()

    term_key = normalize_term_key(term_en)
    if not term_key:
        return None
    return {
        "term_key": term_key,
        "term_en": term_en,
        "term_zh": term_zh,
        "domain": domain,
        "phonetic_or_abbr": abbr,
        "plain_explain": plain_explain,
        "in_context": in_context,
    }


def _today_local_date_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def upsert_daily_term(
    term_dict: dict[str, Any],
    term_date: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Idempotently upsert a daily_term; increment seen_count & preserve is_mastered."""
    cleaned = _normalize_term_fields(term_dict)
    if cleaned is None:
        return None

    init_db(db_path)
    eff_date = str(term_date or "").strip()[:10] or _today_local_date_str()
    now_iso = utc_now_iso()

    sql = """
    INSERT INTO daily_terms (
        term_key,
        term_en,
        term_zh,
        domain,
        phonetic_or_abbr,
        plain_explain,
        in_context,
        first_seen_date,
        last_seen_date,
        seen_count,
        is_mastered,
        created_at,
        updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
    ON CONFLICT(term_key) DO UPDATE SET
        term_en = excluded.term_en,
        term_zh = excluded.term_zh,
        domain = excluded.domain,
        phonetic_or_abbr = CASE
            WHEN excluded.phonetic_or_abbr != '' THEN excluded.phonetic_or_abbr
            ELSE daily_terms.phonetic_or_abbr
        END,
        plain_explain = excluded.plain_explain,
        in_context = CASE
            WHEN excluded.in_context != '' THEN excluded.in_context
            ELSE daily_terms.in_context
        END,
        first_seen_date = MIN(daily_terms.first_seen_date, excluded.first_seen_date),
        last_seen_date = MAX(daily_terms.last_seen_date, excluded.last_seen_date),
        seen_count = daily_terms.seen_count + 1,
        updated_at = excluded.updated_at
    """
    with _connect(db_path) as conn:
        conn.execute(
            sql,
            (
                cleaned["term_key"],
                cleaned["term_en"],
                cleaned["term_zh"],
                cleaned["domain"],
                cleaned["phonetic_or_abbr"],
                cleaned["plain_explain"],
                cleaned["in_context"],
                eff_date,
                eff_date,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    return cleaned["term_key"]


def get_recent_term_names(
    days: int = 30,
    db_path: Path = DEFAULT_DB_PATH,
    today_local: date | None = None,
) -> list[str]:
    """Return term_en list seen within the last `days` days for LLM anti-repetition."""
    if not db_path.exists():
        return []
    init_db(db_path)
    ref_today = today_local or datetime.now().astimezone().date()
    cutoff_date = (ref_today - timedelta(days=max(0, int(days)))).isoformat()

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT term_en
            FROM daily_terms
            WHERE last_seen_date >= ?
            ORDER BY last_seen_date DESC, updated_at DESC
            """,
            (cutoff_date,),
        ).fetchall()
    return [str(row["term_en"]) for row in rows if row["term_en"]]


def get_all_daily_terms(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Return complete daily_terms list ordered by recency."""
    if not db_path.exists():
        return []
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT term_key,
                   term_en,
                   term_zh,
                   domain,
                   phonetic_or_abbr,
                   plain_explain,
                   in_context,
                   first_seen_date,
                   last_seen_date,
                   seen_count,
                   is_mastered,
                   created_at,
                   updated_at
            FROM daily_terms
            ORDER BY last_seen_date DESC, updated_at DESC
            """
        ).fetchall()

    terms: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["seen_count"] = int(item.get("seen_count") or 1)
        item["is_mastered"] = bool(item.get("is_mastered"))
        terms.append(item)
    return terms


def set_term_mastered(
    term_key: str,
    mastered: bool,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Update is_mastered flag for an existing daily term; return True if updated."""
    if not db_path.exists():
        return False
    norm_key = normalize_term_key(term_key)
    raw_key = str(term_key or "").strip()
    if not norm_key and not raw_key:
        return False

    init_db(db_path)
    now_iso = utc_now_iso()
    mastered_val = 1 if mastered else 0
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE daily_terms
            SET is_mastered = ?,
                updated_at = ?
            WHERE term_key = ? OR term_key = ?
            """,
            (mastered_val, now_iso, raw_key, norm_key),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0


def get_glossary_stats(db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Return lightweight {total, mastered} counts from daily_terms."""
    if not db_path.exists():
        return {"total": 0, "mastered": 0}
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN is_mastered != 0 THEN 1 ELSE 0 END), 0) AS mastered
            FROM daily_terms
            """
        ).fetchone()
    if row is None:
        return {"total": 0, "mastered": 0}
    return {
        "total": int(row["total"] or 0),
        "mastered": int(row["mastered"] or 0),
    }


def _backfill_on_connection(
    conn: sqlite3.Connection, state_path: Path
) -> bool:
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    cleaned = _normalize_term_fields(data.get("daily_term"))
    if cleaned is None:
        return False

    existing = conn.execute(
        "SELECT 1 FROM daily_terms WHERE term_key = ? LIMIT 1",
        (cleaned["term_key"],),
    ).fetchone()
    if existing is not None:
        return False

    eff_date = str(data.get("date") or "").strip()[:10] or _today_local_date_str()
    now_iso = utc_now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO daily_terms (
            term_key,
            term_en,
            term_zh,
            domain,
            phonetic_or_abbr,
            plain_explain,
            in_context,
            first_seen_date,
            last_seen_date,
            seen_count,
            is_mastered,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
        """,
        (
            cleaned["term_key"],
            cleaned["term_en"],
            cleaned["term_zh"],
            cleaned["domain"],
            cleaned["phonetic_or_abbr"],
            cleaned["plain_explain"],
            cleaned["in_context"],
            eff_date,
            eff_date,
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    return True


def backfill_daily_term_from_state(
    conn: sqlite3.Connection | Path = DEFAULT_DB_PATH,
    state_path: Path = DAILY_STATE_PATH,
) -> bool:
    """Backfill historical daily_term from data/daily_state.json into daily_terms if missing."""
    if not state_path.exists():
        return False

    if isinstance(conn, sqlite3.Connection):
        conn.executescript(SCHEMA_SQL)
        return _backfill_on_connection(conn, state_path)

    db_path = Path(conn)
    init_db(db_path)
    with _connect(db_path) as db_conn:
        return _backfill_on_connection(db_conn, state_path)
