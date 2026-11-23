import {
  Bell,
  BellOff,
  Eye,
  EyeOff,
  HelpCircle,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Terminal,
} from "lucide-react";
import { formatLocalDateTime, formatRelativeTime } from "../utils/format";

export interface RefreshNotice {
  type: "success" | "error";
  text: string;
}

interface TopBriefingBarProps {
  generatedAt: string;
  briefings: string[];
  antiSpoiler: boolean;
  hideRead: boolean;
  canRefresh: boolean;
  isRefreshing: boolean;
  refreshNotice: RefreshNotice | null;
  notifyEnabled: boolean | null;
  notifyBusy: boolean;
  onRefresh: () => void;
  onToggleNotify: () => void;
  onToggleAntiSpoiler: () => void;
  onToggleHideRead: () => void;
  onOpenShortcuts: () => void;
}

export function TopBriefingBar({
  generatedAt,
  briefings,
  antiSpoiler,
  hideRead,
  canRefresh,
  isRefreshing,
  refreshNotice,
  notifyEnabled,
  notifyBusy,
  onRefresh,
  onToggleNotify,
  onToggleAntiSpoiler,
  onToggleHideRead,
  onOpenShortcuts,
}: TopBriefingBarProps) {
  const localTime = formatLocalDateTime(generatedAt);
  const relTime = formatRelativeTime(generatedAt);

  return (
    <header className="bento-surface rounded-2xl p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-indigo-300">
            <Terminal className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-sm font-semibold tracking-tight text-[#F3F4F6] sm:text-base">
                反算法个人情报站
              </h1>
              <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[11px] font-medium text-[#8B93A1]">
                每日 2 分钟
              </span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[#6B7280] tabular-nums">
              <span>
                最后更新：{localTime}
                {relTime ? `（${relTime}）` : ""}
              </span>
              {refreshNotice && (
                <span
                  title={refreshNotice.text}
                  className={`rounded border px-1.5 py-0.5 text-[10px] ${
                    refreshNotice.type === "success"
                      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
                      : "border-amber-500/25 bg-amber-500/10 text-amber-300"
                  }`}
                >
                  {refreshNotice.text}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canRefresh && (
            <button
              type="button"
              disabled={isRefreshing}
              onClick={onRefresh}
              title="抓取最新 RSS 并进行增量 LLM 降噪"
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                isRefreshing
                  ? "border-indigo-400/30 bg-indigo-500/10 text-indigo-200/80 cursor-wait"
                  : "border-white/10 bg-white/[0.03] text-[#D1D5DB] hover:border-indigo-400/35 hover:bg-indigo-500/10 hover:text-indigo-200 cursor-pointer"
              }`}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${isRefreshing ? "animate-spin" : ""}`}
              />
              <span>{isRefreshing ? "刷新中…" : "刷新情报"}</span>
            </button>
          )}

          {canRefresh && notifyEnabled !== null && (
            <button
              type="button"
              role="switch"
              aria-checked={notifyEnabled}
              disabled={notifyBusy}
              onClick={onToggleNotify}
              title={
                notifyEnabled
                  ? "桌面静音提醒已开启（点击暂停 CS2 赛前与新歌通知）"
                  : "桌面静音提醒已暂停（点击恢复通知）"
              }
              aria-label={notifyEnabled ? "暂停桌面提醒" : "开启桌面提醒"}
              className={`inline-flex items-center justify-center rounded-lg border p-1.5 transition-colors cursor-pointer ${
                notifyEnabled
                  ? "border-indigo-400/35 bg-indigo-500/10 text-indigo-200 hover:bg-indigo-500/20"
                  : "border-white/10 bg-white/[0.03] text-[#6B7280] hover:bg-white/[0.06] hover:text-[#D1D5DB]"
              } ${notifyBusy ? "opacity-60 cursor-wait" : ""}`}
            >
              {notifyEnabled ? (
                <Bell className="h-3.5 w-3.5" />
              ) : (
                <BellOff className="h-3.5 w-3.5" />
              )}
            </button>
          )}

          <button
            type="button"
            role="switch"
            aria-checked={antiSpoiler}
            onClick={onToggleAntiSpoiler}
            className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
              antiSpoiler
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/15"
                : "border-white/10 bg-white/[0.03] text-[#9CA3AF] hover:bg-white/[0.06] hover:text-[#E6E8EC]"
            }`}
          >
            {antiSpoiler ? (
              <ShieldCheck className="h-3.5 w-3.5" />
            ) : (
              <ShieldOff className="h-3.5 w-3.5" />
            )}
            <span>防剧透模式</span>
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] ${
                antiSpoiler
                  ? "bg-emerald-500/20 text-emerald-200"
                  : "bg-white/[0.06] text-[#8B93A1]"
              }`}
            >
              {antiSpoiler ? "开启" : "关闭"}
            </span>
          </button>

          <button
            type="button"
            role="switch"
            aria-checked={hideRead}
            onClick={onToggleHideRead}
            className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
              hideRead
                ? "border-indigo-400/35 bg-indigo-500/15 text-indigo-200 hover:bg-indigo-500/20"
                : "border-white/10 bg-white/[0.03] text-[#9CA3AF] hover:bg-white/[0.06] hover:text-[#E6E8EC]"
            }`}
          >
            {hideRead ? (
              <EyeOff className="h-3.5 w-3.5" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
            <span>隐藏已读</span>
          </button>

          <button
            type="button"
            onClick={onOpenShortcuts}
            title="键盘快捷键 (?)"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-[#9CA3AF] transition-colors hover:bg-white/[0.06] hover:text-[#E6E8EC] cursor-pointer"
          >
            <HelpCircle className="h-3.5 w-3.5" />
            <kbd className="kbd-cap">?</kbd>
          </button>
        </div>
      </div>

      <div className="mt-4 border-t border-white/[0.06] pt-4">
        <div className="mb-2.5 flex items-center justify-between">
          <span className="text-[11px] font-medium tracking-wide text-[#6B7280]">
            今日核心速览
          </span>
        </div>
        {briefings.length > 0 ? (
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
            {briefings.slice(0, 3).map((line, index) => (
              <div
                key={`${index}-${line}`}
                className="flex items-start gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5"
              >
                <span className="mt-0.5 font-mono text-[11px] font-medium text-indigo-300/75 tabular-nums">
                  0{index + 1}
                </span>
                <p className="text-[13px] leading-relaxed text-[#D1D5DB]">
                  {line}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#6B7280]">
            今日暂无高优先核心简报，请浏览下方分域卡片。
          </p>
        )}
      </div>
    </header>
  );
}
