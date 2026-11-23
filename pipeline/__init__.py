"""Pipeline package and backward-compatible config helpers for Anti-Algorithm Personal Hub."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "sources.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config" / "sources.example.yaml"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "intel.db"
MOCK_DB_PATH = ROOT_DIR / "data" / "intel_mock.db"
DEFAULT_DIGEST_PATH = ROOT_DIR / "web" / "public" / "digest.json"
DEFAULT_TERMS_PATH = ROOT_DIR / "web" / "public" / "terms.json"
DAILY_STATE_PATH = ROOT_DIR / "data" / "daily_state.json"
MBID_CACHE_PATH = ROOT_DIR / "data" / "mbid_cache.json"
NOTIFY_STATE_PATH = ROOT_DIR / "data" / "notify_state.json"

DEFAULT_FALLBACK_DAILY_TERM: dict[str, str] = {
    "term_en": "Model Quantization",
    "term_zh": "模型量化",
    "domain": "tech",
    "phonetic_or_abbr": "Quant",
    "plain_explain": "把大模型参数从高精度小数压缩成低精度整数，牺牲极小精度换取更低显存占用和更快运行速度。",
    "in_context": "如今很多轻量开源模型能在普通笔记本或几 GB 内存里本地跑起来，靠的就是模型量化技术。",
}


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load YAML configuration from disk (falling back to sources.example.yaml if needed)."""
    target_path = config_path
    if (
        not target_path.exists()
        and config_path == DEFAULT_CONFIG_PATH
        and EXAMPLE_CONFIG_PATH.exists()
    ):
        target_path = EXAMPLE_CONFIG_PATH

    with target_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root type in {target_path}: expected mapping")
    return data


def _setting_int(
    config: dict[str, Any], keys: tuple[str, ...], default: int
) -> int:
    settings = config.get("settings") or {}
    if not isinstance(settings, dict):
        return default
    for key in keys:
        val = settings.get(key)
        if val is not None:
            return int(val)
    return default


def get_window_hours(config: dict[str, Any]) -> int:
    """Return active item retention window in hours (default 72)."""
    return _setting_int(config, ("window_hours", "max_age_hours"), 72)


def get_max_summary_chars(config: dict[str, Any]) -> int:
    """Return raw summary truncation budget before LLM / storage (default 800)."""
    return _setting_int(config, ("max_summary_chars", "llm_max_summary_chars"), 800)


def get_default_source_limit(config: dict[str, Any]) -> int:
    """Return default maximum entries to parse per source (default 15)."""
    return _setting_int(
        config, ("default_limit_per_source", "max_items_per_source"), 15
    )


def get_max_digest_items(config: dict[str, Any]) -> int:
    """Return maximum cards exported to digest.json (default 15)."""
    return _setting_int(config, ("max_digest_items",), 15)


def get_min_digest_importance(config: dict[str, Any]) -> int:
    """Return minimum importance score required for normal digest export (default 3)."""
    return _setting_int(config, ("min_digest_importance",), 3)


def get_upcoming_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return asymmetric window and collector settings for upcoming events."""
    raw = config.get("upcoming_settings") or {}
    if not isinstance(raw, dict):
        raw = {}
    tiers_raw = raw.get("pandascore_tiers")
    tiers = (
        [str(t).strip().lower() for t in tiers_raw if str(t).strip()]
        if isinstance(tiers_raw, list)
        else ["s", "a"]
    )
    return {
        "esports_window_hours": int(raw.get("esports_window_hours") or 48),
        "music_window_days": int(raw.get("music_window_days") or 14),
        "default_window_hours": int(raw.get("default_window_hours") or 72),
        "max_upcoming_items": int(raw.get("max_upcoming_items") or 8),
        "pandascore_tiers": tiers or ["s", "a"],
    }


def get_notification_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return local Windows Toast notification settings with safe defaults."""
    raw = config.get("notifications")
    if not isinstance(raw, dict):
        raw = {}

    raw_quiet = raw.get("quiet_hours")
    if raw_quiet is None:
        quiet_hours = ["01:30", "08:30"]
    elif isinstance(raw_quiet, list):
        quiet_hours = [str(item).strip() for item in raw_quiet if str(item).strip()]
    else:
        quiet_hours = []

    poll_interval = int(raw.get("poll_interval_seconds") or 60)
    lead_minutes = int(raw.get("cs2_lead_minutes") or 15)

    return {
        "enabled": bool(raw.get("enabled", True)),
        "poll_interval_seconds": max(10, poll_interval),
        "cs2_lead_minutes": max(1, lead_minutes),
        "cs2_only_focus_teams": bool(raw.get("cs2_only_focus_teams", True)),
        "quiet_hours": quiet_hours,
        "cs2_bypass_quiet_hours": bool(raw.get("cs2_bypass_quiet_hours", True)),
        "silent": bool(raw.get("silent", True)),
    }


