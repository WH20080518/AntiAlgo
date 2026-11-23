"""Hard-rule filtering, LLM batch denoising, clustering, and anti-spoiler isolation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

from pipeline import (
    DEFAULT_DB_PATH,
    ROOT_DIR,
    get_blacklist_keywords,
    get_domain_preferences,
    get_enabled_source_names,
    get_focus_entities,
    get_max_summary_chars,
    get_spoiler_shield_domains,
    get_window_hours,
)
from pipeline.db import (
    compute_event_id,
    get_recent_clusters,
    get_recent_term_names,
    get_unprocessed_items,
    mark_items_filtered,
    normalize_event_start_time,
    update_item_processing,
    upsert_daily_term,
    upsert_upcoming_events,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_SUMMARY_CHARS = 800
DEFAULT_BATCH_SIZE = 12
AI_SUMMARY_MAX_CHARS = 90
PLACEHOLDER_API_KEYS = frozenset({"", "sk-your-api-key", "your-api-key"})

CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
CLUSTER_SLUG_RE = re.compile(r"[^a-z0-9-]+")

BATCH_SYSTEM_PROMPT = """你是一个极其克制、讲究“说人话”的高信噪比个人情报编辑（Anti-Algorithm Editor）。
你的任务是对输入的科技/开源、CS2 赛事、欧美与 K-pop 音乐情报进行严格准入筛选、去标题党、大白话汉化改写、同题合并与防剧透隔离。

必须严格遵守以下规则，并仅输出一个合法的 JSON 对象（不要使用 Markdown 代码块，不要输出任何额外解释）：

1. 【100% 简体中文与“说人话”防黑话规则】
   - 剥离情绪化渲染、夸张形容词与诱导点击措辞（如“震惊”、“史诗级”、“必看”、“重磅炸弹”等）。
   - `ai_title`：重写为冷静、客观的简体中文标题（不超过 28 个字），直陈主体与核心事件。
   - `ai_summary`：用通俗易懂的一句完整简体中文概括核心事实（建议 45~60 个字符，最多不超过 85 字符；必须写成完整句子，把英文缩写和专业术语用大白话讲明白，严禁半截断句）。
   - **防黑话规则**：遇到英文技术或行业术语（如 `WSGI`、`Formal Verification`、`Quantization`、`BO3`、`GC` 等），严禁生硬直译或原样照搬，必须用“中文大白话（附英文缩写）”解释用途；遇到普通读者可能不熟的人物、作者或冷门工具，必须用极简定语交代背景（如“Python 老牌网页接口插件作者…”）。

2. 【严格相关性门槛与领域准入 (`importance`: 0~5，宁缺毋滥；仅 >=3 会展示)】
   - **音乐领域（强白名单制）**：除非涉及 `focus_entities.music` 名单内的艺人/组合，或属于欧美流行主流一线大事件 / K-pop 女团与女 Solo 重要回归，否则所有不在白名单内的欧美摇滚、金属、地下乐队、小众独立音乐人（如 Marilyn Manson、Slipknot 等）、白名单外韩国男团、韩剧/韩综/演员八卦及打歌水帖**一律打 0 丢弃**。对于关注艺人/女团的 **YouTube 官方频道正式 MV（Official MV / M/V）、官方音源（Official Audio）、MV 预告片（MV Teaser）及正式舞台/官摄**，一律视为高价值发行事件（打 `4~5`）；非音乐类幕后花絮（BTS）、日常 vlog、周边带货与纯活动宣传片段仍打 `0` 丢弃。
   - **科技领域（去冷门杂讯）**：优先保留 AI 模型、Agent、开发工具、主流编程语言与核心开源框架的重要进展（打 3~5）；过于晦涩且无通用价值的冷门个人杂谈、老旧框架小众补丁、纯物理化学猎奇实验等杂讯直接打 0 或 1。
   - **电竞领域**：聚焦 CS2 一线国际赛事、`focus_entities.esports` 提及的战队选手与官方重大更新（打 3~5）；三线小比赛和无关八卦直接打 0。
   - 分级定义：
     - `0`：命中排除规则、非白名单冷门艺人/冷门杂讯、纯水文或广告（仍需简短填好 `ai_title` 与 `ai_summary` 以便入库标记已审，系统会自动丢弃不展示）。
     - `1~2`：边缘或低优先资讯（不进入高信噪比默认导出）。
     - `3`：明确符合偏好、对读者有实用信息量的有效进展。
     - `4`：高信号行业/技术突破或命中 `focus_entities` 的核心动态。
     - `5`：里程碑级重大发布、一线大赛关键赛果或核心关注实体重大新闻。

