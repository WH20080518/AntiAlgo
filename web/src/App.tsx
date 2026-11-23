import { CheckCircle2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GlossaryModal } from "./components/GlossaryModal";
import { IntelCard } from "./components/IntelCard";
import { ShortcutModal } from "./components/ShortcutModal";
import { TopBriefingBar, type RefreshNotice } from "./components/TopBriefingBar";
import { UpcomingSidebar } from "./components/UpcomingSidebar";
import type {
  DigestData,
  GlossaryPayload,
  GlossaryStats,
  GlossaryTerm,
  NotifyStatus,
  RefreshStatus,
  UserPrefs,
} from "./types";
import { getDomainMeta, sortDomains } from "./utils/format";
import {
  loadMasteredTermKeys,
  loadPrefs,
  loadReadIds,
  saveMasteredTermKeys,
  savePrefs,
  saveReadIds,
} from "./utils/storage";

export function App() {
  const [digest, setDigest] = useState<DigestData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<string>("all");
  const [focusedIndex, setFocusedIndex] = useState<number>(0);
  const [readIds, setReadIds] = useState<Set<string>>(() => loadReadIds());
  const [prefs, setPrefs] = useState<UserPrefs>(() => loadPrefs());
  const [unlockedSpoilers, setUnlockedSpoilers] = useState<Set<string>>(
    () => new Set()
  );
  const [shortcutOpen, setShortcutOpen] = useState(false);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [glossaryLoading, setGlossaryLoading] = useState(false);
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryTerm[]>([]);
  const [glossaryStatsOverride, setGlossaryStatsOverride] =
    useState<GlossaryStats | null>(null);
  const [glossaryStaticFallback, setGlossaryStaticFallback] = useState(false);
  const [glossaryUpdatingKey, setGlossaryUpdatingKey] = useState<string | null>(
    null
  );

  const [canRefresh, setCanRefresh] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshNotice, setRefreshNotice] = useState<RefreshNotice | null>(
    null
  );
  const [notifyEnabled, setNotifyEnabled] = useState<boolean | null>(null);
  const [notifyBusy, setNotifyBusy] = useState(false);

  const cardRefs = useRef<Map<string, HTMLElement>>(new Map());
  const pollTimerRef = useRef<number | null>(null);
  const noticeTimerRef = useRef<number | null>(null);

  const fetchDigest = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const response = await fetch("/digest.json", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}`);
      }
      const data = (await response.json()) as DigestData;
      setDigest(data);
      setGlossaryStatsOverride(null);
    } catch (err) {
      if (!options?.silent) {
        setError(
          err instanceof Error ? err.message : "无法加载 /digest.json 数据文件"
        );
      }
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const showTransientNotice = useCallback(
    (notice: RefreshNotice, durationMs = 4500) => {
      if (noticeTimerRef.current !== null) {
        window.clearTimeout(noticeTimerRef.current);
      }
      setRefreshNotice(notice);
      noticeTimerRef.current = window.setTimeout(() => {
        setRefreshNotice(null);
      }, durationMs);
    },
    []
  );

  const startStatusPolling = useCallback(() => {
    stopPolling();
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (!res.ok) return;
        const status = (await res.json()) as RefreshStatus;
        if (!status.running) {
          stopPolling();
          setIsRefreshing(false);
          if (status.last_ok !== false) {
            await fetchDigest({ silent: true });
            showTransientNotice({
              type: "success",
              text: status.last_summary || "已更新",
            });
          } else {
            showTransientNotice(
              {
                type: "error",
                text: status.last_error || "刷新失败",
              },
              6500
            );
          }
        }
      } catch {
        // Ignore transient poll errors while local server processes
      }
    }, 1500);
  }, [fetchDigest, showTransientNotice, stopPolling]);

  const handleRefresh = useCallback(async () => {
    if (isRefreshing || !canRefresh) return;
    setIsRefreshing(true);
    setRefreshNotice(null);
    try {
      const res = await fetch("/api/refresh", { method: "POST" });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      startStatusPolling();
    } catch {
      setIsRefreshing(false);
      showTransientNotice({ type: "error", text: "无法触发本地刷新" }, 5000);
    }
  }, [canRefresh, isRefreshing, showTransientNotice, startStatusPolling]);

  const handleToggleNotify = useCallback(async () => {
    if (!canRefresh || notifyBusy) return;
    setNotifyBusy(true);
    try {
      const res = await fetch("/api/notify/toggle", { method: "POST" });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const next = (await res.json()) as NotifyStatus;
      const enabled = next.effective_enabled ?? next.enabled;
      setNotifyEnabled(enabled);
      showTransientNotice({
        type: "success",
        text: enabled ? "已开启桌面静音提醒" : "已暂停桌面提醒",
      });
    } catch {
      showTransientNotice({ type: "error", text: "无法切换桌面提醒" }, 5000);
    } finally {
      setNotifyBusy(false);
    }
  }, [canRefresh, notifyBusy, showTransientNotice]);

  const loadGlossary = useCallback(async () => {
    setGlossaryLoading(true);
    try {
      const apiRes = await fetch("/api/terms", { cache: "no-store" });
      const apiCtype = apiRes.headers.get("content-type") || "";
      if (apiRes.ok && apiCtype.includes("application/json")) {
        const data = (await apiRes.json()) as GlossaryPayload;
        if (Array.isArray(data.terms)) {
          setGlossaryTerms(data.terms);
          setGlossaryStatsOverride(
            data.stats ?? {
              total: data.terms.length,
              mastered: data.terms.filter((t) => t.is_mastered).length,
            }
          );
          setGlossaryStaticFallback(false);
          return;
        }
      }
    } catch {
      // Static mode: silently fall back to /terms.json
    } finally {
      setGlossaryLoading(false);
    }

    setGlossaryLoading(true);
    try {
      const staticRes = await fetch("/terms.json", { cache: "no-store" });
      if (!staticRes.ok) {
        setGlossaryTerms([]);
        setGlossaryStatsOverride({ total: 0, mastered: 0 });
        setGlossaryStaticFallback(true);
        return;
      }
      const data = (await staticRes.json()) as GlossaryPayload;
      const localOverrides = loadMasteredTermKeys();
      const mergedTerms = (data.terms ?? []).map((term) => ({
        ...term,
        is_mastered:
          typeof localOverrides[term.term_key] === "boolean"
            ? localOverrides[term.term_key]
            : Boolean(term.is_mastered),
      }));
      setGlossaryTerms(mergedTerms);
      setGlossaryStatsOverride({
        total: mergedTerms.length,
        mastered: mergedTerms.filter((t) => t.is_mastered).length,
      });
      setGlossaryStaticFallback(true);
    } catch {
      setGlossaryTerms([]);
      setGlossaryStaticFallback(true);
    } finally {
      setGlossaryLoading(false);
    }
  }, []);

  const openGlossary = useCallback(() => {
    setShortcutOpen(false);
    setGlossaryOpen(true);
    void loadGlossary();
  }, [loadGlossary]);

  const handleToggleTermMastered = useCallback(
    async (termKey: string, nextMastered: boolean) => {
      setGlossaryUpdatingKey(termKey);
      let updatedTermsSnapshot: GlossaryTerm[] = [];
      setGlossaryTerms((prev) => {
        updatedTermsSnapshot = prev.map((term) =>
          term.term_key === termKey
            ? { ...term, is_mastered: nextMastered }
            : term
        );
        return updatedTermsSnapshot;
      });
      setGlossaryStatsOverride({
        total: updatedTermsSnapshot.length,
        mastered: updatedTermsSnapshot.filter((t) => t.is_mastered).length,
      });

      const persistLocal = () => {
        const current = loadMasteredTermKeys();
        current[termKey] = nextMastered;
        saveMasteredTermKeys(current);
      };

      if (glossaryStaticFallback) {
        persistLocal();
        setGlossaryUpdatingKey(null);
        return;
      }

      try {
        const res = await fetch("/api/terms/mastered", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ term_key: termKey, mastered: nextMastered }),
        });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as { stats?: GlossaryStats };
        if (data.stats) {
          setGlossaryStatsOverride(data.stats);
        }
      } catch {
        persistLocal();
      } finally {
        setGlossaryUpdatingKey(null);
      }
    },
    [glossaryStaticFallback]
  );

  useEffect(() => {
    void fetchDigest();
  }, [fetchDigest]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (!res.ok) return;
        const ctype = res.headers.get("content-type") || "";
        if (!ctype.includes("application/json")) return;
        const status = (await res.json()) as RefreshStatus;
        if (cancelled || status.service !== "anti-algo-hub") return;
        setCanRefresh(true);
        if (status.running) {
          setIsRefreshing(true);
          startStatusPolling();
        }
        try {
          const notifyRes = await fetch("/api/notify/status", {
            cache: "no-store",
          });
          if (notifyRes.ok && !cancelled) {
            const notifyStatus = (await notifyRes.json()) as NotifyStatus;
            setNotifyEnabled(
              notifyStatus.effective_enabled ?? notifyStatus.enabled
            );
          }
        } catch {
          // Notify status endpoint unavailable on older runtime
        }
      } catch {
        // Pure static hosting (e.g. vite preview): hide refresh button silently
      }
    })();
    return () => {
      cancelled = true;
      stopPolling();
      if (noticeTimerRef.current !== null) {
        window.clearTimeout(noticeTimerRef.current);
      }
    };
  }, [startStatusPolling, stopPolling]);

  useEffect(() => {
    saveReadIds(readIds);
  }, [readIds]);

  useEffect(() => {
    savePrefs(prefs);
  }, [prefs]);

  const domains = useMemo(() => {
    if (!digest) return [];
    return sortDomains(digest.items.map((item) => item.domain));
  }, [digest]);

  const tabOptions = useMemo(
    () => [
      { key: "all", label: "全部" },
      ...domains.map((d) => ({ key: d, label: getDomainMeta(d).label })),
    ],
    [domains]
  );

  const domainItems = useMemo(() => {
    if (!digest) return [];
    if (activeTab === "all") return digest.items;
    return digest.items.filter((item) => item.domain === activeTab);
  }, [digest, activeTab]);

  const visibleItems = useMemo(() => {
    if (!prefs.hideRead) return domainItems;
    return domainItems.filter((item) => !readIds.has(item.id));
  }, [domainItems, prefs.hideRead, readIds]);

  const tabCounts = useMemo(() => {
    const counts: Record<string, number> = { all: 0 };
    if (!digest) return counts;
    for (const item of digest.items) {
      if (prefs.hideRead && readIds.has(item.id)) continue;
      counts.all += 1;
      counts[item.domain] = (counts[item.domain] ?? 0) + 1;
    }
    return counts;
  }, [digest, prefs.hideRead, readIds]);

  const clampedFocusedIndex = useMemo(() => {
    if (visibleItems.length === 0) return -1;
    return Math.max(0, Math.min(focusedIndex, visibleItems.length - 1));
  }, [focusedIndex, visibleItems.length]);

  const focusedItem =
    clampedFocusedIndex >= 0 ? visibleItems[clampedFocusedIndex] : null;

  useEffect(() => {
    if (!focusedItem) return;
    const node = cardRefs.current.get(focusedItem.id);
    if (node) {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [focusedItem]);

  const toggleRead = useCallback((id: string) => {
    setReadIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleSpoiler = useCallback((id: string) => {
    setUnlockedSpoilers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;

      if (event.key === "Escape") {
        if (glossaryOpen) {
          event.preventDefault();
          setGlossaryOpen(false);
          return;
        }
        if (shortcutOpen) {
          event.preventDefault();
          setShortcutOpen(false);
        }
        return;
      }

      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }

      if (event.key === "g") {
        event.preventDefault();
        if (glossaryOpen) {
          setGlossaryOpen(false);
        } else {
          openGlossary();
        }
        return;
      }

      if (event.key === "?") {
        event.preventDefault();
        setGlossaryOpen(false);
        setShortcutOpen((prev) => !prev);
        return;
      }

      if (shortcutOpen || glossaryOpen) return;

      if (event.key === "j" || event.key === "ArrowDown") {
        if (visibleItems.length === 0) return;
        event.preventDefault();
        setFocusedIndex(
          Math.min(visibleItems.length - 1, Math.max(0, clampedFocusedIndex) + 1)
        );
        return;
      }

      if (event.key === "k" || event.key === "ArrowUp") {
        if (visibleItems.length === 0) return;
        event.preventDefault();
        setFocusedIndex(Math.max(0, clampedFocusedIndex - 1));
        return;
      }

      if (event.key === "m" && focusedItem) {
        event.preventDefault();
        toggleRead(focusedItem.id);
        return;
      }

      if (event.key === "o" && focusedItem) {
        event.preventDefault();
        window.open(focusedItem.url, "_blank", "noopener,noreferrer");
        return;
      }

      if (
        event.key === "Enter" &&
        focusedItem &&
        target &&
        target === document.body
      ) {
        event.preventDefault();
        window.open(focusedItem.url, "_blank", "noopener,noreferrer");
        return;
      }

      if (event.key === "s" && focusedItem && focusedItem.spoiler_payload) {
        event.preventDefault();
        toggleSpoiler(focusedItem.id);
        return;
      }

      const num = Number.parseInt(event.key, 10);
      if (!Number.isNaN(num) && num >= 1 && num <= tabOptions.length) {
        event.preventDefault();
        setActiveTab(tabOptions[num - 1].key);
        setFocusedIndex(0);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    clampedFocusedIndex,
    focusedItem,
    glossaryOpen,
    openGlossary,
    shortcutOpen,
    tabOptions,
    toggleRead,
    toggleSpoiler,
    visibleItems.length,
  ]);

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 px-4 py-8 sm:px-6">
        <div className="bento-surface h-36 animate-pulse rounded-2xl" />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-10">
          <div className="space-y-3 lg:col-span-7">
            <div className="bento-surface h-28 animate-pulse rounded-xl" />
            <div className="bento-surface h-28 animate-pulse rounded-xl" />
            <div className="bento-surface h-28 animate-pulse rounded-xl" />
          </div>
          <div className="bento-surface h-64 animate-pulse rounded-2xl lg:col-span-3" />
        </div>
      </div>
    );
  }

  if (error || !digest) {
    return (
      <div className="mx-auto flex min-h-screen max-w-xl items-center justify-center px-4">
        <div className="bento-surface w-full rounded-2xl p-6 text-center">
          <h1 className="text-base font-semibold text-[#F3F4F6]">
            尚未读取到今日情报数据
          </h1>
          <p className="mt-2 text-xs leading-relaxed text-[#9CA3AF]">
            请先在项目根目录运行采集与整理管道生成{" "}
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[#E6E8EC]">
              web/public/digest.json
            </code>
            ：
          </p>
          <p className="mt-2">
            <code className="rounded bg-white/[0.06] px-2 py-1 font-mono text-[11px] text-indigo-300">
              uv run --offline python -m pipeline.run
            </code>
          </p>
          {error && (
            <p className="mt-2 font-mono text-[11px] text-[#6B7280]">{error}</p>
          )}
          <button
            type="button"
            onClick={() => void fetchDigest()}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-3.5 py-1.5 text-xs font-medium text-[#E6E8EC] transition-colors hover:bg-white/[0.08] cursor-pointer"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            <span>重新加载</span>
          </button>
        </div>
      </div>
    );
  }

  const totalReadInDigest = digest.items.filter((i) =>
    readIds.has(i.id)
  ).length;
  const hiddenReadInCurrentTab = domainItems.length - visibleItems.length;
  const effectiveGlossaryStats =
    glossaryStatsOverride ?? digest.glossary_stats ?? null;

  return (
    <div className="min-h-screen bg-[#0A0B0D] text-[#E6E8EC]">
      <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 sm:px-6 lg:py-8">
        <TopBriefingBar
          generatedAt={digest.generated_at}
          briefings={digest.top_briefings}
          antiSpoiler={prefs.antiSpoiler}
          hideRead={prefs.hideRead}
          canRefresh={canRefresh}
          isRefreshing={isRefreshing}
          refreshNotice={refreshNotice}
          notifyEnabled={notifyEnabled}
          notifyBusy={notifyBusy}
          onRefresh={() => void handleRefresh()}
          onToggleNotify={() => void handleToggleNotify()}
          onToggleAntiSpoiler={() =>
            setPrefs((prev) => ({ ...prev, antiSpoiler: !prev.antiSpoiler }))
          }
          onToggleHideRead={() =>
            setPrefs((prev) => ({ ...prev, hideRead: !prev.hideRead }))
          }
          onOpenShortcuts={() => setShortcutOpen(true)}
        />

        <main className="grid grid-cols-1 gap-5 lg:grid-cols-10">
          <section className="space-y-3 lg:col-span-7">
            <nav
              aria-label="情报领域切换"
              className="bento-surface flex flex-wrap items-center justify-between gap-2 rounded-xl p-1.5"
            >
              <div className="flex flex-wrap items-center gap-1">
                {tabOptions.map((tab, idx) => {
                  const active = activeTab === tab.key;
                  const count = tabCounts[tab.key] ?? 0;
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      onClick={() => {
                        setActiveTab(tab.key);
                        setFocusedIndex(0);
                      }}
                      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
                        active
                          ? "bg-white/[0.08] text-[#F3F4F6] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]"
                          : "text-[#8B93A1] hover:bg-white/[0.03] hover:text-[#D1D5DB]"
                      }`}
                    >
                      <span className="hidden font-mono text-[10px] text-[#6B7280] sm:inline">
                        {idx + 1}
                      </span>
                      <span>{tab.label}</span>
                      <span className="font-mono text-[11px] text-[#6B7280] tabular-nums">
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>

              {hiddenReadInCurrentTab > 0 && (
                <button
                  type="button"
                  onClick={() =>
                    setPrefs((prev) => ({ ...prev, hideRead: false }))
                  }
                  className="px-2.5 py-1 text-[11px] text-[#8B93A1] transition-colors hover:text-[#E6E8EC] cursor-pointer"
                >
                  已隐藏 {hiddenReadInCurrentTab} 条已读 · 展开
                </button>
              )}
            </nav>

            <div className="space-y-2.5">
              {visibleItems.map((item, idx) => (
                <IntelCard
                  key={item.id}
                  item={item}
                  isFocused={idx === clampedFocusedIndex}
                  isRead={readIds.has(item.id)}
                  antiSpoiler={prefs.antiSpoiler}
                  isSpoilerUnlocked={unlockedSpoilers.has(item.id)}
                  onFocus={() => setFocusedIndex(idx)}
                  onToggleRead={toggleRead}
                  onToggleSpoiler={toggleSpoiler}
                  cardRef={(el) => {
                    if (el) {
                      cardRefs.current.set(item.id, el);
                    } else {
                      cardRefs.current.delete(item.id);
                    }
                  }}
                />
              ))}
            </div>

            <div className="bento-surface rounded-xl px-5 py-6 text-center">
              <div className="mx-auto flex h-9 w-9 items-center justify-center rounded-full border border-emerald-500/25 bg-emerald-500/10 text-emerald-300">
                <CheckCircle2 className="h-4 w-4" />
              </div>
              <h3 className="mt-2.5 text-sm font-medium text-[#E6E8EC]">
                今日情报已清空
              </h3>
              <p className="mt-1 text-xs text-[#6B7280] tabular-nums">
                {visibleItems.length === 0 && domainItems.length > 0
                  ? "当前分类全部条目已标记已读，已停止信息流推荐。"
                  : `以上即为全部 ${visibleItems.length} 条高信噪比情报 · 无无限滚动，保持专注`}
              </p>
              {prefs.hideRead && hiddenReadInCurrentTab > 0 && (
                <button
                  type="button"
                  onClick={() =>
                    setPrefs((prev) => ({ ...prev, hideRead: false }))
                  }
                  className="mt-3 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-[#9CA3AF] transition-colors hover:bg-white/[0.07] hover:text-[#E6E8EC] cursor-pointer"
                >
                  显示 {hiddenReadInCurrentTab} 条已读条目
                </button>
              )}
            </div>
          </section>

          <div className="lg:col-span-3">
            <UpcomingSidebar
              dailyTerm={digest.daily_term}
              glossaryStats={effectiveGlossaryStats}
              upcoming={digest.upcoming}
              totalCount={digest.items.length}
              readCount={totalReadInDigest}
              onOpenGlossary={openGlossary}
              onOpenShortcuts={() => setShortcutOpen(true)}
            />
          </div>
        </main>
      </div>

      <GlossaryModal
        open={glossaryOpen}
        loading={glossaryLoading}
        terms={glossaryTerms}
        stats={
          effectiveGlossaryStats ?? {
            total: glossaryTerms.length,
            mastered: glossaryTerms.filter((t) => t.is_mastered).length,
          }
        }
        updatingKey={glossaryUpdatingKey}
        onClose={() => setGlossaryOpen(false)}
        onToggleMastered={(key, mastered) =>
          void handleToggleTermMastered(key, mastered)
        }
      />

      <ShortcutModal
        open={shortcutOpen}
        onClose={() => setShortcutOpen(false)}
      />
    </div>
  );
}

export default App;
