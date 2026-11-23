import {
  BookMarked,
  Check,
  Copy,
  Eye,
  EyeOff,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GlossaryStats, GlossaryTerm } from "../types";
import { getDomainMeta } from "../utils/format";

interface GlossaryModalProps {
  open: boolean;
  loading: boolean;
  terms: GlossaryTerm[];
  stats: GlossaryStats;
  updatingKey: string | null;
  onClose: () => void;
  onToggleMastered: (termKey: string, nextMastered: boolean) => void;
}

const DOMAIN_FILTERS: Array<{ key: string; label: string }> = [
  { key: "all", label: "全部" },
  { key: "tech", label: "AI 与开源" },
  { key: "esports", label: "CS2 赛事" },
  { key: "music", label: "欧美与 K-pop" },
];

async function writeTextToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fallback below when Clipboard API is restricted
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

export function GlossaryModal({
  open,
  loading,
  terms,
  stats,
  updatingKey,
  onClose,
  onToggleMastered,
}: GlossaryModalProps) {
  const [query, setQuery] = useState("");
  const [domainFilter, setDomainFilter] = useState("all");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [quizMode, setQuizMode] = useState(false);
  const [revealedKeys, setRevealedKeys] = useState<Set<string>>(
    () => new Set()
  );
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  const filteredTerms = useMemo(() => {
    const q = query.trim().toLowerCase();
    return terms.filter((term) => {
      if (domainFilter !== "all" && term.domain !== domainFilter) {
        return false;
      }
      if (reviewOnly && term.is_mastered) {
        return false;
      }
      if (!q) return true;
      const haystack = [
        term.term_en,
        term.phonetic_or_abbr ?? "",
        term.term_zh,
        term.plain_explain,
      ]
        .join("\n")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [terms, domainFilter, reviewOnly, query]);

  if (!open) return null;

  const toggleRevealCard = (termKey: string) => {
    setRevealedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(termKey)) {
        next.delete(termKey);
      } else {
        next.add(termKey);
      }
      return next;
    });
  };

  const handleCopyMarkdown = async () => {
    if (filteredTerms.length === 0) return;
    const lines = [
      "## 反算法个人情报站 · 历史术语本",
      "",
      ...filteredTerms.map((term) => {
        const meta = getDomainMeta(term.domain);
        const abbrPart = term.phonetic_or_abbr
          ? ` (${term.phonetic_or_abbr})`
          : "";
        const statusPart = term.is_mastered ? " [已掌握]" : "";
        const items = [
          `- **${term.term_en}**${abbrPart} — ${term.term_zh}（${meta.label} · ${term.last_seen_date}）${statusPart}`,
          `  - 大白话：${term.plain_explain}`,
        ];
        if (term.in_context) {
          items.push(`  - 语境：${term.in_context}`);
        }
        return items.join("\n");
      }),
    ];
    const ok = await writeTextToClipboard(lines.join("\n"));
    if (copyTimerRef.current !== null) {
      window.clearTimeout(copyTimerRef.current);
    }
    setCopyNotice(ok ? `已复制 ${filteredTerms.length} 条` : "复制失败");
    copyTimerRef.current = window.setTimeout(() => {
      setCopyNotice(null);
    }, 2500);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="历史术语本"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bento-surface flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl shadow-2xl"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-5 py-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <BookMarked className="h-4 w-4 text-indigo-300" />
            <h2 className="text-sm font-semibold text-[#F3F4F6]">历史术语本</h2>
            <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono text-[11px] text-[#9CA3AF] tabular-nums">
              已掌握 {stats.mastered} / {stats.total}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {copyNotice && (
              <span className="rounded border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                {copyNotice}
              </span>
            )}
            <button
              type="button"
              disabled={filteredTerms.length === 0}
              onClick={() => void handleCopyMarkdown()}
              title="将当前筛选术语导出为 Markdown 到剪贴板"
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs font-medium text-[#D1D5DB] transition-colors hover:bg-white/[0.07] hover:text-[#F3F4F6] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
            >
              <Copy className="h-3.5 w-3.5" />
              <span>复制 Markdown</span>
            </button>

            <button
              type="button"
              onClick={onClose}
              aria-label="关闭历史术语本"
              className="rounded-lg border border-white/10 bg-white/[0.03] p-1.5 text-[#9CA3AF] transition-colors hover:bg-white/[0.08] hover:text-[#F3F4F6] cursor-pointer"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="space-y-3 border-b border-white/[0.06] bg-white/[0.015] px-5 py-3.5">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-[#6B7280]" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索英文术语、缩写、中文译名或大白话解释…"
              className="w-full rounded-lg border border-white/10 bg-[#0A0B0D] py-2 pr-8 pl-9 text-xs text-[#E6E8EC] placeholder-[#6B7280] outline-none transition-colors focus:border-indigo-400/50"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="清空搜索"
                className="absolute top-1/2 right-2.5 -translate-y-1/2 text-[#6B7280] hover:text-[#D1D5DB] cursor-pointer"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {DOMAIN_FILTERS.map((item) => {
                const active = domainFilter === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setDomainFilter(item.key)}
                    className={`rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer ${
                      active
                        ? "border-indigo-400/40 bg-indigo-500/15 text-indigo-200"
                        : "border-white/10 bg-white/[0.025] text-[#8B93A1] hover:bg-white/[0.06] hover:text-[#D1D5DB]"
                    }`}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                role="switch"
                aria-checked={reviewOnly}
                onClick={() => setReviewOnly((prev) => !prev)}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer ${
                  reviewOnly
                    ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                    : "border-white/10 bg-white/[0.025] text-[#9CA3AF] hover:bg-white/[0.06] hover:text-[#E6E8EC]"
                }`}
              >
                <span>仅看待复习</span>
              </button>

              <button
                type="button"
                role="switch"
                aria-checked={quizMode}
                onClick={() => {
                  setQuizMode((prev) => !prev);
                  setRevealedKeys(new Set());
                }}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer ${
                  quizMode
                    ? "border-indigo-400/40 bg-indigo-500/15 text-indigo-200"
                    : "border-white/10 bg-white/[0.025] text-[#9CA3AF] hover:bg-white/[0.06] hover:text-[#E6E8EC]"
                }`}
              >
                {quizMode ? (
                  <EyeOff className="h-3 w-3" />
                ) : (
                  <Eye className="h-3 w-3" />
                )}
                <span>自测模式</span>
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 space-y-2.5 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="space-y-2.5">
              <div className="h-24 animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02]" />
              <div className="h-24 animate-pulse rounded-xl border border-white/[0.06] bg-white/[0.02]" />
            </div>
          ) : filteredTerms.length === 0 ? (
            <div className="rounded-xl border border-dashed border-white/[0.08] px-4 py-10 text-center text-xs text-[#6B7280]">
              {terms.length === 0
                ? "暂未收录历史术语，运行情报管道后将自动积累。"
                : "没有符合当前筛选条件的历史术语。"}
            </div>
          ) : (
            filteredTerms.map((term) => {
              const domainMeta = getDomainMeta(term.domain);
              const masked = quizMode && !revealedKeys.has(term.term_key);
              const isBusy = updatingKey === term.term_key;

              return (
                <article
                  key={term.term_key}
                  onClick={() => {
                    if (quizMode) toggleRevealCard(term.term_key);
                  }}
                  className={`rounded-xl border p-4 transition-colors ${
                    term.is_mastered
                      ? "border-white/[0.06] bg-white/[0.015] opacity-80"
                      : "border-white/10 bg-white/[0.025]"
                  } ${quizMode ? "cursor-pointer hover:border-indigo-400/35" : ""}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-semibold tracking-tight text-[#F3F4F6]">
                        {term.term_en}
                      </h3>
                      {term.phonetic_or_abbr && (
                        <span className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-[#9CA3AF]">
                          {term.phonetic_or_abbr}
                        </span>
                      )}
                      <span
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-medium ${domainMeta.badgeClass}`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${domainMeta.dotClass}`}
                        />
                        {domainMeta.label}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[11px] text-[#6B7280] tabular-nums">
                        {term.last_seen_date}
                        {term.seen_count > 1 ? ` · ×${term.seen_count}` : ""}
                      </span>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleMastered(term.term_key, !term.is_mastered);
                        }}
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors cursor-pointer ${
                          term.is_mastered
                            ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/20"
                            : "border-white/10 bg-white/[0.03] text-[#9CA3AF] hover:border-emerald-500/30 hover:bg-emerald-500/10 hover:text-emerald-300"
                        } ${isBusy ? "opacity-50 cursor-wait" : ""}`}
                      >
                        <Check className="h-3 w-3" />
                        <span>{term.is_mastered ? "已掌握" : "标记掌握"}</span>
                      </button>
                    </div>
                  </div>

                  {masked ? (
                    <div className="relative mt-3 overflow-hidden rounded-lg border border-white/10 bg-[#0C0E12] px-4 py-3">
                      <div
                        aria-hidden="true"
                        className="pointer-events-none select-none space-y-1.5 opacity-35 blur-[6px]"
                      >
                        <div className="h-3 w-28 rounded bg-indigo-300/35" />
                        <div className="h-3 w-full rounded bg-white/20" />
                        <div className="h-3 w-3/4 rounded bg-white/15" />
                      </div>
                      <div className="absolute inset-0 flex items-center justify-between gap-3 bg-[#0D1016]/75 px-3.5 backdrop-blur-md">
                        <span className="text-xs text-indigo-200/90">
                          自测遮罩中 · 试着用大白话复述该术语含义
                        </span>
                        <span className="inline-flex shrink-0 items-center gap-1 rounded border border-indigo-400/30 bg-indigo-500/15 px-2.5 py-1 text-[11px] font-medium text-indigo-200">
                          <Eye className="h-3 w-3" />
                          点击掀开核对答案
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="mt-2.5 space-y-1.5">
                      <p className="text-xs font-medium text-indigo-300/90">
                        {term.term_zh}
                      </p>
                      <p className="text-xs leading-relaxed text-[#D1D5DB]">
                        {term.plain_explain}
                      </p>
                      {term.in_context && (
                        <p className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-[11.5px] leading-relaxed text-[#9CA3AF]">
                          语境：{term.in_context}
                        </p>
                      )}
                    </div>
                  )}
                </article>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
