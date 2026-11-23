<div align="center">

# AntiAlgo

**反算法个人情报站：夺回你的注意力控制权**

一个本地优先、采集与阅读彻底解耦的高信噪比个人情报站，聚焦 **AI 与开源**、**CS2 赛事**、**音乐发行** 三大领域，拒绝算法推荐、标题党与信息流焦虑。

[![Python](https://img.shields.io/badge/Python-%3E%3D3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-6-646CFF?style=flat-square&logo=vite&logoColor=white)](https://vite.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-22C55E?style=flat-square)](LICENSE)

<!-- ![AntiAlgo Preview](docs/preview.png) -->

</div>

---

## 核心特性

- **多源异步采集**：
  - 支持标准 RSS / Atom 订阅，同域名请求自动串行并间隔限流（`same_host_delay_seconds`，默认 `2.0` 秒），避免触发反爬或限频。
  - 支持 YouTube 官方频道原生 Atom Feed，内置 `exclude_shorts`（过滤 Shorts）与 `include_keywords`（关键词白名单）前置筛选。
  - 支持 PandaScore CS2 赛程日历（可选配置）与 MusicBrainz 关注艺人发行日历（带本地 MBID 缓存与礼貌限速）。
- **两级降噪与结构化加工**：
  - **硬规则初筛**：基于 `blacklist_keywords` 中的中/英文黑名单拦截营销号、标题党、八卦与菠菜噪音，零 Token 消耗。
  - **LLM 批量精加工**：接入任意 OpenAI 兼容接口，完成客观中文改写、大白话防黑话摘要、0–5 级重要度评分、跨源同题聚类（`cluster_id`）、赛果防剧透剥离（`spoiler_payload`）、未来明确日程提取，以及每日 3 条速览 + 1 个「每日术语」（30 天防重并同步维护掌握状态）。
- **纯本地服务与静音桌面通知**：
  - 基于 Python 标准库 `ThreadingHTTPServer` 的本地静态站与 API 服务（默认 `http://127.0.0.1:8765`），支持前端一键刷新、切换通知开关、同步术语掌握进度。
  - 内置 Windows 10/11 原生静音 Toast 桌面通知引擎：支持 CS2 赛前 15 分钟无剧透提醒（可穿透免打扰时段）与高优先音乐发行合并提醒（防刷屏 + 夜间免打扰 + 本地去重）。
- **键盘优先阅读体验**：
  - 前端基于 React 19 + Tailwind CSS v4 构建暗色 Bento 看板，支持赛果防剧透毛玻璃遮罩、全键盘流式清零与历史术语本管理。

---

## 技术栈

| 模块 | 技术选型 | 说明 |
| --- | --- | --- |
| 后端采集与管道 | Python >=3.11、`httpx`、`feedparser`、`PyYAML` | 异步多源抓取、硬规则过滤、CLI 调度（`uv` 管理环境） |
| 数据持久化 | SQLite（WAL 模式） | 存储 `intel_items`、`upcoming_events`、`daily_terms`、`notification_log` |
| LLM 降噪加工 | OpenAI Python SDK（兼容任意 OpenAI 接口） | 支持 OpenAI、DeepSeek、OpenRouter、本地 Ollama 等 |
| 前端阅读看板 | React 19、TypeScript 5.7、Vite 6、Tailwind CSS v4、`lucide-react` | 暗色 Bento 布局、键盘优先导航、防剧透遮罩、术语本弹窗 |
| 本地服务与通知 | Python `http.server` + WinRT PowerShell Toast | 零控制台黑框托管 `web/dist/`、提供本地 API 与 Windows 静音通知 |

---

## 快速开始

### 1. 环境准备与克隆仓库

- **Python**：`>= 3.11`（项目 `.python-version` 为 `3.12`，推荐使用 [uv](https://docs.astral.sh/uv/)）
- **Node.js**：`>= 18`（附带 `npm`，用于前端依赖安装与静态构建）

```bash
git clone https://github.com/WH20080518/AntiAlgo.git
cd AntiAlgo
```

### 2. 配置 `.env` 与 `config/sources.yaml`

#### 配置环境变量（`.env`）

```powershell
Copy-Item .env.example .env
```

按需填写以下环境变量（`.env` 已被 `.gitignore` 忽略，切勿提交到仓库）：

| 变量名 | 是否必填 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | 必填（使用 LLM 时） | OpenAI 兼容接口地址，默认 `https://api.openai.com/v1` |
| `LLM_API_KEY` | 必填（使用 LLM 时） | 对应服务商的 API Key |
| `LLM_MODEL` | 必填（使用 LLM 时） | 模型名称，如 `gpt-4o-mini`、`deepseek-chat` 等 |
| `PANDASCORE_API_KEY` | 可选 | [PandaScore](https://www.pandascore.co/) 免费 Token，用于抓取 CS2 S/A 级赛程；未配置则自动跳过 |

#### 配置订阅源与偏好（`config/sources.yaml`）

仓库内置脱敏模板 [config/sources.example.yaml](config/sources.example.yaml)（若本地未创建 `config/sources.yaml`，管道会自动回退读取该模板）。建议复制一份本地配置以自定义关注实体、RSS/YouTube 订阅源、黑名单及免打扰时段：

```powershell
Copy-Item config/sources.example.yaml config/sources.yaml
```

如需添加 YouTube 官方频道订阅，可使用内置解析脚本获取真实 `UC...` 频道 ID：

```bash
uv run python scripts/resolve_yt_channels.py @Handle1 @Handle2
```

### 3. 安装依赖并构建前端

```bash
# 安装 Python 后端依赖
uv sync

# 安装前端依赖并构建静态产物（输出至 web/dist/）
npm --prefix web install
npm --prefix web run build
```

### 4. 运行命令

**离线 Mock 演示（无需网络与 API Key）：**

```bash
uv run python -m pipeline.run --mock
```

**运行完整采集与 LLM 清洗管道：**

```bash
uv run python -m pipeline.run
```

`pipeline.run` 支持的常用参数：

| 参数 | 说明 |
| --- | --- |
| `--mock` | 生成离线示例数据到 `data/intel_mock.db` 与 `web/public/digest.json` |
| `--skip-llm` | 仅采集并执行硬规则过滤，跳过 LLM 阶段（无 API Key 时可用） |
| `--skip-fetch` | 跳过网络抓取，直接处理 SQLite 中已有条目 |
| `--skip-upcoming` | 跳过 PandaScore 与 MusicBrainz 日程采集 |
| `--limit <N>` | 限制本次送审 LLM 的最大未处理条目数 |
| `--batch-size <N>` | 覆盖默认的 LLM 批处理大小 |
| `--config <PATH>` | 指定配置文件路径（默认 `config/sources.yaml`） |
| `--output <PATH>` | 指定导出的 `digest.json` 路径（默认 `web/public/digest.json`） |

**启动本地一体服务（推荐）：**

```bash
uv run python -m pipeline.server
```

默认监听 `http://127.0.0.1:8765`（支持 `--host`、`--port`、`--dist`、`--digest`、`--config` 参数），提供静态页面托管、后台刷新（`/api/refresh`）、状态轮询（`/api/status`）、通知控制（`/api/notify/status`、`/api/notify/toggle`、`/api/notify/test`）与术语本掌握状态持久化（`/api/terms`、`/api/terms/mastered`）。

**前端开发热更新模式：**

```bash
npm --prefix web run dev
```

默认启动 Vite 开发服务器 `http://localhost:5173`。

**测试 Windows 静音桌面通知：**

```bash
uv run python -m pipeline.notifier --test
# 或立即执行一次通知评估：
uv run python -m pipeline.notifier --check
```

---

## 目录结构

```text
AntiAlgo/
├── config/
│   └── sources.example.yaml    # 数据源、关注实体、黑名单与通知配置模板（复制为 sources.yaml 使用）
├── data/                       # 本地运行时 SQLite 数据库、缓存与日志（已加入 .gitignore）
├── pipeline/
│   ├── __init__.py             # 配置加载、模板回退与路径常量
│   ├── db.py                   # SQLite 持久化层（intel_items / upcoming_events / daily_terms / notification_log）
│   ├── processor.py            # 硬规则过滤 + OpenAI 兼容 LLM 批量处理与术语生成
│   ├── build_digest.py         # 聚合并导出 digest.json 与 terms.json
│   ├── notifier.py             # Windows 原生静音 Toast 通知引擎
│   ├── run.py                  # 采集与清洗管道统一 CLI 入口
│   ├── server.py               # 本地 HTTP 静态托管与刷新/术语/通知 API 服务
│   └── collectors/
│       ├── rss.py              # 异步 RSS / Atom / YouTube Feed 采集器
│       └── upcoming.py         # PandaScore CS2 与 MusicBrainz 发行日历采集器
├── scripts/
│   ├── resolve_yt_channels.py  # 从 YouTube @handle 解析真实 UC... 频道 ID
│   ├── launch_hub.pyw          # 无控制台黑框拉起本地服务并打开浏览器
│   ├── install_local.ps1       # 创建桌面快捷方式与 Windows 用户级计划任务
│   ├── restart_server.ps1      # 重建前端、重启服务并验证核心接口健康状态
│   └── uninstall_local.ps1     # 卸载计划任务、快捷方式并停止后台进程
├── web/                        # React 19 + TypeScript + Tailwind CSS v4 前端应用
│   ├── public/                 # 运行时生成的 digest.json / terms.json 输出目录（已忽略）
│   └── src/
├── .env.example                # 环境变量配置模板
├── LICENSE                     # MIT 开源协议
├── pyproject.toml              # Python 项目与依赖声明
└── uv.lock                     # Python 锁定依赖
```

---

## Windows 本地部署脚本说明

`scripts/` 目录提供了一组免管理员权限的脚本，可将项目部署为本地桌面应用（面向 Windows 10/11）：

| 脚本 | 用途与常用参数 |
| --- | --- |
| `.\scripts\install_local.ps1` | 在桌面创建「反算法情报站.lnk」（调用 `.venv\Scripts\pythonw.exe` 配合 `scripts/launch_hub.pyw` 无黑框启动）；注册当前用户级计划任务 `AntiAlgoHub_Server`（登录时静默起 `127.0.0.1:8765` 服务）与 `AntiAlgoHub_DailyRefresh`（每日 `08:30` 与 `20:30` 自动执行 `pipeline.run`，错过时间开机自动补跑）。 |
| `.\scripts\restart_server.ps1` | 停止旧版服务、等待端口释放并重新拉起服务，最后自动探测 `/api/status`、`/digest.json`、`/api/notify/status`、`/api/terms` 健康状态。支持参数：`-RebuildWeb`（重建前端）、`-OpenBrowser`（启动后打开浏览器）、`-Port`（默认 `8765`）、`-TaskName`（默认 `AntiAlgoHub_Server`）、`-TimeoutSeconds`（默认 `15`）。 |
| `.\scripts\uninstall_local.ps1` | 注销 `AntiAlgoHub_Server` 与 `AntiAlgoHub_DailyRefresh` 计划任务、删除桌面快捷方式并终止关联的 `pipeline.server` 后台进程。 |
| `scripts\launch_hub.pyw` | 无窗口启动器：若 `127.0.0.1:8765` 尚未就绪则静默拉起 `pipeline.server` 并等待就绪，随后自动在默认浏览器打开看板。 |
| `scripts\resolve_yt_channels.py` | 接收一个或多个 YouTube `@Handle`（或频道 URL），抓取页面提取真实 `UC...` 频道 ID 并输出对应的 Atom RSS 订阅地址。 |

---

## 快捷键速查表

网页看板支持键盘流式操作（对应页面内 `?` 快捷键弹窗）：

| 按键 | 功能说明 |
| --- | --- |
| `j` / `↓` | 聚焦下一张情报卡片 |
| `k` / `↑` | 聚焦上一张情报卡片 |
| `m` | 切换当前卡片已读状态并折叠 |
| `o` / `回车 (Enter)` | 在新标签页中打开当前卡片原文 |
| `s` | 临时解锁或重新遮蔽当前卡片剧透 |
| `g` | 打开或关闭历史术语本弹窗 |
| `1-4` | 快速切换上方领域分类标签 |
| `?` | 打开或关闭此快捷键说明弹窗 |
| `退出 (Esc)` | 关闭弹窗 |

---

## License

本项目基于 [MIT License](LICENSE) 开源。
