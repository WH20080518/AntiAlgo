"""Async upcoming schedule collectors (PandaScore CS2 & MusicBrainz releases)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from dotenv import load_dotenv
import httpx

from pipeline import (
    MBID_CACHE_PATH,
    ROOT_DIR,
    get_focus_entities,
    get_upcoming_settings,
)
from pipeline.db import compute_event_id, normalize_event_start_time

PANDASCORE_URL = "https://api.pandascore.co/csgo/matches/upcoming"
MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_USER_AGENT = (
    "AntiAlgoHub/0.1 ( https://github.com/local/anti-algo-hub )"
)
MUSICBRAINZ_MIN_INTERVAL_SECONDS = 1.15
PLACEHOLDER_PANDASCORE_KEYS = frozenset(
    {"", "your-pandascore-token", "your-api-key"}
)
RELEASE_TYPE_ZH = {
    "single": "单曲",
    "album": "专辑",
    "ep": "EP",
}


def _match_focus_team(name: str, focus_terms_lower: list[str]) -> bool:
    low = name.lower()
    return any(term in low for term in focus_terms_lower if term)


def parse_pandascore_match(
    match: dict[str, Any],
    *,
    focus_teams_lower: list[str],
    allowed_tiers: set[str],
    window_hours: int,
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    """Filter and map one PandaScore match payload to upcoming_events schema."""
    ref_now = now_utc or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)

    raw_begin = match.get("begin_at") or match.get("scheduled_at")
    norm_start = normalize_event_start_time(str(raw_begin or ""), precision="datetime")
    if not norm_start:
        return None

    start_dt = datetime.fromisoformat(norm_start.replace("Z", "+00:00"))
    if start_dt < ref_now - timedelta(hours=3):
        return None
    if start_dt > ref_now + timedelta(hours=window_hours):
        return None

    opponents_raw = match.get("opponents") or []
    team_names: list[str] = []
    if isinstance(opponents_raw, list):
        for entry in opponents_raw:
            if not isinstance(entry, dict):
                continue
            opp = entry.get("opponent") or {}
            name = str(opp.get("name") or "").strip()
            if name and name.lower() != "tbd":
                team_names.append(name)

    if not team_names:
        return None
    title = (
        f"{team_names[0]} vs {team_names[1]}"
        if len(team_names) >= 2
        else f"{team_names[0]} vs TBD"
    )

    tournament = match.get("tournament") or {}
    league = match.get("league") or {}
    serie = match.get("serie") or {}
    tier = str(tournament.get("tier") or "").strip().lower()
    tier_match = tier in allowed_tiers
    focus_hit = any(
        _match_focus_team(name, focus_teams_lower) for name in team_names
    )

    if not (tier_match or focus_hit):
        return None

    league_name = str(league.get("name") or "").strip()
    serie_name = str(serie.get("full_name") or tournament.get("name") or "").strip()
    if league_name and serie_name and league_name.lower() not in serie_name.lower():
        subtitle = f"{league_name} · {serie_name}"
    else:
        subtitle = serie_name or league_name

    num_games = match.get("number_of_games")
    tag = f"BO{int(num_games)}" if isinstance(num_games, int) and num_games > 0 else "CS2"

    url = ""
    streams = match.get("streams_list") or []
    if isinstance(streams, list):
        for stream in streams:
            if isinstance(stream, dict) and stream.get("raw_url"):
                url = str(stream["raw_url"]).strip()
                break

    priority = 5 if (tier_match and focus_hit) else (4 if focus_hit else 3)

    return {
        "event_id": compute_event_id("esports", title, norm_start),
        "domain": "esports",
        "title": title,
        "subtitle": subtitle,
        "start_time": norm_start,
        "precision": "datetime",
        "tag": tag,
        "source": "pandascore",
        "url": url,
        "priority": priority,
    }


async def fetch_pandascore_cs2(
    config: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    now_utc: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch upcoming CS2 S/A-tier or focus-team matches from PandaScore."""
    load_dotenv(ROOT_DIR / ".env", override=False)
    token = (
        api_key if api_key is not None else os.getenv("PANDASCORE_API_KEY", "")
    ).strip()

    if not token or token in PLACEHOLDER_PANDASCORE_KEYS:
        return [], {
            "name": "PandaScore CS2",
            "domain": "esports",
            "ok": True,
            "skipped": True,
            "fetched": 0,
            "detail": "未配置 PANDASCORE_API_KEY，已优雅跳过",
        }

    up_cfg = get_upcoming_settings(config)
    window_hours = int(up_cfg["esports_window_hours"])
    allowed_tiers = set(up_cfg["pandascore_tiers"])
    focus_teams = (get_focus_entities(config).get("esports")) or []
    focus_teams_lower = [t.lower() for t in focus_teams]

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    params = {"sort": "begin_at", "per_page": 50}

    try:
        if client is not None:
            resp = await client.get(PANDASCORE_URL, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()
        else:
            async with httpx.AsyncClient(timeout=15.0) as local_client:
                resp = await local_client.get(
                    PANDASCORE_URL, headers=headers, params=params
                )
                resp.raise_for_status()
                payload = resp.json()

        if not isinstance(payload, list):
            raise ValueError("Unexpected PandaScore response type")

        events: list[dict[str, Any]] = []
        for match in payload:
            if not isinstance(match, dict):
                continue
            parsed = parse_pandascore_match(
                match,
                focus_teams_lower=focus_teams_lower,
                allowed_tiers=allowed_tiers,
                window_hours=window_hours,
                now_utc=now_utc,
            )
            if parsed is not None:
                events.append(parsed)

        return events, {
            "name": "PandaScore CS2",
            "domain": "esports",
            "ok": True,
            "skipped": False,
            "fetched": len(events),
            "detail": f"{len(events)} 场聚焦赛程",
        }
    except Exception as exc:  # noqa: BLE001 - isolate API errors cleanly
        return [], {
            "name": "PandaScore CS2",
            "domain": "esports",
            "ok": False,
            "skipped": False,
            "fetched": 0,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _load_mbid_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    if not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(k): v
                for k, v in data.items()
                if isinstance(v, dict) and v.get("mbid")
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_mbid_cache(
    cache: dict[str, dict[str, str]], cache_path: Path
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _select_best_artist_match(
    artist_name: str, artists: list[dict[str, Any]]
) -> dict[str, str] | None:
    target = artist_name.strip().lower()
    for artist in artists:
        if not isinstance(artist, dict):
            continue
        name = str(artist.get("name") or "").strip()
        mbid = str(artist.get("id") or "").strip()
        if mbid and name.lower() == target:
            return {"mbid": mbid, "name": name}

    if artists and isinstance(artists[0], dict):
        top = artists[0]
        mbid = str(top.get("id") or "").strip()
        name = str(top.get("name") or "").strip()
        score = int(top.get("score") or 0)
        if mbid and score >= 85:
            return {"mbid": mbid, "name": name}
    return None


def parse_musicbrainz_release_groups(
    artist_name: str,
    release_groups: list[dict[str, Any]],
    *,
    window_days: int,
    today_local: date | None = None,
) -> list[dict[str, Any]]:
    """Extract release groups with exact YYYY-MM-DD date within [today, today + window_days]."""
    ref_today = today_local or datetime.now().astimezone().date()
    end_date = ref_today + timedelta(days=window_days)

    events: list[dict[str, Any]] = []
    for rg in release_groups:
        if not isinstance(rg, dict):
            continue
        raw_date = str(rg.get("first-release-date") or "").strip()
        if len(raw_date) != 10:
            continue
        try:
            rel_date = date.fromisoformat(raw_date)
        except ValueError:
            continue

        if not (ref_today <= rel_date <= end_date):
            continue

        rg_title = " ".join(str(rg.get("title") or "").split()).strip()
        rg_id = str(rg.get("id") or "").strip()
        if not rg_title:
            continue

        raw_type = str(rg.get("primary-type") or "").strip().lower()
        tag = RELEASE_TYPE_ZH.get(raw_type, "发行")
        title = f"{artist_name}《{rg_title}》"
        norm_start = f"{rel_date.isoformat()}T00:00:00Z"

        events.append(
            {
                "event_id": compute_event_id("music", title, norm_start),
                "domain": "music",
                "title": title,
                "subtitle": f"预定发行{tag}",
                "start_time": norm_start,
                "precision": "date_only",
                "tag": tag,
                "source": "musicbrainz",
                "url": f"https://musicbrainz.org/release-group/{rg_id}"
                if rg_id
                else "",
                "priority": 4,
            }
        )
    return events


async def fetch_musicbrainz_releases(
    config: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    mbid_cache_path: Path = MBID_CACHE_PATH,
    min_interval_seconds: float = MUSICBRAINZ_MIN_INTERVAL_SECONDS,
    today_local: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Query MusicBrainz public API with strict 1.1s+ rate limiting for upcoming releases."""
    artists = (get_focus_entities(config).get("music")) or []
    if not artists:
        return [], {
            "name": "MusicBrainz 发行日历",
            "domain": "music",
            "ok": True,
            "skipped": True,
            "fetched": 0,
            "detail": "focus_entities.music 为空，已跳过",
        }

    up_cfg = get_upcoming_settings(config)
    window_days = int(up_cfg["music_window_days"])
    cache = _load_mbid_cache(mbid_cache_path)
    cache_dirty = False
    last_req_ts = 0.0

    async def _mb_get(
        http_client: httpx.AsyncClient, path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        nonlocal last_req_ts
        if min_interval_seconds > 0 and last_req_ts > 0:
            elapsed = time.monotonic() - last_req_ts
            if elapsed < min_interval_seconds:
                await asyncio.sleep(min_interval_seconds - elapsed)

        last_req_ts = time.monotonic()
        resp = await http_client.get(f"{MUSICBRAINZ_BASE_URL}{path}", params=params)
        if resp.status_code == 503:
            await asyncio.sleep(max(1.5, min_interval_seconds))
            last_req_ts = time.monotonic()
            resp = await http_client.get(
                f"{MUSICBRAINZ_BASE_URL}{path}", params=params
            )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    all_events: list[dict[str, Any]] = []
    errors = 0

    headers = {
        "User-Agent": MUSICBRAINZ_USER_AGENT,
        "Accept": "application/json",
    }

    async def _process_all(http_client: httpx.AsyncClient) -> None:
        nonlocal cache_dirty, errors
        for artist_name in artists:
            key = artist_name.strip().lower()
            if not key:
                continue
            try:
                cached = cache.get(key)
                mbid = cached.get("mbid") if isinstance(cached, dict) else None

                if not mbid:
                    search_data = await _mb_get(
                        http_client,
                        "/artist/",
                        {"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 3},
                    )
                    best = _select_best_artist_match(
                        artist_name, list(search_data.get("artists") or [])
                    )
                    if best:
                        mbid = best["mbid"]
                        cache[key] = best
                        cache_dirty = True

                if not mbid:
                    continue

                rg_data = await _mb_get(
                    http_client,
                    "/release-group",
                    {
                        "artist": mbid,
                        "type": "album|single|ep",
                        "fmt": "json",
                        "limit": 100,
                    },
                )
                matched = parse_musicbrainz_release_groups(
                    artist_name,
                    list(rg_data.get("release-groups") or []),
                    window_days=window_days,
                    today_local=today_local,
                )
                all_events.extend(matched)
            except Exception:  # noqa: BLE001 - isolate single artist errors
                errors += 1

    try:
        if client is not None:
            await _process_all(client)
        else:
            async with httpx.AsyncClient(
                timeout=15.0, headers=headers, follow_redirects=True
            ) as local_client:
                await _process_all(local_client)
    finally:
        if cache_dirty:
            _save_mbid_cache(cache, mbid_cache_path)

    ok = errors < len(artists)
    detail = f"{len(all_events)} 条发行计划"
    if errors > 0:
        detail += f"（{errors} 位艺人查询异常已跳过）"

    return all_events, {
        "name": "MusicBrainz 发行日历",
        "domain": "music",
        "ok": ok,
        "skipped": False,
        "fetched": len(all_events),
        "detail": detail,
    }


async def collect_upcoming_events(
    config: dict[str, Any],
    *,
    mbid_cache_path: Path = MBID_CACHE_PATH,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run PandaScore and MusicBrainz upcoming collectors concurrently and safely."""
    results = await asyncio.gather(
        fetch_pandascore_cs2(config),
        fetch_musicbrainz_releases(config, mbid_cache_path=mbid_cache_path),
        return_exceptions=True,
    )

    events: list[dict[str, Any]] = []
    stats: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, BaseException):
            stats.append(
                {
                    "name": "Upcoming Collector",
                    "domain": "mixed",
                    "ok": False,
                    "skipped": False,
                    "fetched": 0,
                    "detail": f"{type(res).__name__}: {res}",
                }
            )
            continue
        ev_list, stat = res
        events.extend(ev_list)
        stats.append(stat)

    return events, stats
