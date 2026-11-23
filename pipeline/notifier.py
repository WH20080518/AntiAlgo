"""Zero-console native Windows Toast notification engine for Anti-Algorithm Personal Hub."""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape

from dotenv import load_dotenv
import httpx

from pipeline import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    NOTIFY_STATE_PATH,
    ROOT_DIR,
    get_enabled_source_names,
    get_focus_entities,
    get_notification_settings,
    get_upcoming_settings,
    get_webhook_settings,
    load_config,
)
from pipeline.db import (
    cleanup_old_notifications,
    get_last_notification_sent_at,
    get_recent_items,
    get_recent_music_releases,
    get_upcoming_events,
    has_been_notified,
    record_notifications_batch,
    utc_now_iso,
)

DEFAULT_HUB_URL = "http://127.0.0.1:8765"
POWERSHELL_AUMID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)
INVALID_XML_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]")
ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]")

WEBHOOK_TIMEOUT_SECONDS = 10.0
WEBHOOK_MAX_SENT_IDS = 500
BARK_BODY_MAX_CHARS = 450
WEBHOOK_ENV_VARS: dict[str, str] = {
    "feishu": "WEBHOOK_FEISHU_URL",
    "discord": "WEBHOOK_DISCORD_URL",
    "bark": "WEBHOOK_BARK_URL",
    "custom": "WEBHOOK_CUSTOM_URL",
}
DOMAIN_LABELS_ZH: dict[str, str] = {
    "tech": "AI 与开源",
    "esports": "CS2 赛事",
    "music": "音乐动态",
}

_STATE_LOCK = threading.Lock()

ToastSender = Callable[[str, str, str, bool], tuple[bool, str | None] | bool]
WebhookSender = Callable[[str, str, dict[str, Any], float], tuple[bool, str | None]]


def _sanitize_xml_text(raw: str) -> str:
    cleaned = INVALID_XML_CHAR_RE.sub("", str(raw or ""))
    return xml_escape(cleaned.strip(), {'"': "&quot;", "'": "&apos;"})


def _safe_launch_url(url: str | None, fallback: str = DEFAULT_HUB_URL) -> str:
    candidate = str(url or "").strip()
    lowered = candidate.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        chosen = candidate
    else:
        chosen = fallback
    cleaned = INVALID_XML_CHAR_RE.sub("", chosen)
    return xml_escape(cleaned, {'"': "&quot;", "'": "&apos;"})


def build_toast_xml(
    title: str,
    body: str,
    url: str = DEFAULT_HUB_URL,
    *,
    silent: bool = True,
) -> str:
    """Build valid escaped WinRT ToastGeneric XML with optional silent audio tag."""
    safe_title = _sanitize_xml_text(title) or "反算法个人情报站"
    safe_body = _sanitize_xml_text(body)
    safe_url = _safe_launch_url(url)
    audio_xml = '<audio silent="true"/>' if silent else ""

    return (
        f'<toast activationType="protocol" launch="{safe_url}">'
        "<visual>"
        '<binding template="ToastGeneric">'
        f"<text>{safe_title}</text>"
        f"<text>{safe_body}</text>"
        "</binding>"
        "</visual>"
        f"{audio_xml}"
        "</toast>"
    )


def _build_powershell_toast_script(toast_xml: str, app_id: str = POWERSHELL_AUMID) -> str:
    xml_b64 = base64.b64encode(toast_xml.encode("utf-8")).decode("ascii")
    aumid_b64 = base64.b64encode(app_id.encode("utf-8")).decode("ascii")
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        f"$xmlText = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{xml_b64}'))\n"
        f"$appId = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{aumid_b64}'))\n"
        "$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        "$xmlDoc.LoadXml($xmlText)\n"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDoc)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)\n"
    )


def send_windows_toast(
    title: str,
    body: str,
    url: str = DEFAULT_HUB_URL,
    *,
    silent: bool = True,
    timeout_seconds: float = 15.0,
) -> tuple[bool, str | None]:
    """Deliver a native Windows 10/11 Toast notification without flashing a console."""
    if sys.platform != "win32":
        return False, f"Unsupported platform for WinRT Toast: {sys.platform}"

    toast_xml = build_toast_xml(title, body, url=url, silent=silent)
    ps_script = _build_powershell_toast_script(toast_xml)
    encoded_cmd = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_cmd,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"exit_code={proc.returncode}").strip()
        return False, err[:400]
    return True, None


