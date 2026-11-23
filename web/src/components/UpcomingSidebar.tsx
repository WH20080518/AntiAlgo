import { ArrowUpRight, CalendarClock, Keyboard } from "lucide-react";
import { useEffect, useState } from "react";
import type { DailyTerm, GlossaryStats, UpcomingItem } from "../types";
import {
  formatUpcomingDisplay,
  getDomainMeta,
  getLocalTimeZone,
  getUpcomingSourceLabel,
} from "../utils/format";
import { DailyTermCard } from "./DailyTermCard";

interface UpcomingSidebarProps {
  dailyTerm?: DailyTerm | null;
  glossaryStats?: GlossaryStats | null;
  upcoming: UpcomingItem[];
  totalCount: number;
  readCount: number;
  onOpenGlossary: () => void;
  onOpenShortcuts: () => void;
}

export function UpcomingSidebar({
  dailyTerm,
  glossaryStats,
  upcoming,
  totalCount,
  readCount,
  onOpenGlossary,
  onOpenShortcuts,
}: UpcomingSidebarProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const timeZone = getLocalTimeZone();

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const progressPct =
    totalCount > 0 ? Math.round((readCount / totalCount) * 100) : 0;

  return (
    <aside className="space-y-4">
      <DailyTermCard
        term={dailyTerm}
        glossaryStats={glossaryStats}
        onOpenGlossary={onOpenGlossary}
      />

      <section className="bento-surface rounded-2xl p-4 sm:p-5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-indigo-300" />
            <h2 className="text-xs font-semibold tracking-wide text-[#D1D5DB]">
              近期关注日程
            </h2>
          </div>
          <span
            title={`已自动换算至本地时区：${timeZone}`}
            className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 text-[10px] text-[#6B7280]"
          >
            本地时区
          </span>
        </div>

        <div className="mt-3.5 space-y-2.5">
          {upcoming.length > 0 ? (
            upcoming.map((event) => {
              const domainMeta = getDomainMeta(event.domain);
              const display = formatUpcomingDisplay(event, nowMs);
              const sourceLabel = getUpcomingSourceLabel(event.source);
              return (
                <div
                  key={`${event.domain}-${event.title}-${event.start_time}`}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${domainMeta.dotClass}`}
                      />
                      <span className="text-[11px] text-[#8B93A1]">
                        {domainMeta.label}
                      </span>
                      {event.tag && (
                        <span className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-[#9CA3AF]">
                          {event.tag}
                        </span>
                      )}
                    </div>
                    <span
                      className={`inline-flex shrink-0 items-center gap-1.5 text-[11px] font-medium tabular-nums ${
                        display.isLive || display.isToday
                          ? "text-emerald-300"
                          : "text-indigo-300"
                      }`}
                    >
                      {display.isLive && (
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                      )}
                      {display.badge}
                    </span>
                  </div>

                  <div className="mt-1.5 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      {event.url ? (
                        <a
                          href={event.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-[13px] font-medium leading-snug text-[#E6E8EC] transition-colors hover:text-indigo-300"
                        >
                          <span>{event.title}</span>
                          <ArrowUpRight className="h-3 w-3 shrink-0 opacity-70" />
                        </a>
                      ) : (
                        <p className="text-[13px] font-medium leading-snug text-[#E6E8EC]">
                          {event.title}
                        </p>
                      )}
                      {event.subtitle && (
                        <p className="mt-0.5 text-[11px] text-[#8B93A1]">
                          {event.subtitle}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px] text-[#6B7280]">
                    <span className="tabular-nums">
                      {display.caption || ""}
                    </span>
                    {sourceLabel && (
                      <span className="rounded bg-white/[0.03] px-1.5 py-0.5 text-[10px] text-[#6B7280]">
                        {sourceLabel}
                      </span>
                    )}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="rounded-xl border border-dashed border-white/[0.08] px-3.5 py-5 text-center text-xs text-[#6B7280]">
              近期暂无关注日程
            </div>
          )}
        </div>
      </section>

      <section className="bento-surface rounded-2xl p-4 sm:p-5">
        <div className="flex items-center justify-between text-xs">
          <span className="font-medium text-[#9CA3AF]">今日阅读进度</span>
          <span className="font-mono text-[#D1D5DB] tabular-nums">
            {readCount} / {totalCount}
          </span>
        </div>
        <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full rounded-full bg-indigo-400/75 transition-all duration-300"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <p className="mt-2 text-[11px] text-[#6B7280] tabular-nums">
          {totalCount > 0 && readCount >= totalCount
            ? "今日全部高信噪比条目已阅毕"
            : `剩余 ${Math.max(0, totalCount - readCount)} 条待阅 · 完成度 ${progressPct}%`}
        </p>
      </section>

      <section className="bento-surface rounded-2xl p-4 sm:p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Keyboard className="h-4 w-4 text-[#8B93A1]" />
            <h2 className="text-xs font-semibold tracking-wide text-[#D1D5DB]">
              键盘优先导航
            </h2>
          </div>
          <button
            type="button"
            onClick={onOpenShortcuts}
            className="text-[11px] text-indigo-300/85 hover:text-indigo-200 cursor-pointer"
          >
            全部说明
          </button>
        </div>

        <dl className="mt-3 space-y-2 text-xs">
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>下移 / 上移焦点</dt>
            <dd className="flex items-center gap-1">
              <kbd className="kbd-cap">j</kbd>
              <kbd className="kbd-cap">k</kbd>
            </dd>
          </div>
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>标记已读 / 折叠</dt>
            <dd>
              <kbd className="kbd-cap">m</kbd>
            </dd>
          </div>
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>新标签打开原文</dt>
            <dd>
              <kbd className="kbd-cap">o</kbd>
            </dd>
          </div>
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>单卡解锁 / 遮蔽剧透</dt>
            <dd>
              <kbd className="kbd-cap">s</kbd>
            </dd>
          </div>
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>历史术语本</dt>
            <dd>
              <kbd className="kbd-cap">g</kbd>
            </dd>
          </div>
          <div className="flex items-center justify-between text-[#9CA3AF]">
            <dt>显示快捷键弹窗</dt>
            <dd>
              <kbd className="kbd-cap">?</kbd>
            </dd>
          </div>
        </dl>
      </section>
    </aside>
  );
}