3. 【跨源同题合并 (`cluster_id`)】
   - 仅当多条新闻报道的是**同一个具体事件、同一场赛事或同一个项目发布**时，才为它们分配**完全相同**的简短小写英文 slug（仅含 `a-z`、`0-9` 和 `-`，例如 `lean-riemann-verification`、`mcp-v2-spec`）。
   - 如果某条新闻与 `existing_clusters` 中的已有事件属于同一事件，请直接复用该 `cluster_id`。
   - 如果是独立事件，必须填 `null`；切勿将同领域不同事件强行归为一类。

4. 【严格防剧透隔离 (`spoiler_payload`)】
   - 若内容涉及**比赛比分、胜负/淘汰/晋级/夺冠结果、MVP 归属**，或作品/演出的**关键剧情/惊喜曲目剧透**：
     - `ai_title` 与 `ai_summary` **严禁透露胜者、比分、淘汰情况或剧透结果**（请改写为“某某对决结果出炉，晋级席位已确定”等客观无剧透表达）。
     - 必须把所有剧透细节剥离到 `spoiler_payload` 对象中：
       `{"label": "赛果详情", "match": "对阵双方或作品名", "score": "比分(无则null)", "winner": "胜者/结果(无则null)", "summary": "一句完整剧透事实"}`
   - 若只是选手转会、赛程预告、版本发布、新歌发行等无比赛结果或剧透的普通新闻，`spoiler_payload` 必须严格为 `null`。

5. 【零额外爬虫提取日程 (`extracted_event`)】
   - 默认必须为 `null`。
   - **仅当原文明确写出未来（今日或之后）具体日历日期的发布、开赛或回归日程**（例如“将于 11 月 28 日发布新单曲”、“Major 将于 12 月 5 日 18:00 开赛”，或 YouTube MV Teaser 标题/视频简介写明“2026.11.28”、“November 28”等明确回归/发行日期，且 `importance >= 3`）时，才输出对象：
     `{"title": "简短中文日程标题", "subtitle": "赛事/专辑说明", "start_time": "YYYY-MM-DD 或 ISO-8601 时间", "precision": "date_only 或 datetime", "tag": "单曲/专辑/BO3/开赛"}`
   - 若日期模糊（如“即将”、“下个月”、“2027 年”）或属于已发生的过去事件，**严禁猜测编造，必须填 `null`**。

6. 【输出格式】
   输出结构必须严格为：
   {
     "items": [
       {
         "id": "原样保留输入中的短 id",
         "ai_title": "...",
         "ai_summary": "...",
         "importance": 3,
         "cluster_id": null,
         "spoiler_payload": null,
         "extracted_event": null
       }
     ]
   }"""

BATCH_USER_TEMPLATE = """请处理以下待降噪情报条目。当前本地日期为 {today_date}。

[用户重点关注实体 / 白名单 (focus_entities)]
{focus_entities_json}

[领域细分偏好与排除规则 (domain_preferences，命中 exclude 且不在白名单内请打 importance=0)]
{domain_preferences_json}

[默认启用防剧透隔离的领域 (spoiler_shield_domains，若含赛果/淘汰/比分务必隔离写入 spoiler_payload)]
{spoiler_domains_json}

[72 小时内已有事件聚类 (existing_clusters，若属同一事件请直接复用其 cluster_id)]
{existing_clusters_json}

[待处理条目列表 (请原样保留每条的 id 字段)]
{items_json}"""

BRIEFINGS_SYSTEM_PROMPT = """你是一个精简晨报编辑，负责从当日已筛选的高信噪比情报中提炼：
1. `top_briefings`：最多 3 条最核心的一句话中文速览；
2. `daily_term`：从当日高分情报里挑选 1 个最有学习价值的英文专业术语，并用“说人话”的简体中文解释清楚。