def _normalize_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return _normalize_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_notify_state(
    state: dict[str, Any], state_path: Path = NOTIFY_STATE_PATH
) -> None:
    """Persist runtime notification toggle and cold-start enabled_since timestamp."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(state_path)


def _normalize_webhook_sent_ids(raw_ids: Any) -> list[str]:
    if not isinstance(raw_ids, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in raw_ids:
        cleaned = str(entry or "").strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped[-WEBHOOK_MAX_SENT_IDS:]


def _load_notify_state_unlocked(
    state_path: Path = NOTIFY_STATE_PATH,
    *,
    default_enabled: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now_iso = _format_utc(_normalize_utc(now_utc))
    dirty = False
    loaded: dict[str, Any] = {}
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                loaded = raw
            else:
                dirty = True
        except (OSError, json.JSONDecodeError):
            dirty = True
    else:
        dirty = True

    enabled_since = str(loaded.get("enabled_since") or "").strip()
    if not enabled_since:
        enabled_since = now_iso
        dirty = True

    enabled = loaded.get("enabled")
    if not isinstance(enabled, bool):
        enabled = bool(default_enabled)
        dirty = True

    raw_wh_ids = loaded.get("webhook_sent_ids")
    webhook_sent_ids = _normalize_webhook_sent_ids(raw_wh_ids)
    if not isinstance(raw_wh_ids, list) or len(raw_wh_ids) != len(webhook_sent_ids):
        dirty = True

    raw_digest_date = loaded.get("webhook_last_digest_date")
    webhook_last_digest_date = (
        str(raw_digest_date).strip() if raw_digest_date and str(raw_digest_date).strip() else None
    )
    raw_wh_sent_at = loaded.get("webhook_last_sent_at")
    webhook_last_sent_at = (
        str(raw_wh_sent_at).strip() if raw_wh_sent_at and str(raw_wh_sent_at).strip() else None
    )

    state: dict[str, Any] = {
        "enabled": enabled,
        "enabled_since": enabled_since,
        "last_sent_at": loaded.get("last_sent_at"),
        "last_title": loaded.get("last_title"),
        "webhook_sent_ids": webhook_sent_ids,
        "webhook_last_digest_date": webhook_last_digest_date,
        "webhook_last_sent_at": webhook_last_sent_at,
    }
    if dirty:
        save_notify_state(state, state_path=state_path)
    return state


def load_notify_state(
    state_path: Path = NOTIFY_STATE_PATH,
    *,
    default_enabled: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Load or cold-start initialize data/notify_state.json."""
    with _STATE_LOCK:
        return _load_notify_state_unlocked(
            state_path=state_path,
            default_enabled=default_enabled,
            now_utc=now_utc,
        )


