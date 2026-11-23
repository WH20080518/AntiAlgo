"""Unified CLI entrypoint for the Anti-Algorithm Personal Hub pipeline."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

from pipeline import (
    DAILY_STATE_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_DIGEST_PATH,
    DEFAULT_FALLBACK_DAILY_TERM,
    MOCK_DB_PATH,
    ROOT_DIR,
    load_config,
    load_daily_state,
    save_daily_state,
)
from pipeline.build_digest import build_digest, load_merged_digest_items
from pipeline.collectors.rss import collect_rss_sources
from pipeline.collectors.upcoming import collect_upcoming_events
from pipeline.db import (
    backfill_daily_term_from_state,
    cleanup_expired_upcoming,
    compute_url_hash,
    init_db,
    set_term_mastered,
    upsert_daily_term,
    upsert_raw_items,
    upsert_upcoming_events,
)
from pipeline.notifier import push_digest_webhook
from pipeline.processor import (
    LLMConfigError,
    apply_hard_rules,
    create_llm_client,
    generate_briefings_and_term_llm,
    process_items_with_llm,
)


def _configure_stdio() -> None:
    if sys.stdout is None or sys.stderr is None:
        log_path = ROOT_DIR / "data" / "pipeline.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("a", encoding="utf-8", buffering=1)
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        log_fh.write(f"\n=== Pipeline run started at {ts} ===\n")
        if sys.stdout is None:
            sys.stdout = log_fh
        if sys.stderr is None:
            sys.stderr = log_fh
        return

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _generate_mock_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()

    def _iso_ago(hours: int) -> str:
        return (now_utc - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _iso_ahead(hours: int) -> str:
        return (now_local + timedelta(hours=hours)).isoformat(timespec="seconds")

    items: list[dict[str, Any]] = [
        {
            "url_hash": compute_url_hash("https://github.blog/news-insights/mock-mcp-spec"),
            "domain": "tech",
            "source_name": "GitHub Blog",
            "title": "Model Context Protocol v2.0 规范进入候选发布阶段",
            "raw_summary": "新增异步长任务回调与细粒度权限作用域声明，多款主流桌面客户端已完成原型适配。",
            "ai_title": "MCP v2.0 规范进入候选阶段，新增异步任务与细粒度权限",
            "ai_summary": "支持长任务回调与作用域隔离，主流客户端已启动兼容。",
            "url": "https://github.blog/news-insights/mock-mcp-spec",
            "published_at": _iso_ago(2),
            "importance": 5,
            "cluster_id": "cluster-mcp-v2",
            "related_links": [
                "https://news.ycombinator.com/item?id=42000001",
                "https://huggingface.co/blog/mock-mcp-ecosystem",
            ],
            "spoiler_payload": None,
        },
        {
            "url_hash": compute_url_hash("https://hnrss.org/mock-rust-compiler-speedup"),
            "domain": "tech",
            "source_name": "Hacker News (High Signal)",
            "title": "Rust 编译器并行前端默认启用，增量构建耗时下降约 24%",
            "raw_summary": "Nightly 工具链基准测试显示大型工作区冷编译与增量检查均获得明显吞吐提升。",
            "ai_title": "Rust 并行前端将默认启用，大型工作区增量编译提速 24%",
            "ai_summary": "基准测试确认多线程类型检查可稳定降低构建等待时间。",
            "url": "https://hnrss.org/mock-rust-compiler-speedup",
            "published_at": _iso_ago(5),
            "importance": 4,
            "cluster_id": None,
            "related_links": [],
            "spoiler_payload": None,
        },
        {
            "url_hash": compute_url_hash("https://www.vlr.gg/mock-vct-pacific-finals"),
            "domain": "esports",
            "source_name": "VLR.gg",
            "title": "VCT Pacific 季后赛关键对决结束，决出首支晋级总决赛战队",
            "raw_summary": "胜者组决赛打满四局，双方在亚海悬城与莲华古城展开高强度加时拉锯。",
            "ai_title": "VCT Pacific 胜者组决赛落幕，首支总决赛席位已确定",
            "ai_summary": "双方历经四图高强度对抗决出直通资格（剧透详情已隔离）。",
            "url": "https://www.vlr.gg/mock-vct-pacific-finals",
            "published_at": _iso_ago(3),
            "importance": 5,
            "cluster_id": "cluster-vct-pacific-ubf",
            "related_links": [
                "https://www.vlr.gg/mock-vct-pacific-recap",
            ],
            "spoiler_payload": {
                "match": "Paper Rex vs Gen.G",
                "score": "3 - 1",
                "winner": "Gen.G",
                "summary": "Gen.G 以 3-1 击败 Paper Rex 率先晋级总决赛。",
            },
        },
        {
            "url_hash": compute_url_hash("https://www.vlr.gg/mock-worlds-schedule"),
            "domain": "esports",
            "source_name": "VLR.gg",
            "title": "全球总决赛瑞士轮赛制微调，取消同赛区首轮回避限制",
            "raw_summary": "官方公布下赛季国际赛事竞赛手册修订草案，强调对阵随机性与跨区对抗密度。",
            "ai_title": "国际赛事瑞士轮拟取消首轮同区回避规则",
            "ai_summary": "新规旨在提高早期强强对话概率，赛程总天数保持不变。",
            "url": "https://www.vlr.gg/mock-worlds-schedule",
            "published_at": _iso_ago(11),
            "importance": 3,
            "cluster_id": None,
            "related_links": [],
            "spoiler_payload": None,
        },
        {
            "url_hash": compute_url_hash("https://daily.bandcamp.com/mock-ambient-release"),
            "domain": "music",
            "source_name": "Bandcamp Daily",
            "title": "坂本龙一档案库公开 1998 年未发行钢琴独奏母带修复版",
            "raw_summary": "收录六首现场即兴录音，以 24-bit/96kHz 无损格式同步上线独立发行平台。",
            "ai_title": "坂本龙一 1998 年未发行钢琴即兴录音修复上线",
            "ai_summary": "六首现场母带经高清修复，现已开放无损数字试听与下载。",
            "url": "https://daily.bandcamp.com/mock-ambient-release",
            "published_at": _iso_ago(8),
            "importance": 4,
            "cluster_id": None,
            "related_links": [],
            "spoiler_payload": None,
        },
        {
            "url_hash": compute_url_hash("https://daily.bandcamp.com/mock-fred-again-ep"),
            "domain": "music",
            "source_name": "Bandcamp Daily",
            "title": "Fred again.. 联合多位伦敦制作人发布 4 曲深夜 Club 采样 EP",
            "raw_summary": "作品延续人声切片与碎拍合成器质感，黑胶限量版将于下周开启预售。",
            "ai_title": "Fred again.. 发布 4 曲伦敦制作人合作采样 EP",
            "ai_summary": "延续人声切片与碎拍电子风格，实体黑胶随后开放预订。",
            "url": "https://daily.bandcamp.com/mock-fred-again-ep",
            "published_at": _iso_ago(16),
            "importance": 3,
            "cluster_id": None,
            "related_links": [],
            "spoiler_payload": None,
        },
    ]

    today_local = now_local.date()

    def _date_ahead(days: int) -> str:
        return (today_local + timedelta(days=days)).isoformat()

    upcoming: list[dict[str, Any]] = [
        {
            "domain": "esports",
            "title": "Team Spirit vs MOUZ",
            "subtitle": "BLAST Premier 世界总决赛",
            "start_time": _iso_ahead(5),
            "precision": "datetime",
            "tag": "BO3",
            "source": "pandascore",
            "url": "https://www.twitch.tv/blastpremier",
        },
        {
            "domain": "music",
            "title": "aespa《Whiplash (Remix EP)》",
            "subtitle": "预定发行 EP",
            "start_time": _date_ahead(0),
            "precision": "date_only",
            "tag": "EP",
            "source": "musicbrainz",
            "url": "https://musicbrainz.org/",
        },
        {
            "domain": "music",
            "title": "Billie Eilish 全新单曲正式上线",
            "subtitle": "官网倒计时预告确认发行日",
            "start_time": _date_ahead(4),
            "precision": "date_only",
            "tag": "单曲",
            "source": "llm_extracted",
            "url": "https://pitchfork.com/",
        },
    ]
    return items, upcoming


def _print_source_report(source_stats: list[dict[str, Any]]) -> None:
    print("\n[RSS Collection]")
    for stat in source_stats:
        status = "OK  " if stat["ok"] else "FAIL"
        detail = (
            f"{stat['fetched']} items"
            if stat["ok"]
            else f"error: {stat['error']}"
        )
        print(f"  [{status}] {stat['name']} ({stat['domain']}) -> {detail}")


def _print_upcoming_report(upcoming_stats: list[dict[str, Any]]) -> None:
    print("[Upcoming Collection]")
    for stat in upcoming_stats:
        if stat.get("skipped"):
            tag = "SKIP"
        elif stat.get("ok"):
            tag = "OK  "
        else:
            tag = "FAIL"
        print(f"  [{tag}] {stat['name']} ({stat['domain']}) -> {stat['detail']}")


def run_pipeline(
    *,
    skip_llm: bool,
    mock: bool,
    skip_fetch: bool,
    skip_upcoming: bool = False,
    limit: int | None,
    batch_size: int | None,
    config_path: Path,
    output_path: Path,
) -> int:
    """Execute ingestion, hard rules, optional LLM processing, and static digest export."""
    config = load_config(config_path)

    if mock:
        if MOCK_DB_PATH.exists():
            MOCK_DB_PATH.unlink()
        init_db(MOCK_DB_PATH)
        mock_items, mock_upcoming = _generate_mock_data()
        now_local = datetime.now().astimezone().date()
        mock_historical_terms = [
            {
                "term_en": "Model Context Protocol",
                "term_zh": "模型上下文协议",
                "domain": "tech",
                "phonetic_or_abbr": "MCP",
                "plain_explain": "让 AI 助手像插 USB 接口一样，用同一套标准去安全读取本地文件、数据库和外部工具的通用协议。",
                "in_context": "今天 GitHub 博客提到 MCP 2.0 规范进入候选发布阶段，重点增强了长任务和权限隔离。",
                "date": now_local.isoformat(),
            },
            {
                "term_en": "Speculative Decoding",
                "term_zh": "投机采样解码",
                "domain": "tech",
                "phonetic_or_abbr": "Draft-Verify",
                "plain_explain": "先让一个小模型飞快猜出后面几个词，再让大模型一口气并行校验，既不降智又能翻倍提速的推理技巧。",
                "in_context": "近期开源推理引擎普遍借它显著压低了长文本输出延迟。",
                "date": (now_local - timedelta(days=1)).isoformat(),
            },
            {
                "term_en": "Eco Round",
                "term_zh": "经济局 / 攒钱局",
                "domain": "esports",
                "phonetic_or_abbr": "Eco",
                "plain_explain": "战队刻意不买或只买极便宜手枪与基础道具，省下资金确保下一回合全员起全枪全弹的战术回合。",
                "in_context": "BO3 关键决胜图里一次成功的纯 Eco 翻盘往往能直接击穿对手经济链。",
                "date": (now_local - timedelta(days=2)).isoformat(),
            },
            {
                "term_en": "B-Side",
                "term_zh": "B 面曲 / 非主打收录曲",
                "domain": "music",
                "phonetic_or_abbr": "B-Side",
                "plain_explain": "源自黑胶唱片背面，现指随单曲或专辑一同上线但未作为首波打歌宣传的优质收录曲。",
                "in_context": "不少关注女团与流行歌手会在回归期放出质感不输主打的 B-Side 曲目。",
                "date": (now_local - timedelta(days=3)).isoformat(),
            },
        ]
        for mock_term in mock_historical_terms:
            upsert_daily_term(
                mock_term, term_date=mock_term["date"], db_path=MOCK_DB_PATH
            )
        set_term_mastered("Model Context Protocol", True, db_path=MOCK_DB_PATH)
        mock_daily_term = dict(mock_historical_terms[0])
        inserted, skipped = upsert_raw_items(mock_items, db_path=MOCK_DB_PATH)
        digest = build_digest(
            config,
            db_path=MOCK_DB_PATH,
            output_path=output_path,
            upcoming_override=mock_upcoming,
            daily_term_override=mock_daily_term,
            filter_enabled_sources=False,
            require_processed=True,
        )
        print("[Mock Mode]")
        print(f"  DB: {MOCK_DB_PATH} (inserted={inserted}, skipped={skipped})")
        print(
            f"  Digest: {output_path} "
            f"(items={len(digest['items'])}, "
            f"briefings={len(digest['top_briefings'])}, "
            f"upcoming={len(digest['upcoming'])}, "
            f"term={digest['daily_term']['term_en']})"
        )
        return 0

    init_db(DEFAULT_DB_PATH)
    backfill_daily_term_from_state(DEFAULT_DB_PATH, DAILY_STATE_PATH)
    cleaned_upcoming = cleanup_expired_upcoming(db_path=DEFAULT_DB_PATH)
    if cleaned_upcoming > 0:
        print(f"[Upcoming] Cleaned {cleaned_upcoming} expired events.")

    if skip_fetch:
        print("[RSS Collection] Skipped (--skip-fetch).")
    else:
        raw_items, source_stats = asyncio.run(collect_rss_sources(config))
        _print_source_report(source_stats)
        inserted, skipped = upsert_raw_items(raw_items, db_path=DEFAULT_DB_PATH)
        print(
            f"\n[SQLite Upsert] db={DEFAULT_DB_PATH} "
            f"fetched={len(raw_items)} inserted={inserted} deduplicated={skipped}"
        )

    if skip_upcoming or skip_fetch:
        print("[Upcoming Collection] Skipped.")
    else:
        upcoming_events, upcoming_stats = asyncio.run(
            collect_upcoming_events(config)
        )
        _print_upcoming_report(upcoming_stats)
        up_written = upsert_upcoming_events(
            upcoming_events, db_path=DEFAULT_DB_PATH
        )
        print(f"[Upcoming Upsert] upserted={up_written}")

    hard_stats = apply_hard_rules(config, db_path=DEFAULT_DB_PATH)
    print(
        f"[Hard Rules] checked={hard_stats['checked']} "
        f"filtered={hard_stats['filtered']}"
    )

    top_briefings_override: list[str] | None = None
    daily_term_override: dict[str, Any] | None = None
    require_processed = not skip_llm
    min_importance_override: int | None = 1 if skip_llm else None

    if skip_llm:
        print("[Processor] Skipped LLM processing (--skip-llm).")
    else:
        try:
            client, model = create_llm_client()
        except LLMConfigError as exc:
            print(f"[LLM Config Error] {exc}")
            return 2

        llm_stats = process_items_with_llm(
            config,
            db_path=DEFAULT_DB_PATH,
            limit=limit,
            batch_size=batch_size,
            client=client,
            model=model,
        )
        print(
            f"[Processor] candidates={llm_stats['candidates']} "
            f"batches={llm_stats['batches']} "
            f"processed={llm_stats['processed']} "
            f"dropped={llm_stats['dropped']} "
            f"events_extracted={llm_stats.get('events_extracted', 0)} "
            f"degraded={llm_stats['degraded']}"
        )

        merged_items, _ = load_merged_digest_items(
            config, db_path=DEFAULT_DB_PATH, require_processed=True
        )
        cached_state = load_daily_state()
        cached_term = (
            cached_state.get("daily_term")
            if isinstance(cached_state, dict)
            else None
        )

        if llm_stats["processed"] == 0 and isinstance(cached_term, dict):
            top_briefings_override = cached_state.get("top_briefings")
            daily_term_override = cached_term
        elif merged_items:
            gen_briefings, gen_term = generate_briefings_and_term_llm(
                merged_items,
                client=client,
                model=model,
                max_count=3,
                db_path=DEFAULT_DB_PATH,
            )
            top_briefings_override = gen_briefings
            has_cached_term = isinstance(cached_term, dict) and bool(
                cached_term.get("term_en")
            )
            if has_cached_term:
                daily_term_override = cached_term
            elif gen_term:
                daily_term_override = gen_term
            else:
                daily_term_override = dict(DEFAULT_FALLBACK_DAILY_TERM)
                upsert_daily_term(daily_term_override, db_path=DEFAULT_DB_PATH)
            save_daily_state(top_briefings_override, daily_term_override)

    _, clusters_merged = load_merged_digest_items(
        config,
        db_path=DEFAULT_DB_PATH,
        require_processed=require_processed,
        min_importance=min_importance_override,
    )
    digest = build_digest(
        config,
        db_path=DEFAULT_DB_PATH,
        output_path=output_path,
        top_briefings_override=top_briefings_override,
        daily_term_override=daily_term_override,
        require_processed=require_processed,
        min_importance=min_importance_override,
    )
    print(
        f"[Digest] Exported {len(digest['items'])} items "
        f"(clusters_merged={clusters_merged}), "
        f"{len(digest['top_briefings'])} briefings, "
        f"term={digest['daily_term']['term_en']}, "
        f"{len(digest['upcoming'])} upcoming -> {output_path}"
    )

    try:
        wh_res = push_digest_webhook(digest, config=config)
        sent_channels = wh_res.get("sent_channels") or []
        failed_channels = wh_res.get("failed") or {}
        if sent_channels or failed_channels:
            print(
                f"[Webhook] Digest push: items={wh_res.get('item_count', 0)}, "
                f"sent={sent_channels}, failed={list(failed_channels.keys())}"
            )
    except Exception as exc:  # noqa: BLE001 - never let webhook push fail pipeline
        print(f"[Webhook] Digest push skipped due to error: {type(exc).__name__}: {exc}")

    return 0


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Run the Anti-Algorithm Personal Hub ingestion and digest pipeline."
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip the LLM denoising/clustering stage.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Generate offline mock data into data/intel_mock.db and web/public/digest.json.",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip RSS network fetching and process existing SQLite entries directly.",
    )
    parser.add_argument(
        "--skip-upcoming",
        action="store_true",
        help="Skip PandaScore & MusicBrainz upcoming schedule collection.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of unprocessed items to send to the LLM in this run.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override LLM batch size (defaults to config/sources.yaml settings).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to sources.yaml configuration.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DIGEST_PATH,
        help="Output path for digest.json.",
    )
    args = parser.parse_args()

    raise SystemExit(
        run_pipeline(
            skip_llm=args.skip_llm,
            mock=args.mock,
            skip_fetch=args.skip_fetch,
            skip_upcoming=args.skip_upcoming,
            limit=args.limit,
            batch_size=args.batch_size,
            config_path=args.config,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