严格遵守以下要求，仅输出合法 JSON（不要 Markdown 代码块或多余文字）：
1. `top_briefings`：最多 3 条，每条 16~38 个简体汉字，通俗客观、严禁生硬黑话，严禁剧透（输入已剔除赛果细节，绝不可猜测比分与胜负）。尽量兼顾不同领域。
2. `daily_term`：优先从提供的当日卡片中挑选真实出现的英文专业术语（若无合适术语则选一个 AI/软件工程经典实用术语）：
   - `term_en`: 英文原词（如 "Formal Verification"）
   - `term_zh`: 简体中文译名（如 "形式化验证"）
   - `domain`: 所属领域（"tech" | "esports" | "music"）
   - `phonetic_or_abbr`: 常见缩写或简写（如 "FV"，无则填 ""）
   - `plain_explain`: 一到两句大白话解释（普通人也能秒懂的比喻或直白说明，不超过 60 字）
   - `in_context`: 结合今天某条情报说明它在新闻里的含义（不超过 65 字，严禁剧透）
3. 输出格式：
   {
     "top_briefings": ["简报1", "简报2", "简报3"],
     "daily_term": {
       "term_en": "...",
       "term_zh": "...",
       "domain": "tech",
       "phonetic_or_abbr": "...",
       "plain_explain": "...",
       "in_context": "..."
     }
   }"""

BRIEFINGS_USER_TEMPLATE = """请根据以下今日高优先情报卡片生成 `top_briefings` 与 1 个 `daily_term`：
{recent_terms_section}{cards_json}"""


class LLMConfigError(RuntimeError):
    """Raised when required OpenAI-compatible LLM environment variables are missing."""


def truncate_for_llm(text: str, max_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> str:
    """Collapse whitespace and truncate raw_summary to protect LLM token budget."""
    clean = " ".join(str(text or "").split()).strip()
    if max_chars <= 0 or len(clean) <= max_chars:
        return clean
    if max_chars == 1:
        return clean[:1]
    return clean[: max_chars - 1].rstrip() + "…"


def clamp_ai_summary(text: str, max_chars: int = AI_SUMMARY_MAX_CHARS) -> str:
    """Enforce the <=30-character concise factual summary constraint."""
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= max_chars:
        return clean
    stripped_punct = clean.rstrip("。；;！! ")
    if len(stripped_punct) <= max_chars:
        return stripped_punct
    return clean[: max_chars - 1].rstrip("，、。；： ") + "…"


def _compile_keyword_matcher(keyword: str) -> re.Pattern[str] | None:
    """Compile boundary-aware regex for ASCII terms; return None for CJK substring match."""
    if not keyword.isascii():
        return None
    prefix = r"(?<![a-z0-9])" if keyword[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if keyword[-1].isalnum() else ""
    return re.compile(f"{prefix}{re.escape(keyword)}{suffix}", re.IGNORECASE)


def apply_hard_rules(
    config: dict[str, Any], db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    """Filter unprocessed active items matching blacklist keywords (active window)."""
    window_hours = get_window_hours(config)
    keywords = get_blacklist_keywords(config)
    enabled_sources = get_enabled_source_names(config)

    candidates = get_unprocessed_items(
        max_age_hours=window_hours,
        limit=None,
        enabled_sources=enabled_sources,
        db_path=db_path,
    )
    if not candidates or not keywords:
        return {"checked": len(candidates), "filtered": 0, "matched_samples": []}

    matchers = [(kw, _compile_keyword_matcher(kw)) for kw in keywords]
    matched_hashes: list[str] = []
    matched_samples: list[dict[str, str]] = []

    for item in candidates:
        haystack = f"{item.get('title', '')}\n{item.get('raw_summary', '')}"
        haystack_lower = haystack.lower()
        hit: str | None = None
        for kw, pattern in matchers:
            if pattern is not None:
                if pattern.search(haystack):
                    hit = kw
                    break
            elif kw.lower() in haystack_lower:
                hit = kw
                break
        if hit is not None:
            matched_hashes.append(str(item["url_hash"]))
            if len(matched_samples) < 5:
                matched_samples.append(
                    {"title": str(item.get("title", "")), "keyword": hit}
                )

    filtered = mark_items_filtered(matched_hashes, db_path=db_path)
    return {
        "checked": len(candidates),
        "filtered": filtered,
        "matched_samples": matched_samples,
    }


def create_llm_client() -> tuple[OpenAI, str]:
    """Build an OpenAI-compatible client from .env and process environment variables."""
    load_dotenv(ROOT_DIR / ".env", override=False)

    base_url = (os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).strip()
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    model = (os.getenv("LLM_MODEL") or "").strip()

    if not api_key or api_key in PLACEHOLDER_API_KEYS:
        raise LLMConfigError(
            "未检测到有效的 LLM_API_KEY。请在项目根目录创建 .env（可复制 .env.example）"
            "并配置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，或使用 --skip-llm 跳过模型阶段。"
        )
    if not model:
        raise LLMConfigError(
            "未检测到 LLM_MODEL。请在 .env 或环境变量中配置 LLM_MODEL（例如 gpt-4o-mini）。"
        )

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    return client, model


def parse_llm_json(raw_text: str) -> dict[str, Any]:
    """Clean markdown fences / trailing commas and parse structured JSON output."""
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("Empty LLM response")

    cleaned = CODE_FENCE_RE.sub("", text).strip()

    first_obj = cleaned.find("{")
    last_obj = cleaned.rfind("}")
    first_arr = cleaned.find("[")
    last_arr = cleaned.rfind("]")

    candidate = cleaned
    if first_obj != -1 and last_obj > first_obj and (
        first_arr == -1 or first_obj < first_arr
    ):
        candidate = cleaned[first_obj : last_obj + 1]
    elif first_arr != -1 and last_arr > first_arr:
        candidate = cleaned[first_arr : last_arr + 1]

    candidate = TRAILING_COMMA_RE.sub(r"\1", candidate)
    parsed = json.loads(candidate)

    if isinstance(parsed, list):
        return {"items": parsed}
    if isinstance(parsed, dict):
        return parsed
    raise ValueError(f"Expected JSON object or array, got {type(parsed).__name__}")


def _sanitize_cluster_id(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip().lower()
    if not text or text in {"null", "none", "n/a"}:
        return None
    slug = CLUSTER_SLUG_RE.sub("-", text).strip("-")
    return slug[:64] if slug else None


def _sanitize_spoiler_payload(raw_value: Any) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped or stripped.lower() in {"null", "none"}:
            return None
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, dict):
                raw_value = decoded
            else:
                return {"label": "剧透详情", "summary": stripped}
        except json.JSONDecodeError:
            return {"label": "剧透详情", "summary": stripped}

    if not isinstance(raw_value, dict):
        return None

    cleaned: dict[str, Any] = {}
    for key, val in raw_value.items():
        if val is not None and str(val).strip() != "":
            cleaned[str(key)] = val
    return cleaned if cleaned else None


def _sanitize_importance(raw_value: Any) -> int:
    try:
        score = int(raw_value)
    except (TypeError, ValueError):
        return 3
    return max(0, min(5, score))


def _sanitize_extracted_event(
    raw_event: Any,
    *,
    domain: str,
    source_url: str,
    importance: int,
    today_local: date,
    now_utc: datetime,
) -> dict[str, Any] | None:
    if importance < 3 or not isinstance(raw_event, dict):
        return None
    title = " ".join(str(raw_event.get("title") or "").split()).strip()
    raw_start = str(raw_event.get("start_time") or "").strip()
    if not title or not raw_start:
        return None

    raw_prec = str(raw_event.get("precision") or "").strip()
    if raw_prec not in {"datetime", "date_only"}:
        raw_prec = "date_only" if len(raw_start) == 10 else "datetime"

    norm_start = normalize_event_start_time(raw_start, precision=raw_prec)
    if not norm_start:
        return None

    if raw_prec == "date_only":
        ev_date = date.fromisoformat(norm_start[:10])
        if ev_date < today_local or ev_date > today_local + timedelta(days=90):
            return None
    else:
        ev_dt = datetime.fromisoformat(norm_start.replace("Z", "+00:00"))
        if ev_dt < now_utc - timedelta(hours=3) or ev_dt > now_utc + timedelta(
            days=90
        ):
            return None

    subtitle = " ".join(str(raw_event.get("subtitle") or "").split()).strip()
    tag = " ".join(str(raw_event.get("tag") or "").split()).strip() or "日程"

    return {
        "event_id": compute_event_id(domain, title, norm_start),
        "domain": domain,
        "title": title,
        "subtitle": subtitle,
        "start_time": norm_start,
        "precision": raw_prec,
        "tag": tag,
        "source": "llm_extracted",
        "url": source_url,
        "priority": importance,
    }


def _call_chat_json(
    client: Any, model: str, messages: list[dict[str, str]]
) -> str:
    """Invoke chat.completions.create with json_object mode and provider fallback."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except BadRequestError:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
        )

    content = response.choices[0].message.content
    return str(content or "")