def toggle_notify_state(
    state_path: Path = NOTIFY_STATE_PATH,
    *,
    enabled: bool | None = None,
    default_enabled: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Toggle or explicitly set runtime notification enabled flag and persist."""
    with _STATE_LOCK:
        state = _load_notify_state_unlocked(
            state_path=state_path,
            default_enabled=default_enabled,
            now_utc=now_utc,
        )
        next_enabled = (not bool(state["enabled"])) if enabled is None else bool(enabled)
        state["enabled"] = next_enabled
        save_notify_state(state, state_path=state_path)
        return dict(state)


def _update_last_sent(
    state_path: Path,
    *,
    sent_at: str,
    last_title: str,
    default_enabled: bool = True,
) -> None:
    with _STATE_LOCK:
        state = _load_notify_state_unlocked(
            state_path=state_path, default_enabled=default_enabled
        )
        state["last_sent_at"] = sent_at
        state["last_title"] = last_title
        save_notify_state(state, state_path=state_path)


def record_webhook_sent_ids(
    ids: list[str],
    *,
    state_path: Path = NOTIFY_STATE_PATH,
    sent_at: str | None = None,
    digest_date: str | None = None,
    default_enabled: bool = True,
) -> None:
    """Append deduplicated Webhook IDs and metadata to notify_state.json."""
    cleaned_new = [str(entry or "").strip() for entry in ids if str(entry or "").strip()]
    with _STATE_LOCK:
        state = _load_notify_state_unlocked(
            state_path=state_path, default_enabled=default_enabled
        )
        merged = list(state.get("webhook_sent_ids") or [])
        merged.extend(cleaned_new)
        state["webhook_sent_ids"] = _normalize_webhook_sent_ids(merged)
        if sent_at:
            state["webhook_last_sent_at"] = sent_at
        if digest_date:
            state["webhook_last_digest_date"] = digest_date
        save_notify_state(state, state_path=state_path)


def _parse_hhmm_minutes(text: str) -> int | None:
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def is_in_quiet_hours(
    quiet_hours: list[str] | tuple[str, str] | None,
    now_local: datetime | None = None,
) -> bool:
    """Return True when local time falls inside [start, end), handling cross-midnight."""
    if not quiet_hours or len(quiet_hours) < 2:
        return False

    start_min = _parse_hhmm_minutes(quiet_hours[0])
    end_min = _parse_hhmm_minutes(quiet_hours[1])
    if start_min is None or end_min is None or start_min == end_min:
        return False

    ref = now_local if now_local is not None else datetime.now().astimezone()
    cur_min = ref.hour * 60 + ref.minute
    if start_min < end_min:
        return start_min <= cur_min < end_min
    return cur_min >= start_min or cur_min < end_min


def matches_focus_esports(
    event: dict[str, Any], focus_teams: list[str] | None
) -> bool:
    """Match high-priority events or titles/subtitles mentioning focus esports entities."""
    try:
        if int(event.get("priority") or 0) >= 4:
            return True
    except (TypeError, ValueError):
        pass

    entities = [str(e).strip() for e in (focus_teams or []) if str(e).strip()]
    if not entities:
        return True

    haystack = f"{event.get('title', '')} {event.get('subtitle', '')}"
    lowered = haystack.lower()
    for entity in entities:
        if ASCII_WORD_RE.search(entity):
            pattern = rf"(?<![A-Za-z0-9]){re.escape(entity)}(?![A-Za-z0-9])"
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                return True
        else:
            if entity.lower() in lowered:
                return True
    return False


def _invoke_sender(
    sender: ToastSender | None,
    title: str,
    body: str,
    url: str,
    silent: bool,
) -> tuple[bool, str | None]:
    if sender is None:
        return send_windows_toast(title, body, url=url, silent=silent)
    result = sender(title, body, url, silent)
    if isinstance(result, tuple):
        return bool(result[0]), result[1]
    return bool(result), (None if result else "Mock sender returned False")


def send_test_toast(
    *,
    hub_url: str = DEFAULT_HUB_URL,
    silent: bool = True,
    sender: ToastSender | None = None,
) -> tuple[bool, str | None]:
    """Send a silent diagnostic test Toast without touching notification_log."""
    return _invoke_sender(
        sender,
        "反算法个人情报站 · 静音测试通知",
        "桌面静音提醒通道运行正常，点击可直接打开本地情报看板。",
        hub_url,
        silent,
    )


def _parse_iso_utc(raw: str) -> datetime | None:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return None
    try:
        dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return _normalize_utc(dt)
    except ValueError:
        return None


def _build_merged_music_toast(
    candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    count = len(candidates)
    title = f"音乐动态汇总 · 新增 {count} 条发行与新歌"
    preview_titles = [
        str(c.get("display_title") or c.get("record_title") or "").strip()
        for c in candidates[:3]
    ]
    preview_titles = [t for t in preview_titles if t]
    joined = " / ".join(preview_titles)
    if count > 3:
        body = f"{joined} 等 {count} 条动态，点击查看详情"
    else:
        body = joined or f"共有 {count} 条关注艺人发行与新歌，点击查看详情"
    return title, body


def check_and_notify(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    state_path: Path = NOTIFY_STATE_PATH,
    hub_url: str = DEFAULT_HUB_URL,
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
    sender: ToastSender | None = None,
) -> dict[str, Any]:
    """Evaluate CS2 pre-match and music release triggers and deliver silent Toasts."""
    config = load_config(config_path) if config_path.exists() else {}
    settings = get_notification_settings(config)

    ref_utc = _normalize_utc(now_utc)
    if now_local is not None:
        ref_local = now_local
    elif now_utc is not None:
        ref_local = ref_utc.astimezone()
    else:
        ref_local = datetime.now().astimezone()
    ref_today: date = ref_local.date()
    now_utc_iso = _format_utc(ref_utc)

    state = load_notify_state(
        state_path=state_path,
        default_enabled=settings["enabled"],
        now_utc=ref_utc,
    )
    cleaned_old = cleanup_old_notifications(
        max_age_days=30, db_path=db_path, now_utc=ref_utc
    )

    result: dict[str, Any] = {
        "sent_count": 0,
        "cs2_sent": 0,
        "music_sent": 0,
        "music_merged_count": 0,
        "cleaned_old": cleaned_old,
        "skipped_reason": None,
        "errors": [],
    }

    if not settings["enabled"] or not state["enabled"]:
        result["skipped_reason"] = "disabled"
    else:
        in_quiet = is_in_quiet_hours(settings["quiet_hours"], now_local=ref_local)
        silent_mode = bool(settings["silent"])
        focus_entities = get_focus_entities(config)
        esports_focus = focus_entities.get("esports", [])
        upcoming_cfg = get_upcoming_settings(config)
        enabled_sources = get_enabled_source_names(config)

        upcoming_rows = get_upcoming_events(
            esports_window_hours=upcoming_cfg["esports_window_hours"],
            music_window_days=upcoming_cfg["music_window_days"],
            default_window_hours=upcoming_cfg["default_window_hours"],
            db_path=db_path,
            now_utc=ref_utc,
            today_local=ref_today,
        )

        last_sent_title: str | None = None

        # Rule A: CS2 15-minute pre-match reminders (spoiler-free)
        allow_cs2 = (not in_quiet) or bool(settings["cs2_bypass_quiet_hours"])
        if allow_cs2:
            max_lead = float(settings["cs2_lead_minutes"] + 1)
            for ev in upcoming_rows:
                if (
                    str(ev.get("domain") or "").strip().lower() != "esports"
                    or str(ev.get("precision") or "").strip() != "datetime"
                ):
                    continue

                start_dt = _parse_iso_utc(str(ev.get("start_time") or ""))
                if start_dt is None:
                    continue

                minutes_until = (start_dt - ref_utc).total_seconds() / 60.0
                if not (1.0 <= minutes_until <= max_lead):
                    continue

                if settings["cs2_only_focus_teams"] and not matches_focus_esports(
                    ev, esports_focus
                ):
                    continue

                event_id = str(ev.get("event_id") or "").strip()
                if not event_id:
                    continue
                dedup_key = f"cs2_15m:{event_id}"
                if has_been_notified(dedup_key, db_path=db_path):
                    continue

                mins_display = max(1, round(minutes_until))
                match_title = str(ev.get("title") or "CS2 焦点赛事").strip()
                subtitle = str(ev.get("subtitle") or "").strip()
                toast_title = f"CS2 赛前提醒 · 约 {mins_display} 分钟后开赛"
                toast_body = f"{match_title}（{subtitle}）" if subtitle else match_title
                target_url = str(ev.get("url") or "").strip() or hub_url

                ok, err = _invoke_sender(
                    sender, toast_title, toast_body, target_url, silent_mode
                )
                if ok:
                    record_notifications_batch(
                        [(dedup_key, "cs2_15m", match_title)],
                        sent_at=now_utc_iso,
                        db_path=db_path,
                    )
                    result["sent_count"] += 1
                    result["cs2_sent"] += 1
                    last_sent_title = toast_title
                elif err:
                    result["errors"].append(err)

        # Rule B: Music high-signal releases & today's scheduled releases (anti-spam merged)
        if not in_quiet:
            since_24h_iso = _format_utc(ref_utc - timedelta(hours=24))
            music_candidates: list[dict[str, Any]] = []

            recent_items = get_recent_music_releases(
                since_published_iso=since_24h_iso,
                enabled_since_iso=str(state["enabled_since"]),
                min_importance=4,
                enabled_sources=enabled_sources,
                db_path=db_path,
            )
            for item in recent_items:
                url_hash = str(item.get("url_hash") or "").strip()
                if not url_hash:
                    continue
                dedup_key = f"music_release:{url_hash}"
                if has_been_notified(dedup_key, db_path=db_path):
                    continue

                display_title = str(
                    item.get("ai_title") or item.get("title") or "音乐新动态"
                ).strip()
                body_text = str(
                    item.get("ai_summary") or f"来源：{item.get('source_name', 'Music')}"
                ).strip()
                music_candidates.append(
                    {
                        "dedup_key": dedup_key,
                        "category": "music_release",
                        "record_title": display_title,
                        "display_title": display_title,
                        "toast_title": f"新歌/MV 动态 · {display_title}",
                        "toast_body": body_text,
                        "url": str(item.get("url") or "").strip() or hub_url,
                    }
                )

            today_str = ref_today.isoformat()
            for ev in upcoming_rows:
                if (
                    str(ev.get("domain") or "").strip().lower() != "music"
                    or str(ev.get("precision") or "").strip() != "date_only"
                ):
                    continue
                if str(ev.get("start_time") or "")[:10] != today_str:
                    continue

                event_id = str(ev.get("event_id") or "").strip()
                if not event_id:
                    continue
                dedup_key = f"music_date:{event_id}"
                if has_been_notified(dedup_key, db_path=db_path):
                    continue

                ev_title = str(ev.get("title") or "今日新歌/专辑发行").strip()
                subtitle = str(ev.get("subtitle") or "").strip()
                tag = str(ev.get("tag") or "今日发行").strip()
                music_candidates.append(
                    {
                        "dedup_key": dedup_key,
                        "category": "music_date",
                        "record_title": ev_title,
                        "display_title": ev_title,
                        "toast_title": f"今日发行提醒 · {ev_title}",
                        "toast_body": f"{subtitle}（{tag}）" if subtitle else f"今日正式上线（{tag}）",
                        "url": str(ev.get("url") or "").strip() or hub_url,
                    }
                )

            if len(music_candidates) == 1:
                candidate = music_candidates[0]
                ok, err = _invoke_sender(
                    sender,
                    candidate["toast_title"],
                    candidate["toast_body"],
                    candidate["url"],
                    silent_mode,
                )
                if ok:
                    record_notifications_batch(
                        [
                            (
                                candidate["dedup_key"],
                                candidate["category"],
                                candidate["record_title"],
                            )
                        ],
                        sent_at=now_utc_iso,
                        db_path=db_path,
                    )
                    result["sent_count"] += 1
                    result["music_sent"] += 1
                    last_sent_title = candidate["toast_title"]
                elif err:
                    result["errors"].append(err)
            elif len(music_candidates) >= 2:
                merged_title, merged_body = _build_merged_music_toast(music_candidates)
                ok, err = _invoke_sender(
                    sender,
                    merged_title,
                    merged_body,
                    hub_url,
                    silent_mode,
                )
                if ok:
                    record_notifications_batch(
                        [
                            (c["dedup_key"], c["category"], c["record_title"])
                            for c in music_candidates
                        ],
                        sent_at=now_utc_iso,
                        db_path=db_path,
                    )
                    result["sent_count"] += 1
                    result["music_sent"] += 1
                    result["music_merged_count"] = len(music_candidates)
                    last_sent_title = merged_title
                elif err:
                    result["errors"].append(err)

        if last_sent_title is not None:
            _update_last_sent(
                state_path,
                sent_at=now_utc_iso,
                last_title=last_sent_title,
                default_enabled=settings["enabled"],
            )

    try:
        wh_res = push_high_priority_webhooks(
            config=config,
            config_path=config_path,
            db_path=db_path,
            state_path=state_path,
            hub_url=hub_url,
            now_utc=ref_utc,
            now_local=ref_local,
        )
        result["webhook"] = wh_res
        for channel, err_msg in (wh_res.get("failed") or {}).items():
            result["errors"].append(f"Webhook[{channel}]: {err_msg}")
    except Exception as exc:  # noqa: BLE001 - isolate webhook evaluation from Toast engine
        result["webhook"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["errors"].append(f"Webhook: {type(exc).__name__}: {exc}")

    return result


def _is_http_url(url: str | None) -> bool:
    lowered = str(url or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def get_webhook_urls(
    urls_override: dict[str, str | None] | None = None,
) -> dict[str, str]:
    """Return configured http(s) Webhook URLs keyed by channel name."""
    if urls_override is None:
        load_dotenv(ROOT_DIR / ".env", override=False)

    resolved: dict[str, str] = {}
    for channel, env_key in WEBHOOK_ENV_VARS.items():
        raw_val = (
            urls_override.get(channel)
            if urls_override is not None
            else os.getenv(env_key, "")
        )
        cleaned = str(raw_val or "").strip()
        if _is_http_url(cleaned):
            if channel == "bark":
                cleaned = cleaned.rstrip("/")
            resolved[channel] = cleaned
    return resolved


def _normalize_webhook_item(raw: dict[str, Any]) -> dict[str, Any]:
    domain = str(raw.get("domain") or "tech").strip().lower()
    title = " ".join(str(raw.get("title") or "").split()).strip()
    summary = " ".join(str(raw.get("summary") or "").split()).strip()
    source_name = " ".join(str(raw.get("source_name") or "").split()).strip()
    url = str(raw.get("url") or "").strip()
    return {
        "id": str(raw.get("id") or "").strip(),
        "domain": domain,
        "source_name": source_name,
        "title": title,
        "summary": summary,
        "url": url if _is_http_url(url) else "",
        "published_at": str(raw.get("published_at") or "").strip(),
        "importance": int(raw.get("importance") or 3),
    }


def _escape_md_brackets(text: str) -> str:
    return text.replace("[", "【").replace("]", "】")


def build_feishu_payload(
    msg_type: str,
    title: str,
    summary: str,
    items: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    """Build Feishu interactive card payload."""
    template_map = {
        "digest": "blue",
        "high_priority": "orange",
        "test": "green",
    }
    elements: list[dict[str, Any]] = []
    if summary:
        elements.append({"tag": "markdown", "content": summary})

    display_items = items[:10]
    if display_items:
        if elements:
            elements.append({"tag": "hr"})
        for idx, item in enumerate(display_items, start=1):
            item_title = _escape_md_brackets(item.get("title") or "未命名条目")
            item_url = str(item.get("url") or "").strip()
            domain_label = DOMAIN_LABELS_ZH.get(
                str(item.get("domain") or "").lower(),
                str(item.get("domain") or "情报"),
            )
            source_name = str(item.get("source_name") or "").strip()
            meta = f"{domain_label} · {source_name}" if source_name else domain_label
            header_md = (
                f"**{idx}. [{item_title}]({item_url})**"
                if _is_http_url(item_url)
                else f"**{idx}. {item_title}**"
            )
            item_summary = str(item.get("summary") or "").strip()
            block = f"{header_md}\n<font color='grey'>{meta}</font>"
            if item_summary:
                block += f"\n{item_summary}"
            elements.append({"tag": "markdown", "content": block})

    elements.append(
        {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": f"AntiAlgo · {timestamp}"}
            ],
        }
    )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template_map.get(msg_type, "blue"),
                "title": {"tag": "plain_text", "content": title[:120]},
            },
            "elements": elements,
        },
    }


def build_discord_payload(
    msg_type: str,
    title: str,
    summary: str,
    items: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    """Build standard Discord Embed Webhook payload."""
    color_map = {
        "digest": 0x5865F2,
        "high_priority": 0xF59E0B,
        "test": 0x22C55E,
    }
    fields: list[dict[str, Any]] = []
    for idx, item in enumerate(items[:10], start=1):
        domain_label = DOMAIN_LABELS_ZH.get(
            str(item.get("domain") or "").lower(),
            str(item.get("domain") or "情报"),
        )
        item_title = str(item.get("title") or "未命名条目").strip()
        field_name = f"{idx}. [{domain_label}] {item_title}"[:256]

        parts: list[str] = []
        if item.get("summary"):
            parts.append(str(item["summary"]).strip())
        item_url = str(item.get("url") or "").strip()
        if _is_http_url(item_url):
            parts.append(f"[查看原文]({item_url})")
        field_value = ("\n".join(parts) or "-")[:1024]

        fields.append({"name": field_name, "value": field_value, "inline": False})

    embed: dict[str, Any] = {
        "title": title[:256],
        "color": color_map.get(msg_type, 0x5865F2),
        "footer": {"text": "AntiAlgo · 反算法个人情报站"},
        "timestamp": timestamp,
    }
    if summary:
        embed["description"] = summary[:4096]
    if fields:
        embed["fields"] = fields
    return {"embeds": [embed]}


def build_custom_payload(
    msg_type: str,
    title: str,
    summary: str,
    items: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    """Build standard JSON payload for custom Webhook endpoints."""
    return {
        "type": msg_type,
        "title": title,
        "summary": summary,
        "items": items,
        "timestamp": timestamp,
    }


def _truncate_bark_body(text: str, max_chars: int = BARK_BODY_MAX_CHARS) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def build_bark_payload(
    msg_type: str,
    title: str,
    summary: str,
    items: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any]:
    """Build Bark iOS push JSON payload."""
    del timestamp  # Bark uses server receive time
    clean_title = " ".join(str(title or "").split()).strip()[:100]
    clean_summary = str(summary or "").strip()

    if msg_type == "digest" or len(items) > 1:
        parts: list[str] = []
        if clean_summary:
            parts.append(clean_summary)
        item_lines = [
            f"{idx}. {str(it.get('title') or '未命名条目').strip()}"
            for idx, it in enumerate(items[:5], start=1)
        ]
        if item_lines:
            parts.append("\n".join(item_lines))
        body_raw = "\n\n".join(parts)
    else:
        fallback = ""
        if items:
            fallback = str(items[0].get("summary") or items[0].get("title") or "").strip()
        body_raw = clean_summary or fallback

    body_text = _truncate_bark_body(body_raw or clean_title, BARK_BODY_MAX_CHARS)
    payload: dict[str, Any] = {
        "title": clean_title,
        "body": body_text,
        "group": "AntiAlgo",
        "level": "timeSensitive" if msg_type == "high_priority" else "active",
    }

    first_url = next(
        (
            str(it.get("url") or "").strip()
            for it in items
            if _is_http_url(it.get("url"))
        ),
        "",
    )
    if first_url:
        payload["url"] = first_url

    return payload


_CHANNEL_PAYLOAD_BUILDERS = {
    "feishu": build_feishu_payload,
    "discord": build_discord_payload,
    "bark": build_bark_payload,
    "custom": build_custom_payload,
}


def send_webhook_request(
    channel: str,
    url: str,
    payload: dict[str, Any],
    timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    *,
    client: httpx.Client | None = None,
) -> tuple[bool, str | None]:
    """Send one JSON Webhook POST via httpx with timeout and full error isolation."""
    try:
        if client is not None:
            resp = client.post(url, json=payload)
        else:
            with httpx.Client(timeout=httpx.Timeout(timeout)) as local_client:
                resp = local_client.post(url, json=payload)
        resp.raise_for_status()
        if channel == "feishu":
            try:
                data = resp.json()
            except ValueError:
                data = None
            if isinstance(data, dict):
                err_code = data.get("code", data.get("StatusCode"))
                if err_code not in (None, 0):
                    err_msg = (
                        data.get("msg")
                        or data.get("StatusMessage")
                        or str(data)
                    )
                    return False, f"Feishu error {err_code}: {err_msg}"
        elif channel == "bark":
            try:
                data = resp.json()
            except ValueError:
                return False, "Invalid Bark JSON response"
            if not isinstance(data, dict) or data.get("code") != 200:
                err_code = data.get("code") if isinstance(data, dict) else None
                err_msg = (
                    data.get("message") or str(data)
                    if isinstance(data, dict)
                    else str(data)
                )
                return False, f"Bark error {err_code}: {err_msg}"
        return True, None
    except Exception as exc:  # noqa: BLE001 - never let webhook failure block pipeline
        return False, f"{type(exc).__name__}: {exc}"


def dispatch_webhook_message(
    *,
    msg_type: str,
    title: str,
    summary: str,
    items: list[dict[str, Any]] | None = None,
    timestamp: str | None = None,
    urls: dict[str, str | None] | None = None,
    timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    sender: WebhookSender | None = None,
) -> dict[str, Any]:
    """Dispatch a formatted message across all configured Webhook channels."""
    eff_urls = get_webhook_urls(urls)
    if not eff_urls:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "no_webhook_urls",
        }

    ts = timestamp or utc_now_iso()
    norm_items = [_normalize_webhook_item(entry) for entry in (items or [])]
    sent_channels: list[str] = []
    failed: dict[str, str] = {}

    for channel, url in eff_urls.items():
        builder = _CHANNEL_PAYLOAD_BUILDERS.get(channel, build_custom_payload)
        payload = builder(msg_type, title, summary, norm_items, ts)
        try:
            if sender is None:
                ok, err = send_webhook_request(channel, url, payload, timeout)
            else:
                ok, err = sender(channel, url, payload, timeout)
        except Exception as exc:  # noqa: BLE001
            ok, err = False, f"{type(exc).__name__}: {exc}"

        if ok:
            sent_channels.append(channel)
        else:
            failed[channel] = err or "Unknown webhook error"

    return {
        "ok": bool(sent_channels) and not failed,
        "any_sent": bool(sent_channels),
        "sent_channels": sent_channels,
        "failed": failed,
        "skipped_reason": None,
    }


def _resolve_eval_times(
    now_utc: datetime | None,
    now_local: datetime | None,
) -> tuple[datetime, datetime, str]:
    ref_utc = _normalize_utc(now_utc)
    if now_local is not None:
        ref_local = now_local
    elif now_utc is not None:
        ref_local = ref_utc.astimezone()
    else:
        ref_local = datetime.now().astimezone()
    return ref_utc, ref_local, _format_utc(ref_utc)


def push_digest_webhook(
    digest: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = NOTIFY_STATE_PATH,
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
    urls: dict[str, str | None] | None = None,
    sender: WebhookSender | None = None,
) -> dict[str, Any]:
    """Push new digest.json items via configured Webhook channels with deduplication."""
    eff_config = (
        config
        if config is not None
        else (load_config(config_path) if config_path.exists() else {})
    )
    webhook_cfg = get_webhook_settings(eff_config)
    notify_cfg = get_notification_settings(eff_config)

    if not webhook_cfg["enabled"] or not webhook_cfg["push_digest"]:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "disabled",
            "item_count": 0,
        }

    eff_urls = get_webhook_urls(urls)
    if not eff_urls:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "no_webhook_urls",
            "item_count": 0,
        }

    ref_utc, ref_local, now_utc_iso = _resolve_eval_times(now_utc, now_local)
    if webhook_cfg["respect_quiet_hours"] and is_in_quiet_hours(
        notify_cfg["quiet_hours"], now_local=ref_local
    ):
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "quiet_hours",
            "item_count": 0,
        }

    state = load_notify_state(
        state_path=state_path,
        default_enabled=notify_cfg["enabled"],
        now_utc=ref_utc,
    )
    sent_ids = set(state.get("webhook_sent_ids") or [])

    raw_items = digest.get("items") or []
    unsent_items: list[dict[str, Any]] = []
    dedup_keys: list[str] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        dedup_key = f"digest_item:{item_id}"
        if dedup_key in sent_ids:
            continue
        unsent_items.append(entry)
        dedup_keys.append(dedup_key)

    if not unsent_items:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "already_sent",
            "item_count": 0,
        }

    briefings = [
        str(line).strip()
        for line in (digest.get("top_briefings") or [])
        if str(line).strip()
    ]
    summary_lines = [f"• {line}" for line in briefings[:3]]
    if len(unsent_items) > 10:
        summary_lines.append(f"（本次共新增 {len(unsent_items)} 条，卡片内展示前 10 条）")
    summary_text = "\n".join(summary_lines) or f"本次精选 {len(unsent_items)} 条高信噪比情报。"

    today_str = ref_local.date().isoformat()
    title = f"AntiAlgo 情报汇总 · {today_str}（{len(unsent_items)} 条）"

    dispatch_res = dispatch_webhook_message(
        msg_type="digest",
        title=title,
        summary=summary_text,
        items=unsent_items[:10],
        timestamp=now_utc_iso,
        urls=eff_urls,
        sender=sender,
    )
    if dispatch_res.get("any_sent"):
        record_webhook_sent_ids(
            dedup_keys,
            state_path=state_path,
            sent_at=now_utc_iso,
            digest_date=today_str,
            default_enabled=notify_cfg["enabled"],
        )

    dispatch_res["item_count"] = len(unsent_items)
    return dispatch_res


def push_high_priority_webhooks(
    *,
    config: dict[str, Any] | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    state_path: Path = NOTIFY_STATE_PATH,
    hub_url: str = DEFAULT_HUB_URL,
    now_utc: datetime | None = None,
    now_local: datetime | None = None,
    urls: dict[str, str | None] | None = None,
    sender: WebhookSender | None = None,
) -> dict[str, Any]:
    """Push high-priority CS2 pre-match & importance>=4 items via Webhook channels."""
    eff_config = (
        config
        if config is not None
        else (load_config(config_path) if config_path.exists() else {})
    )
    webhook_cfg = get_webhook_settings(eff_config)
    notify_cfg = get_notification_settings(eff_config)

    if not webhook_cfg["enabled"] or not webhook_cfg["push_high_priority"]:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "disabled",
            "item_count": 0,
        }

    eff_urls = get_webhook_urls(urls)
    if not eff_urls:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "no_webhook_urls",
            "item_count": 0,
        }

    ref_utc, ref_local, now_utc_iso = _resolve_eval_times(now_utc, now_local)
    if webhook_cfg["respect_quiet_hours"] and is_in_quiet_hours(
        notify_cfg["quiet_hours"], now_local=ref_local
    ):
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "quiet_hours",
            "item_count": 0,
        }

    state = load_notify_state(
        state_path=state_path,
        default_enabled=notify_cfg["enabled"],
        now_utc=ref_utc,
    )
    sent_ids = set(state.get("webhook_sent_ids") or [])

    focus_entities = get_focus_entities(eff_config)
    esports_focus = focus_entities.get("esports", [])
    upcoming_cfg = get_upcoming_settings(eff_config)
    enabled_sources = get_enabled_source_names(eff_config)

    candidates: list[dict[str, Any]] = []
    dedup_keys: list[str] = []

    upcoming_rows = get_upcoming_events(
        esports_window_hours=upcoming_cfg["esports_window_hours"],
        music_window_days=upcoming_cfg["music_window_days"],
        default_window_hours=upcoming_cfg["default_window_hours"],
        db_path=db_path,
        now_utc=ref_utc,
        today_local=ref_local.date(),
    )
    max_lead = float(notify_cfg["cs2_lead_minutes"] + 1)
    for ev in upcoming_rows:
        if (
            str(ev.get("domain") or "").strip().lower() != "esports"
            or str(ev.get("precision") or "").strip() != "datetime"
        ):
            continue
        start_dt = _parse_iso_utc(str(ev.get("start_time") or ""))
        if start_dt is None:
            continue
        minutes_until = (start_dt - ref_utc).total_seconds() / 60.0
        if not (1.0 <= minutes_until <= max_lead):
            continue
        if notify_cfg["cs2_only_focus_teams"] and not matches_focus_esports(
            ev, esports_focus
        ):
            continue
        event_id = str(ev.get("event_id") or "").strip()
        if not event_id:
            continue
        dedup_key = f"wh_cs2:{event_id}"
        if dedup_key in sent_ids:
            continue

        mins_display = max(1, round(minutes_until))
        match_title = str(ev.get("title") or "CS2 焦点赛事").strip()
        subtitle = str(ev.get("subtitle") or "").strip()
        candidates.append(
            {
                "id": event_id,
                "domain": "esports",
                "source_name": str(ev.get("source") or "CS2 赛程").strip(),
                "title": f"CS2 赛前提醒（约 {mins_display} 分钟后）：{match_title}",
                "summary": subtitle or "即将开赛，已开启防剧透保护",
                "url": str(ev.get("url") or "").strip() or hub_url,
                "published_at": str(ev.get("start_time") or now_utc_iso),
                "importance": int(ev.get("priority") or 4),
            }
        )
        dedup_keys.append(dedup_key)

    recent_items = get_recent_items(
        max_age_hours=24,
        limit=20,
        enabled_sources=enabled_sources,
        min_importance=4,
        require_processed=True,
        db_path=db_path,
    )
    enabled_since = str(state["enabled_since"])
    for row in recent_items:
        url_hash = str(row.get("url_hash") or "").strip()
        if not url_hash:
            continue
        if str(row.get("created_at") or "") < enabled_since:
            continue
        wh_key = f"wh_item:{url_hash}"
        digest_key = f"digest_item:{url_hash}"
        if wh_key in sent_ids or digest_key in sent_ids:
            continue

        item_title = str(row.get("ai_title") or row.get("title") or "").strip()
        item_summary = str(row.get("ai_summary") or "").strip()
        candidates.append(
            {
                "id": url_hash,
                "domain": str(row.get("domain") or "tech").strip().lower(),
                "source_name": str(row.get("source_name") or "").strip(),
                "title": item_title,
                "summary": item_summary,
                "url": str(row.get("url") or "").strip() or hub_url,
                "published_at": str(row.get("published_at") or now_utc_iso),
                "importance": int(row.get("importance") or 4),
            }
        )
        dedup_keys.append(wh_key)

    if not candidates:
        return {
            "ok": False,
            "any_sent": False,
            "sent_channels": [],
            "failed": {},
            "skipped_reason": "no_new_items",
            "item_count": 0,
        }

    if len(candidates) == 1:
        single = candidates[0]
        title = f"高优提醒 · {single['title']}"
        summary = single["summary"] or f"来源：{single['source_name'] or single['domain']}"
    else:
        title = f"高优情报提醒 · 新增 {len(candidates)} 条"
        summary = f"检测到 {len(candidates)} 条高优先动态与赛前提醒。"

    dispatch_res = dispatch_webhook_message(
        msg_type="high_priority",
        title=title,
        summary=summary,
        items=candidates[:10],
        timestamp=now_utc_iso,
        urls=eff_urls,
        sender=sender,
    )
    if dispatch_res.get("any_sent"):
        record_webhook_sent_ids(
            dedup_keys,
            state_path=state_path,
            sent_at=now_utc_iso,
            default_enabled=notify_cfg["enabled"],
        )

    dispatch_res["item_count"] = len(candidates)
    return dispatch_res


def send_test_webhook(
    *,
    hub_url: str = DEFAULT_HUB_URL,
    urls: dict[str, str | None] | None = None,
    sender: WebhookSender | None = None,
) -> dict[str, Any]:
    """Send a diagnostic test Webhook message without touching notify_state.json."""
    now_iso = utc_now_iso()
    sample_items = [
        {
            "id": "webhook-connectivity-test",
            "domain": "tech",
            "source_name": "AntiAlgo Notifier",
            "title": "AntiAlgo Webhook 通道连通性测试",
            "summary": "如果你看到这条消息，说明飞书 / Discord / Bark / 自定义 Webhook 配置已生效。",
            "url": hub_url,
            "published_at": now_iso,
            "importance": 4,
        }
    ]
    return dispatch_webhook_message(
        msg_type="test",
        title="AntiAlgo · Webhook 连通性测试",
        summary="这是一条来自反算法个人情报站的测试推送（不会写入去重记录）。",
        items=sample_items,
        timestamp=now_iso,
        urls=urls,
        sender=sender,
    )


def get_runtime_notify_status(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    state_path: Path = NOTIFY_STATE_PATH,
) -> dict[str, Any]:
    """Return merged config + runtime notify state snapshot for server/UI APIs."""
    config = load_config(config_path) if config_path.exists() else {}
    settings = get_notification_settings(config)
    state = load_notify_state(
        state_path=state_path,
        default_enabled=settings["enabled"],
    )
    last_sent = state.get("last_sent_at") or get_last_notification_sent_at(
        db_path=db_path
    )
    return {
        "enabled": bool(state["enabled"]),
        "config_enabled": bool(settings["enabled"]),
        "effective_enabled": bool(settings["enabled"] and state["enabled"]),
        "enabled_since": state["enabled_since"],
        "last_sent_at": last_sent,
        "last_title": state.get("last_title"),
        "poll_interval_seconds": settings["poll_interval_seconds"],
        "silent": settings["silent"],
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Windows silent Toast notification engine for Anti-Algorithm Personal Hub."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send one silent test Windows Toast notification without logging to DB.",
    )
    parser.add_argument(
        "--test-webhook",
        action="store_true",
        help="Send one test message to all configured Webhook URLs without recording dedup IDs.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run a single notification evaluation pass immediately.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_HUB_URL,
        help=f"Target URL when clicking notification (default {DEFAULT_HUB_URL}).",
    )
    args = parser.parse_args(argv)

    if args.test:
        config = load_config(DEFAULT_CONFIG_PATH) if DEFAULT_CONFIG_PATH.exists() else {}
        settings = get_notification_settings(config)
        ok, err = send_test_toast(hub_url=args.url, silent=settings["silent"])
        if ok:
            print("[Notifier] 已投递静音测试 Windows Toast 通知（未写入去重表）。")
            return 0
        print(f"[Notifier] 测试通知投递失败：{err}", file=sys.stderr)
        return 1

    if args.test_webhook:
        res = send_test_webhook(hub_url=args.url)
        if res.get("skipped_reason") == "no_webhook_urls":
            print(
                "[Webhook] 未检测到任何有效 Webhook URL。请在 .env 中配置 "
                "WEBHOOK_FEISHU_URL / WEBHOOK_DISCORD_URL / WEBHOOK_BARK_URL / WEBHOOK_CUSTOM_URL 后重试。",
                file=sys.stderr,
            )
            return 1
        for channel in res.get("sent_channels") or []:
            print(f"[Webhook] 通道 {channel} 测试消息发送成功。")
        for channel, err_msg in (res.get("failed") or {}).items():
            print(f"[Webhook] 通道 {channel} 发送失败：{err_msg}", file=sys.stderr)
        return 0 if res.get("ok") else 1

    summary = check_and_notify(hub_url=args.url)
    print(f"[Notifier] 检查完成：{json.dumps(summary, ensure_ascii=False)}")
    return 0 if not summary.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
