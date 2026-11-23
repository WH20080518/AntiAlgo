# Anti-Algorithm Personal Hub（反算法个人情报站）

> 解耦的高信噪比个人情报采集管道与极简静态阅读看板，聚焦 **AI 与开源（Tech）**、**CS2 赛事（Esports）**、**欧美与 K-pop 音乐（Music）** 三大领域，拒绝算法推荐与信息流焦虑。

---

## 核心特性

- **多源异步采集**：
  - 支持标准 RSS / Atom 订阅，同域名请求自动串行并间隔限流（默认 2 秒），避免触发限流。
  - 支持 YouTube 官方频道原生 Atom Feed，内置 `exclude_shorts`（过滤 Shorts）与 `include_keywords`（关键词白名单）初筛。
  - 支持 PandaScore CS2 赛程日历（可选配置）与 MusicBrainz 关注艺人发行日历（带本地 MBID 缓存与礼貌限速）。
- **两级降噪与结构化加工**：
  - **硬规则初筛**：基于 `config/sources.yaml` 中的中/英文黑名单关键词拦截营销号、标题党、八卦与菠菜噪音，零 Token 消耗。
  - **LLM 批量精加工**：接入任意 OpenAI 兼容模型，完成去标题党、大白话简体中文改写、0–5 级重要度评分、跨源同题聚类（`cluster_id`）、赛果防剧透剥离（`spoiler_payload`）、未来明确日程提取，以及每日 3 条速览 + 1 个「每日术语」（30 天防重复并持久化至个人术语本）。
- **纯本地服务与桌面级通知**：
  - 基于 Python 标准库 `ThreadingHTTPServer` 的零依赖本地服务（默认 `http://127.0.0.1:8765`），同时托管前端静态构建产物与本地 API（手动触发刷新、查询状态、开关通知、标记术语已掌握）。
  - 内置 Windows 10/11 原生静音 Toast 桌面通知引擎：支持 CS2 赛前 15 分钟无剧透提醒（可穿透免打扰时段）与高优先音乐发行合并提醒（防刷屏 + 夜间免打扰 + 本地去重）。
- **键盘优先阅读体验**：
  - 前端基于 React 19 + Vite + Tailwind CSS v4 构建暗色 Bento 看板，支持防剧透毛玻璃遮罩、快捷键流式阅读与历史术语本管理。

---

## 目录结构

```text
AntiAlgo/
├── config/
│   └── sources.example.yaml    # 数据源、关注实体、黑名单与通知配置模板（复制为 sources.yaml 使用）
├── data/                       # 本地运行时 SQLite 数据库、缓存与日志（已加入 .gitignore）
├── pipeline/
│   ├── __init__.py             # 配置加载与路径常量
│   ├── db.py                   # SQLite 持久化层（intel_items / upcoming_events / daily_terms / notification_log）
│   ├── processor.py            # 硬规则过滤 + OpenAI 兼容 LLM 批量处理与术语生成
│   ├── build_digest.py         # 聚合并导出前端所需的 digest.json 与 terms.json
│   ├── notifier.py             # Windows 原生静音 Toast 通知引擎
│   ├── run.py                  # 情报采集与清洗管道统一 CLI 入口
│   ├── server.py               # 本地 HTTP 静态托管与刷新/术语/通知 API 服务
│   └── collectors/
│       ├── rss.py              # 异步 RSS / Atom / YouTube Feed 采集器
│       └── upcoming.py         # PandaScore CS2 与 MusicBrainz 发行日历采集器
├── scripts/
│   ├── resolve_yt_channels.py  # 从 YouTube @handle 解析真实 UC... 频道 ID
│   ├── launch_hub.pyw          # 无控制台黑框启动并打开浏览器
│   ├── install_local.ps1       # 创建桌面快捷方式与 Windows 用户级计划任务
│   ├── restart_server.ps1      # 重建前端、重启服务并验证健康检查
│   └── uninstall_local.ps1     # 卸载计划任务、快捷方式并停止后台进程
├── web/                        # React 19 + TypeScript + Tailwind CSS v4 前端应用
│   ├── public/                 # 运行时生成的 digest.json / terms.json 输出目录（已忽略）
│   └── src/
├── .env.example                # 环境变量配置模板
├── pyproject.toml              # Python 项目与依赖声明
└── uv.lock                     # Python 锁定依赖
```

---

## 环境要求

