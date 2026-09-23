import type {
  BatchStatus,
  Manifest,
  ModuleSummary,
  PagedRows,
  SpuAnalysis,
} from "./types";

const BASE = "/api";

async function get<T>(url: string): Promise<T> {
  const res = await fetch(BASE + url);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export function fetchManifest() {
  return get<Manifest>("/manifest");
}

export interface RowsQuery {
  shop?: string;
  start?: string; // 区间起点 YYYY-MM-DD（与 end 构成范围）
  end?: string; // 区间终点 YYYY-MM-DD
  keyword?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  page: number;
  page_size: number;
}

export function fetchRows(moduleId: string, q: RowsQuery) {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.start) params.set("start", q.start);
  if (q.end) params.set("end", q.end);
  if (q.keyword) params.set("keyword", q.keyword);
  if (q.sort_by) params.set("sort_by", q.sort_by);
  if (q.sort_order) params.set("sort_order", q.sort_order);
  params.set("page", String(q.page));
  params.set("page_size", String(q.page_size));
  return get<PagedRows>(`/module/${moduleId}/rows?${params.toString()}`);
}

export function fetchSummary(moduleId: string) {
  return get<ModuleSummary>(`/module/${moduleId}/summary`);
}

export function fetchStatus() {
  return get<BatchStatus>("/status");
}

export interface SpuAnalysisQuery {
  shop?: string;
  start?: string;
  end?: string;
}

export function fetchSpuAnalysis(moduleId: string, spu: string, q: SpuAnalysisQuery = {}) {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.start) params.set("start", q.start);
  if (q.end) params.set("end", q.end);
  const qs = params.toString();
  return get<SpuAnalysis>(`/module/${moduleId}/spu/${spu}/analysis${qs ? "?" + qs : ""}`);
}

export async function postRefresh(): Promise<BatchStatus> {
  const res = await fetch(`${BASE}/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as BatchStatus;
}
