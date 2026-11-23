import { BookMarked, BookOpen, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import type { DailyTerm, GlossaryStats } from "../types";
import { getDomainMeta } from "../utils/format";

interface DailyTermCardProps {
  term?: DailyTerm | null;
  glossaryStats?: GlossaryStats | null;
  onOpenGlossary?: () => void;
}

export function DailyTermCard({
  term,
  glossaryStats,
  onOpenGlossary,
}: DailyTermCardProps) {
  const [contextOpen, setContextOpen] = useState(false);

  if (!term) return null;

  const domainMeta = getDomainMeta(term.domain);

  return (
    <section className="bento-surface rounded-2xl p-4 sm:p-5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BookOpen className="h-4 w-4 text-indigo-300" />
          <h2 className="text-xs font-semibold tracking-wide text-[#D1D5DB]">
            每日中英术语
          </h2>
        </div>
        <div className="flex items-center gap-1.5">
          {onOpenGlossary && (
            <button
              type="button"
              onClick={onOpenGlossary}
              title="打开历史术语本与自测模式 (g)"
              className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-[#D1D5DB] transition-colors hover:border-indigo-400/35 hover:bg-indigo-500/10 hover:text-indigo-200 cursor-pointer"
            >
              <BookMarked className="h-3 w-3 text-indigo-300" />
              <span>术语本</span>
              {typeof glossaryStats?.total === "number" && (
                <span className="font-mono text-[#9CA3AF] tabular-nums">
                  {glossaryStats.total}
                </span>
              )}
            </button>
          )}
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-medium ${domainMeta.badgeClass}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${domainMeta.dotClass}`} />
            {domainMeta.label}
          </span>
        </div>
      </div>

      <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold tracking-tight text-[#F3F4F6]">
            {term.term_en}
          </p>
          {term.phonetic_or_abbr && (
            <span className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] text-[#9CA3AF]">
              {term.phonetic_or_abbr}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs font-medium text-indigo-300/90">
          {term.term_zh}
        </p>

        <p className="mt-2.5 text-xs leading-relaxed text-[#D1D5DB]">
          {term.plain_explain}
        </p>

        {term.in_context && (
          <div className="mt-3 border-t border-white/[0.06] pt-2.5">
            <button
              type="button"
              onClick={() => setContextOpen((prev) => !prev)}
              className="flex w-full items-center justify-between text-[11px] font-medium text-[#8B93A1] transition-colors hover:text-[#E6E8EC] cursor-pointer"
            >
              <span>今日语境</span>
              {contextOpen ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
            </button>
            {contextOpen && (
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-[#9CA3AF]">
                {term.in_context}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