def _validate_batch_response(
    parsed: dict[str, Any], valid_ids: set[str]
) -> list[dict[str, Any]]:
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Missing 'items' array in LLM response")

    matched = [
        entry
        for entry in raw_items
        if isinstance(entry, dict) and str(entry.get("id", "")).strip() in valid_ids
    ]
    if not matched:
        raise ValueError("No valid item IDs returned in LLM response")
    return matched


def process_items_with_llm(
    config: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    limit: int | None = None,
    batch_size: int | None = None,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Batch-process unprocessed 72h items via OpenAI-compatible LLM and persist results."""
    settings = config.get("settings") or {}
    max_age_hours = get_window_hours(config)
    eff_batch_size = max(1, int(batch_size or settings.get("llm_batch_size", DEFAULT_BATCH_SIZE)))
    max_summary_chars = get_max_summary_chars(config)
    enabled_sources = get_enabled_source_names(config)

    candidates = get_unprocessed_items(
        max_age_hours=max_age_hours,
        limit=limit,
        enabled_sources=enabled_sources,
        db_path=db_path,
    )
    if not candidates:
        return {
            "candidates": 0,
            "batches": 0,
            "processed": 0,
            "dropped": 0,
            "degraded": 0,
            "events_extracted": 0,
            "processed_hashes": [],
        }

    if client is None or model is None:
        client, model = create_llm_client()

    today_local = datetime.now().astimezone().date()
    today_date_str = today_local.isoformat()
    now_utc = datetime.now(timezone.utc)

    focus_entities = get_focus_entities(config)
    focus_json = json.dumps(focus_entities, ensure_ascii=False, indent=2)
    domain_preferences = get_domain_preferences(config)
    domain_preferences_json = json.dumps(
        domain_preferences, ensure_ascii=False, indent=2
    )
    spoiler_domains = get_spoiler_shield_domains(config)
    spoiler_domains_json = json.dumps(spoiler_domains, ensure_ascii=False)

    batches = [
        candidates[i : i + eff_batch_size]
        for i in range(0, len(candidates), eff_batch_size)
    ]

    processed_hashes: list[str] = []
    dropped_count = 0
    degraded_count = 0
    events_extracted_count = 0

    for batch_idx, batch in enumerate(batches, start=1):
        batch_map: dict[str, dict[str, Any]] = {}
        payload_items: list[dict[str, Any]] = []

        for idx, row in enumerate(batch, start=1):
            short_id = str(idx)
            batch_map[short_id] = row
            payload_items.append(
                {
                    "id": short_id,
                    "domain": str(row.get("domain", "tech")),
                    "source_name": str(row.get("source_name", "")),
                    "title": str(row.get("title", "")),
                    "raw_summary": truncate_for_llm(
                        str(row.get("raw_summary", "")), max_chars=max_summary_chars
                    ),
                    "published_at": str(row.get("published_at", "")),
                }
            )

        existing_clusters = get_recent_clusters(
            max_age_hours=max_age_hours,
            limit=25,
            enabled_sources=enabled_sources,
            db_path=db_path,
        )
        user_prompt = BATCH_USER_TEMPLATE.format(
            today_date=today_date_str,
            focus_entities_json=focus_json,
            domain_preferences_json=domain_preferences_json,
            spoiler_domains_json=spoiler_domains_json,
            existing_clusters_json=json.dumps(
                existing_clusters, ensure_ascii=False, indent=2
            ),
            items_json=json.dumps(payload_items, ensure_ascii=False, indent=2),
        )
        messages = [
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        valid_entries: list[dict[str, Any]] | None = None
        last_raw = ""

        try:
            last_raw = _call_chat_json(client, model, messages)
            parsed = parse_llm_json(last_raw)
            valid_entries = _validate_batch_response(parsed, set(batch_map.keys()))
        except Exception as first_exc:  # noqa: BLE001 - retry once then degrade gracefully
            print(
                f"[Processor] Batch {batch_idx}/{len(batches)} parse/API issue "
                f"({type(first_exc).__name__}: {first_exc}); retrying once..."
            )
            try:
                retry_messages = [
                    *messages,
                    {"role": "assistant", "content": last_raw or "{}"},
                    {
                        "role": "user",
                        "content": (
                            "上一条响应不是合法的严格 JSON（或缺少匹配的 items 条目）。"
                            '请仅输出严格 JSON 对象，格式为 {"items": [...]}，'
                            "不要任何 Markdown 代码块或说明文字。"
                        ),
                    },
                ]
                retry_raw = _call_chat_json(client, model, retry_messages)
                parsed_retry = parse_llm_json(retry_raw)
                valid_entries = _validate_batch_response(
                    parsed_retry, set(batch_map.keys())
                )
            except Exception as retry_exc:  # noqa: BLE001
                print(
                    f"[Processor] Batch {batch_idx}/{len(batches)} degraded gracefully "
                    f"({type(retry_exc).__name__}: {retry_exc}); keeping raw items."
                )
                degraded_count += len(batch)
                continue

        seen_short_ids: set[str] = set()
        batch_extracted_events: list[dict[str, Any]] = []
        for entry in valid_entries:
            short_id = str(entry.get("id", "")).strip()
            if short_id in seen_short_ids:
                continue
            seen_short_ids.add(short_id)

            source_row = batch_map[short_id]
            ai_title = (
                " ".join(str(entry.get("ai_title") or "").split()).strip()
                or str(source_row["title"])
            )
            raw_ai_summary = " ".join(
                str(entry.get("ai_summary") or "").split()
            ).strip()
            fallback_summary = str(
                source_row.get("raw_summary") or source_row["title"]
            )
            ai_summary = clamp_ai_summary(
                raw_ai_summary or fallback_summary, max_chars=AI_SUMMARY_MAX_CHARS
            )

            importance = _sanitize_importance(entry.get("importance"))
            if importance == 0:
                dropped_count += 1
            cluster_id = _sanitize_cluster_id(entry.get("cluster_id"))
            spoiler_payload = _sanitize_spoiler_payload(entry.get("spoiler_payload"))

            extracted_ev = _sanitize_extracted_event(
                entry.get("extracted_event"),
                domain=str(source_row.get("domain", "tech")),
                source_url=str(source_row.get("url", "")),
                importance=importance,
                today_local=today_local,
                now_utc=now_utc,
            )
            if extracted_ev is not None:
                batch_extracted_events.append(extracted_ev)

            update_item_processing(
                str(source_row["url_hash"]),
                ai_title=ai_title,
                ai_summary=ai_summary,
                importance=importance,
                cluster_id=cluster_id,
                related_links=source_row.get("related_links", "[]"),
                spoiler_payload=spoiler_payload,
                db_path=db_path,
            )
            processed_hashes.append(str(source_row["url_hash"]))

        if batch_extracted_events:
            events_extracted_count += upsert_upcoming_events(
                batch_extracted_events, db_path=db_path
            )

        omitted = len(batch) - len(seen_short_ids)
        if omitted > 0:
            degraded_count += omitted

    return {
        "candidates": len(candidates),
        "batches": len(batches),
        "processed": len(processed_hashes),
        "dropped": dropped_count,
        "degraded": degraded_count,
        "events_extracted": events_extracted_count,
        "processed_hashes": processed_hashes,
    }


def _sanitize_daily_term(raw_value: Any) -> dict[str, str] | None:
    if not isinstance(raw_value, dict):
        return None
    term_en = " ".join(str(raw_value.get("term_en") or "").split()).strip()
    term_zh = " ".join(str(raw_value.get("term_zh") or "").split()).strip()
    plain_explain = " ".join(str(raw_value.get("plain_explain") or "").split()).strip()
    in_context = " ".join(str(raw_value.get("in_context") or "").split()).strip()
    if not (term_en and term_zh and plain_explain and in_context):
        return None

    raw_domain = str(raw_value.get("domain") or "tech").strip().lower()
    domain = raw_domain if raw_domain in {"tech", "esports", "music"} else "tech"
    abbr = " ".join(str(raw_value.get("phonetic_or_abbr") or "").split()).strip()

    return {
        "term_en": term_en,
        "term_zh": term_zh,
        "domain": domain,
        "phonetic_or_abbr": abbr,
        "plain_explain": plain_explain,
        "in_context": in_context,
    }


def generate_briefings_and_term_llm(
    items: list[dict[str, Any]],
    client: Any,
    model: str,
    max_count: int = 3,
    *,
    recent_terms: list[str] | None = None,
    db_path: Path | None = None,
    persist_term: bool = True,
    term_date: str | None = None,
) -> tuple[list[str] | None, dict[str, str] | None]:
    """Synthesize up to 3 spoiler-free top_briefings and 1 daily_term in a single LLM call."""
    eligible = [item for item in items if int(item.get("importance", 0)) >= 3]
    if not eligible or max_count <= 0:
        return None, None

    eff_recent_terms = recent_terms
    if eff_recent_terms is None and db_path is not None:
        eff_recent_terms = get_recent_term_names(days=30, db_path=db_path)

    recent_terms_section = ""
    if eff_recent_terms:
        recent_terms_section = (
            "[近 30 天已收录术语（以下术语近 30 天已收录，请优先从今日情报中挑选其他未学过的专业术语，避免重复）]\n"
            f"{json.dumps(eff_recent_terms, ensure_ascii=False)}\n\n"
            "[今日高优先情报卡片]\n"
        )

    cards_payload = [
        {
            "domain": item.get("domain", ""),
            "source_name": item.get("source_name", ""),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "importance": item.get("importance", 3),
        }
        for item in eligible[:12]
    ]
    user_prompt = BRIEFINGS_USER_TEMPLATE.format(
        recent_terms_section=recent_terms_section,
        cards_json=json.dumps(cards_payload, ensure_ascii=False, indent=2),
    )
    messages = [
        {"role": "system", "content": BRIEFINGS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = _call_chat_json(client, model, messages)
        parsed = parse_llm_json(raw)
        briefings_raw = parsed.get("top_briefings")
        briefings: list[str] | None = None
        if isinstance(briefings_raw, list):
            cleaned = [
                " ".join(str(line).split()).strip()
                for line in briefings_raw
                if str(line).strip()
            ]
            if cleaned:
                briefings = cleaned[:max_count]
        daily_term = _sanitize_daily_term(parsed.get("daily_term"))
        if daily_term is not None and persist_term and db_path is not None:
            upsert_daily_term(daily_term, term_date=term_date, db_path=db_path)
        return briefings, daily_term
    except Exception as exc:  # noqa: BLE001 - fall back cleanly
        print(
            f"[Processor] Briefings & daily_term LLM fallback ({type(exc).__name__}: {exc})."
        )
        return None, None


def generate_top_briefings_llm(
    items: list[dict[str, Any]],
    client: Any,
    model: str,
    max_count: int = 3,
) -> list[str] | None:
    """Synthesize up to 3 spoiler-free top_briefings from importance>=3 merged cards."""
    briefings, _ = generate_briefings_and_term_llm(
        items, client, model, max_count=max_count
    )
    return briefings