- **Python**：`>= 3.11`（项目内置 `.python-version` 为 `3.12`，推荐使用 [uv](https://docs.astral.sh/uv/) 管理环境）
- **Node.js**：`>= 18`（附带 `npm`，用于前端依赖安装与静态构建）
- **操作系统**：数据管道与本地服务跨平台可用；桌面静音 Toast 通知与 `scripts/*.ps1` 部署脚本面向 **Windows 10/11**。

---

## 安装与配置

### 1. 克隆仓库并安装依赖

```bash
git clone https://github.com/WH20080518/AntiAlgo.git
cd AntiAlgo

# 1) 安装 Python 后端依赖
uv sync

# 2) 安装前端依赖并构建静态产物（输出至 web/dist/）
npm --prefix web install
npm --prefix web run build
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，并填入你的配置：

```powershell
Copy-Item .env.example .env
```

`.env` 支持以下变量：

| 变量名 | 是否必填 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | 必填（使用 LLM 时） | OpenAI 兼容接口地址，默认 `https://api.openai.com/v1`（可替换为 DeepSeek、OpenRouter、本地 Ollama 等） |
| `LLM_API_KEY` | 必填（使用 LLM 时） | 对应服务商的 API Key |
| `LLM_MODEL` | 必填（使用 LLM 时） | 模型名称，如 `gpt-4o-mini`、`deepseek-chat` 等 |
| `PANDASCORE_API_KEY` | 可选 | [PandaScore](https://www.pandascore.co/) 免费 Token，用于抓取 CS2 S/A 级赛程；留空则自动跳过 |

> **提示**：`.env` 已被 `.gitignore` 忽略，请勿提交到版本库。如暂不配置 API Key，可使用 `--mock` 或 `--skip-llm` 模式体验。

### 3. 自定义订阅源与偏好（可选）

仓库默认提供脱敏模板 [config/sources.example.yaml](config/sources.example.yaml)（未创建 `config/sources.yaml` 时会自动回退读取该模板）。如需个性化订阅与偏好，请复制为本地配置文件后修改（`config/sources.yaml` 已被 `.gitignore` 忽略，不会随代码提交）：

```powershell
Copy-Item config/sources.example.yaml config/sources.yaml
```

编辑 `config/sources.yaml` 可自定义：

- `settings`：滚动时间窗口（默认 `72` 小时）、最大导出条数（默认 `15`）、最低展示重要度（默认 `3`）。
- `sources`：RSS / Atom / YouTube 频道订阅列表（支持 `exclude_shorts`、`include_keywords`）。
- `focus_entities` & `domain_preferences`：重点关注实体白名单与各领域准入/排除偏好。
- `blacklist_keywords`：中英文硬过滤黑名单关键词。
- `notifications`：桌面提醒开关、轮询间隔、CS2 赛前提前分钟数、夜间免打扰时段（`quiet_hours`）与静音模式。

若需新增 YouTube 官方频道订阅，可先用辅助脚本解析其 `UC...` 频道 ID：

```bash
uv run python scripts/resolve_yt_channels.py @Handle1 @Handle2
```

---

## 快速开始与常用命令

### 1. 离线 Mock 演示（无需网络与 API Key）

```bash
uv run python -m pipeline.run --mock
```

将在 `data/intel_mock.db` 生成示例数据，并导出 `web/public/digest.json` 与 `web/public/terms.json`。

### 2. 运行完整采集与降噪管道

```bash
uv run python -m pipeline.run
```

常用 CLI 参数：

- `--skip-llm`：仅抓取并执行硬规则过滤，跳过 LLM 调用（适合无 Key 测试）。
- `--skip-fetch`：跳过网络抓取，直接对 SQLite 中已有数据进行加工与导出。
- `--skip-upcoming`：跳过 PandaScore 与 MusicBrainz 日程采集。
- `--limit <N>` / `--batch-size <N>`：限制单次送审条数或覆盖 LLM 批大小。
- `--config <PATH>` / `--output <PATH>`：指定自定义配置文件或输出路径。

### 3. 启动本地阅读看板

**方式 A：本地一体服务（推荐，支持后台刷新、通知开关与术语本持久化）**

```bash
# 确保已构建前端：npm --prefix web run build
uv run python -m pipeline.server
```

启动后在浏览器访问 `http://127.0.0.1:8765`。

**方式 B：前端开发热更新模式**

```bash
npm --prefix web run dev
```

访问 Vite 开发服务器（默认 `http://localhost:5173`，静态读取 `web/public/digest.json`）。

### 4. 测试桌面静音通知（Windows）

```bash
uv run python -m pipeline.notifier --test
```

---

## Windows 本地桌面化部署（可选）

项目提供了一组无管理员权限的 PowerShell 脚本，适合日常作为桌面本地应用使用：

- **安装桌面快捷方式与定时任务**：
  ```powershell
  .\scripts\install_local.ps1
  ```
  - 在桌面创建「反算法情报站.lnk」（通过 `pythonw.exe` 无黑框启动并自动打开浏览器）。
  - 注册当前用户级计划任务 `AntiAlgoHub_Server`（登录时静默拉起 `127.0.0.1:8765`）与 `AntiAlgoHub_DailyRefresh`（每天 `08:30` 与 `20:30` 静默更新情报，开机后自动补跑错过任务）。
- **代码更新后重建并重启服务**：
  ```powershell
  .\scripts\restart_server.ps1 -RebuildWeb -OpenBrowser
  ```
- **彻底卸载快捷方式与计划任务**：
  ```powershell
  .\scripts\uninstall_local.ps1
  ```

---

## 键盘快捷键

看板支持键盘流式阅读（在网页中按 `?` 可随时查看）：

| 按键 | 功能 |
| --- | --- |
| `j` / `↓` | 聚焦下一张情报卡片 |
| `k` / `↑` | 聚焦上一张情报卡片 |
| `m` | 切换当前卡片已读状态并折叠 |
| `o` / `Enter` | 在新标签页打开当前卡片原文 |
| `s` | 临时解锁或重新遮蔽当前卡片剧透 |
| `g` | 打开或关闭历史术语本弹窗 |
| `1` - `4` | 快速切换上方领域分类标签 |
| `?` | 打开或关闭快捷键说明弹窗 |
| `Esc` | 关闭当前弹窗 |
