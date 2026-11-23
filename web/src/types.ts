export interface SpoilerPayload {
  label?: string;
  match?: string | null;
  score?: string | null;
  winner?: string | null;
  summary?: string | null;
  [key: string]: unknown;
}

export interface DailyTerm {
  term_en: string;
  term_zh: string;
  domain: string;
  phonetic_or_abbr?: string;
  plain_explain: string;
  in_context: string;
}

export interface IntelItem {
  id: string;
  domain: string;
  source_name: string;
  title: string;
  summary: string;
  url: string;
  published_at: string;
  importance: number;
  related_links: string[];
  spoiler_payload: SpoilerPayload | string | null;
}

export interface UpcomingItem {
  domain: string;
  title: string;
  subtitle?: string;
  start_time: string;
  precision?: "datetime" | "date_only";
  tag: string;
  source?: string;
  url?: string;
}

export interface DigestData {
  generated_at: string;
  top_briefings: string[];
  daily_term?: DailyTerm | null;
  glossary_stats?: GlossaryStats | null;
  items: IntelItem[];
  upcoming: UpcomingItem[];
}

export interface GlossaryStats {
  total: number;
  mastered: number;
}

export interface GlossaryTerm extends DailyTerm {
  term_key: string;
  first_seen_date: string;
  last_seen_date: string;
  seen_count: number;
  is_mastered: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface GlossaryPayload {
  generated_at?: string;
  stats: GlossaryStats;
  terms: GlossaryTerm[];
}

export interface UserPrefs {
  antiSpoiler: boolean;
  hideRead: boolean;
}

export interface RefreshStatus {
  service?: string;
  running: boolean;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  last_ok?: boolean | null;
  last_summary?: string | null;
  last_error?: string | null;
}

export interface NotifyStatus {
  enabled: boolean;
  config_enabled?: boolean;
  effective_enabled?: boolean;
  enabled_since?: string | null;
  last_sent_at?: string | null;
  last_title?: string | null;
  poll_interval_seconds?: number;
  silent?: boolean;
}