def get_rss_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled RSS/Atom sources supporting both 'sources' and legacy 'rss_sources'."""
    raw_sources = config.get("sources")
    if raw_sources is None:
        raw_sources = config.get("rss_sources")
    if not isinstance(raw_sources, list):
        return []

    return [
        src
        for src in raw_sources
        if isinstance(src, dict)
        and src.get("enabled", True)
        and str(src.get("type", "rss")).strip().lower() == "rss"
    ]


def get_focus_entities(config: dict[str, Any]) -> dict[str, list[str]]:
    """Return focus/watched entities mapping supporting both 'focus_entities' and 'watched_entities'."""
    raw = config.get("focus_entities")
    if raw is None:
        raw = config.get("watched_entities")
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for domain, entities in raw.items():
        if isinstance(entities, list):
            normalized[str(domain)] = [
                str(e).strip() for e in entities if str(e).strip()
            ]
    return normalized


def get_blacklist_keywords(config: dict[str, Any]) -> list[str]:
    """Flatten blacklist_keywords from either {lang: [...]} mapping or flat list."""
    raw = config.get("blacklist_keywords")
    candidates: list[Any] = []

    if isinstance(raw, dict):
        for group in raw.values():
            if isinstance(group, list):
                candidates.extend(group)
            elif isinstance(group, str):
                candidates.append(group)
    elif isinstance(raw, list):
        candidates.extend(raw)

    deduped: list[str] = []
    seen: set[str] = set()
    for kw in candidates:
        cleaned = str(kw).strip()
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            seen.add(lowered)
            deduped.append(cleaned)
    return deduped


def get_spoiler_shield_domains(config: dict[str, Any]) -> list[str]:
    """Return domain keys that enable spoiler_shield by default."""
    domains = config.get("domains") or {}
    if not isinstance(domains, dict):
        return []
    return [
        str(domain)
        for domain, meta in domains.items()
        if isinstance(meta, dict) and bool(meta.get("spoiler_shield"))
    ]


def get_domain_preferences(config: dict[str, Any]) -> dict[str, Any]:
    """Return domain-specific include/exclude rules if configured."""
    prefs = config.get("domain_preferences")
    return prefs if isinstance(prefs, dict) else {}


def get_enabled_source_names(config: dict[str, Any]) -> set[str] | None:
    """Return enabled source names when sources/rss_sources is configured, else None."""
    raw_sources = config.get("sources")
    if raw_sources is None:
        raw_sources = config.get("rss_sources")
    if not isinstance(raw_sources, list):
        return None
    return {
        str(src["name"]).strip()
        for src in raw_sources
        if isinstance(src, dict)
        and src.get("enabled", True)
        and str(src.get("name", "")).strip()
    }


def _today_local_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def load_daily_state(state_path: Path = DAILY_STATE_PATH) -> dict[str, Any] | None:
    """Load cached today's top_briefings and daily_term if matching local date."""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == _today_local_str():
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_daily_state(
    top_briefings: list[str] | None,
    daily_term: dict[str, Any] | None,
    state_path: Path = DAILY_STATE_PATH,
) -> None:
    """Persist today's synthesized briefings and daily_term to prevent jitter."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": _today_local_str(),
        "top_briefings": top_briefings,
        "daily_term": daily_term,
    }
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def clear_daily_state(state_path: Path = DAILY_STATE_PATH) -> None:
    """Remove cached daily state (used when resetting test runs)."""
    if state_path.exists():
        state_path.unlink()
