"""Zero-console native Windows Toast notification engine for Anti-Algorithm Personal Hub."""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape

from pipeline import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    NOTIFY_STATE_PATH,
    get_enabled_source_names,
    get_focus_entities,
    get_notification_settings,
    get_upcoming_settings,
    load_config,
)
from pipeline.db import (
    cleanup_old_notifications,
    get_last_notification_sent_at,
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

_STATE_LOCK = threading.Lock()

ToastSender = Callable[[str, str, str, bool], tuple[bool, str | None] | bool]


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


def load_notify_state(
    state_path: Path = NOTIFY_STATE_PATH,
    *,
    default_enabled: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Load or cold-start initialize data/notify_state.json."""
    now_iso = _format_utc(_normalize_utc(now_utc))
    with _STATE_LOCK:
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

        state = {
            "enabled": enabled,
            "enabled_since": enabled_since,
            "last_sent_at": loaded.get("last_sent_at"),
            "last_title": loaded.get("last_title"),
        }
        if dirty:
            save_notify_state(state, state_path=state_path)
        return state


def toggle_notify_state(
    state_path: Path = NOTIFY_STATE_PATH,
    *,
    enabled: bool | None = None,
    default_enabled: bool = True,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Toggle or explicitly set runtime notification enabled flag and persist."""
    state = load_notify_state(
        state_path=state_path,
        default_enabled=default_enabled,
        now_utc=now_utc,
    )
    with _STATE_LOCK:
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
    state = load_notify_state(state_path=state_path, default_enabled=default_enabled)
    with _STATE_LOCK:
        state["last_sent_at"] = sent_at
        state["last_title"] = last_title
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
        return result

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

    return result


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

    summary = check_and_notify(hub_url=args.url)
    print(f"[Notifier] 检查完成：{json.dumps(summary, ensure_ascii=False)}")
    return 0 if not summary.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
