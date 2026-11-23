import {
  ArrowUpRight,
  Check,
  Eye,
  Link2,
  RotateCcw,
  ShieldAlert,
  ShieldOff,
} from "lucide-react";
import type { IntelItem, SpoilerPayload } from "../types";
import {
  extractHostname,
  formatLocalDateTime,
  formatRelativeTime,
  getDomainMeta,
} from "../utils/format";

interface IntelCardProps {
  item: IntelItem;
  isFocused: boolean;
  isRead: boolean;
  antiSpoiler: boolean;
  isSpoilerUnlocked: boolean;
  onFocus: () => void;
  onToggleRead: (id: string) => void;
  onToggleSpoiler: (id: string) => void;
  cardRef: (el: HTMLElement | null) => void;
}

function normalizeSpoiler(
  raw: IntelItem["spoiler_payload"]
): SpoilerPayload | null {
  if (!raw) return null;
  if (typeof raw === "string") {
    return { label: "剧透详情", summary: raw };
  }
  if (typeof raw === "object") {
    return raw;
  }
  return null;
}

export function IntelCard({
  item,
  isFocused,
  isRead,
  antiSpoiler,
  isSpoilerUnlocked,
  onFocus,
  onToggleRead,
  onToggleSpoiler,
  cardRef,
}: IntelCardProps) {
  const domainMeta = getDomainMeta(item.domain);
  const spoiler = normalizeSpoiler(item.spoiler_payload);
  const isMasked = Boolean(spoiler && antiSpoiler && !isSpoilerUnlocked);

  if (isRead) {
    return (
      <article
        ref={cardRef}
        onClick={onFocus}
        className={`bento-surface-interactive relative flex items-center justify-between gap-3 rounded-xl px-4 py-2.5 opacity-55 hover:opacity-85 ${
          isFocused
            ? "!border-indigo-400/45 !bg-[#14171D] !opacity-90 ring-1 ring-indigo-400/25"
            : ""
        }`}
      >
        {isFocused && (
          <span
            aria-hidden="true"
            className="absolute inset-y-2 left-0 w-0.5 rounded-r bg-indigo-400"
          />
        )}
        <div className="flex min-w-0 items-center gap-2.5">
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-400/75" />
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="truncate text-xs text-[#9CA3AF] line-through decoration-white/20 hover:text-[#E6E8EC] hover:no-underline"
          >
            {item.title}
          </a>
          <span className="hidden shrink-0 text-[11px] text-[#6B7280] sm:inline">
            · {item.source_name}
          </span>
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggleRead(item.id);
          }}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-[#9CA3AF] transition-colors hover:bg-white/[0.08] hover:text-[#E6E8EC] cursor-pointer"
        >
          <RotateCcw className="h-3 w-3" />
          <span>恢复未读</span>
        </button>
      </article>
    );
  }

  const clampedImportance = Math.max(1, Math.min(5, item.importance || 3));

  return (
    <article
      ref={cardRef}
      onClick={onFocus}
      className={`bento-surface-interactive group relative rounded-xl p-4 sm:p-5 ${
        isFocused
          ? "!border-indigo-400/45 !bg-[#14171D] ring-1 ring-indigo-400/25"
          : ""
      }`}
    >
      {isFocused && (
        <span
          aria-hidden="true"
          className="absolute inset-y-3 left-0 w-0.5 rounded-r bg-indigo-400"
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium ${domainMeta.badgeClass}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${domainMeta.dotClass}`} />
            {domainMeta.label}
          </span>
          <span className="font-medium text-[#9CA3AF]">{item.source_name}</span>
          <span className="text-[#4B5563]">·</span>
          <time
            dateTime={item.published_at}
            title={formatLocalDateTime(item.published_at)}
            className="text-[#6B7280] tabular-nums"
          >
            {formatRelativeTime(item.published_at)}
          </time>
        </div>

        <div className="flex items-center gap-2">
          <div
            className="mr-1 hidden items-center gap-1 sm:flex"
            title={`信噪比权重 ${clampedImportance}/5`}
          >
            {[1, 2, 3, 4, 5].map((dot) => (
              <span
                key={dot}
                className={`h-1.5 w-1.5 rounded-full ${
                  dot <= clampedImportance
                    ? "bg-indigo-300/75"
                    : "bg-white/[0.08]"
                }`}
              />
            ))}
          </div>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleRead(item.id);
            }}
            title="标记已读 (m)"
            className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.025] px-2 py-1 text-[11px] font-medium text-[#9CA3AF] transition-colors hover:border-emerald-500/30 hover:bg-emerald-500/10 hover:text-emerald-300 cursor-pointer"
          >
            <Check className="h-3 w-3" />
            <span>已读</span>
          </button>

          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            title="打开原文 (o)"
            className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.025] px-2 py-1 text-[11px] font-medium text-[#9CA3AF] transition-colors hover:border-white/20 hover:bg-white/[0.07] hover:text-[#F3F4F6]"
          >
            <span>原文</span>
            <ArrowUpRight className="h-3 w-3" />
          </a>
        </div>
      </div>

      <h2 className="mt-2.5 text-[15px] font-medium leading-snug text-[#F3F4F6] sm:text-base">
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="transition-colors hover:text-indigo-300"
        >
          {item.title}
        </a>
      </h2>

      {item.summary && (
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-[#9CA3AF]">
          {item.summary}
        </p>
      )}

      {spoiler && (
        <div className="mt-3.5">
          {isMasked ? (
            <div className="relative overflow-hidden rounded-lg border border-white/10 bg-[#0C0E12] px-4 py-3">
              <div
                aria-hidden="true"
                className="select-none space-y-1.5 blur-[6px] opacity-35 pointer-events-none"
              >
                <div className="h-3 w-44 rounded bg-white/25" />
                <div className="h-3 w-72 rounded bg-white/15" />
              </div>
              <div className="absolute inset-0 flex items-center justify-between gap-3 bg-[#0D1016]/75 px-3.5 backdrop-blur-md">
                <div className="flex items-center gap-2 text-xs text-amber-200/90">
                  <ShieldAlert className="h-4 w-4 shrink-0 text-amber-300/85" />
                  <span>含比分 / 赛果或关键剧透信息，已隔离保护</span>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleSpoiler(item.id);
                  }}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-amber-400/30 bg-amber-500/15 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/25 cursor-pointer"
                >
                  <Eye className="h-3.5 w-3.5" />
                  <span>点击查看剧透</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.04] px-3.5 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-amber-300/90">
                    {spoiler.label || "赛果 / 剧透详情"}
                  </span>
                  {spoiler.match && (
                    <span className="text-[#D1D5DB]">{String(spoiler.match)}</span>
                  )}
                  {spoiler.score && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-amber-200 tabular-nums">
                      {String(spoiler.score)}
                    </span>
                  )}
                  {spoiler.winner && (
                    <span className="rounded border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-0.5 text-[11px] text-emerald-300">
                      胜者：{String(spoiler.winner)}
                    </span>
                  )}
                </div>
                {antiSpoiler && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleSpoiler(item.id);
                    }}
                    className="inline-flex items-center gap-1 text-[11px] text-[#8B93A1] transition-colors hover:text-[#E6E8EC] cursor-pointer"
                  >
                    <ShieldOff className="h-3 w-3" />
                    <span>重新遮蔽</span>
                  </button>
                )}
              </div>
              {spoiler.summary && (
                <p className="mt-1.5 text-xs leading-relaxed text-[#D1D5DB]">
                  {String(spoiler.summary)}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {item.related_links.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-white/[0.06] pt-2.5">
          <span className="mr-1 inline-flex items-center gap-1 text-[11px] text-[#6B7280]">
            <Link2 className="h-3 w-3" />
            <span>同题参考</span>
          </span>
          {item.related_links.map((link) => (
            <a
              key={link}
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 rounded-md border border-white/[0.07] bg-white/[0.02] px-2 py-0.5 text-[11px] text-[#9CA3AF] transition-colors hover:border-white/20 hover:bg-white/[0.06] hover:text-[#E6E8EC]"
            >
              <span>{extractHostname(link)}</span>
              <ArrowUpRight className="h-2.5 w-2.5 opacity-70" />
            </a>
          ))}
        </div>
      )}
    </article>
  );
}
