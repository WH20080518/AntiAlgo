import type { UpcomingItem } from "../types";

export interface DomainMeta {
  label: string;
  badgeClass: string;
  dotClass: string;
}

const DOMAIN_META_MAP: Record<string, DomainMeta> = {
  tech: {
    label: "AI 与开源",
    badgeClass:
      "border-sky-500/20 bg-sky-500/10 text-sky-300/90",
    dotClass: "bg-sky-400/80",
  },
  esports: {
    label: "CS2 赛事",
    badgeClass:
      "border-amber-500/20 bg-amber-500/10 text-amber-300/90",
    dotClass: "bg-amber-400/80",
  },
  music: {
    label: "欧美与 K-pop",
    badgeClass:
      "border-emerald-500/20 bg-emerald-500/10 text-emerald-300/90",
    dotClass: "bg-emerald-400/80",
  },
};

const CANONICAL_DOMAIN_ORDER = ["tech", "esports", "music"];

export function getDomainMeta(domain: string): DomainMeta {
  const key = domain.trim().toLowerCase();
  if (DOMAIN_META_MAP[key]) {
    return DOMAIN_META_MAP[key];
  }
  const fallbackLabel = key
    ? key.charAt(0).toUpperCase() + key.slice(1)
    : "综合";
  return {
    label: fallbackLabel,
    badgeClass: "border-zinc-500/25 bg-zinc-500/10 text-zinc-300",
    dotClass: "bg-zinc-400",
  };
}

export function sortDomains(domains: string[]): string[] {
  const unique = Array.from(new Set(domains));
  return unique.sort((a, b) => {
    const idxA = CANONICAL_DOMAIN_ORDER.indexOf(a);
    const idxB = CANONICAL_DOMAIN_ORDER.indexOf(b);
    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
    if (idxA !== -1) return -1;
    if (idxB !== -1) return 1;
    return a.localeCompare(b);
  });
}

export function getLocalTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Local";
  } catch {
    return "Local";
  }
}

export function formatLocalDateTime(isoString: string): string {
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return isoString;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatRelativeTime(isoString: string, nowMs = Date.now()): string {
  const date = new Date(isoString);
  const ts = date.getTime();
  if (Number.isNaN(ts)) {
    return "";
  }
  const diffSec = Math.round((nowMs - ts) / 1000);
  if (diffSec < 60) {
    return "刚刚";
  }
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) {
    return `${diffMin} 分钟前`;
  }
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 48) {
    return `${diffHour} 小时前`;
  }
  const diffDay = Math.floor(diffHour / 24);
  return `${diffDay} 天前`;
}

export function formatCountdown(
  isoString: string,
  nowMs = Date.now()
): { text: string; isLiveOrPast: boolean; localTime: string } {
  const date = new Date(isoString);
  const ts = date.getTime();
  if (Number.isNaN(ts)) {
    return { text: "--", isLiveOrPast: false, localTime: isoString };
  }

  const localTime = formatLocalDateTime(isoString);
  const diffMs = ts - nowMs;
  if (diffMs <= 0) {
    return { text: "已开始", isLiveOrPast: true, localTime };
  }

  const totalMinutes = Math.floor(diffMs / 60000);
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;

  if (days > 0) {
    return {
      text: `还有 ${days} 天 ${hours} 小时`,
      isLiveOrPast: false,
      localTime,
    };
  }
  if (hours > 0) {
    return {
      text: `还有 ${hours} 小时 ${minutes} 分`,
      isLiveOrPast: false,
      localTime,
    };
  }
  return {
    text: `还有 ${Math.max(1, minutes)} 分钟`,
    isLiveOrPast: false,
    localTime,
  };
}

export interface UpcomingDisplay {
  badge: string;
  caption: string | null;
  isLive: boolean;
  isToday: boolean;
}

const SOURCE_LABEL_MAP: Record<string, string> = {
  pandascore: "PandaScore",
  musicbrainz: "MusicBrainz",
  llm_extracted: "新闻提取",
  manual: "手动置顶",
};

export function getUpcomingSourceLabel(source?: string): string {
  if (!source) return "";
  return SOURCE_LABEL_MAP[source] ?? source;
}

function formatNaturalLocalDateTime(date: Date, now: Date): string {
  const targetDay = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate()
  );
  const todayStart = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate()
  );
  const diffDays = Math.round(
    (targetDay.getTime() - todayStart.getTime()) / 86_400_000
  );
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");

  if (diffDays === 0) {
    return date.getHours() >= 18 ? `今晚 ${hh}:${mm}` : `今天 ${hh}:${mm}`;
  }
  if (diffDays === 1) {
    return `明天 ${hh}:${mm}`;
  }
  return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
}

export function formatUpcomingDisplay(
  event: UpcomingItem,
  nowMs = Date.now()
): UpcomingDisplay {
  const precision = event.precision === "date_only" ? "date_only" : "datetime";
  const now = new Date(nowMs);

  if (precision === "date_only") {
    const datePart = event.start_time.slice(0, 10);
    const parts = datePart.split("-").map((n) => Number.parseInt(n, 10));
    if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) {
      const [y, m, d] = parts;
      const targetStart = new Date(y, m - 1, d);
      const todayStart = new Date(
        now.getFullYear(),
        now.getMonth(),
        now.getDate()
      );
      const diffDays = Math.round(
        (targetStart.getTime() - todayStart.getTime()) / 86_400_000
      );
      const isMusic = event.domain.trim().toLowerCase() === "music";

      if (diffDays <= 0) {
        return {
          badge: isMusic ? "今天发行" : "今天",
          caption: `${m}月${d}日`,
          isLive: false,
          isToday: true,
        };
      }
      if (diffDays === 1) {
        return {
          badge: isMusic ? "明天发行" : "明天",
          caption: `${m}月${d}日`,
          isLive: false,
          isToday: false,
        };
      }
      return {
        badge: `${diffDays} 天后 · ${m}月${d}日`,
        caption: isMusic ? "预计发行日" : "预计日期",
        isLive: false,
        isToday: false,
      };
    }
  }

  const date = new Date(event.start_time);
  const ts = date.getTime();
  if (Number.isNaN(ts)) {
    return {
      badge: "--",
      caption: event.start_time,
      isLive: false,
      isToday: false,
    };
  }

  const naturalLabel = formatNaturalLocalDateTime(date, now);
  const diffMs = ts - nowMs;
  if (diffMs <= 0) {
    return {
      badge: "进行中",
      caption: `已于 ${naturalLabel} 开始`,
      isLive: true,
      isToday: true,
    };
  }

  const totalMinutes = Math.floor(diffMs / 60_000);
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;

  let badge: string;
  if (days > 0) {
    badge = `还有 ${days} 天 ${hours} 小时`;
  } else if (hours > 0) {
    badge = `还有 ${hours} 小时 ${minutes} 分`;
  } else {
    badge = `还有 ${Math.max(1, minutes)} 分钟`;
  }

  return {
    badge,
    caption: naturalLabel,
    isLive: false,
    isToday: false,
  };
}

export function extractHostname(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl);
    return parsed.hostname.replace(/^www\./, "");
  } catch {
    return rawUrl;
  }
}
