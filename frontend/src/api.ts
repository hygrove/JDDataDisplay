// 后端接口封装：统一走 /api 前缀，所有请求失败都转成 Error 抛出，由调用方捕获展示。
//
// ⚠️ BASE 用相对路径 "/api" 而不是写死 http://127.0.0.1:8000：
//    生产是单端口 8000 同源部署，开发时由 vite 的 proxy 转发，
//    写死域名会让「换端口部署」或「走 https」时全部请求失败。
import type {
  BatchStatus,
  Manifest,
  ModuleSummary,
  PagedRows,
  SpuAnalysis,
} from "./types";

/** 接口统一前缀（配合 vite dev server 的 proxy 转发到 8000）。 */
const BASE = "/api";

/**
 * 通用 GET 请求：非 2xx 响应统一抛 Error（错误体带上状态码与后端返回的详情文本）。
 *
 * @template T 期望的响应数据类型。
 * @param {string} url - 以 "/" 开头的接口路径（不含 /api 前缀），如 "/manifest"。
 * @returns {Promise<T>} 解析后的响应 JSON。
 * @throws {Error} 响应非 2xx 时抛出，message 形如 `404 数据文件不存在：xxx`；
 *      网络中断时 fetch 自身也会 reject。
 * @example
 * ```ts
 * const manifest = await get<Manifest>("/manifest");
 * ```
 */
async function get<T>(url: string): Promise<T> {
  const res = await fetch(BASE + url);
  // 把状态码 + 后端详情文本一并塞进错误，便于直接把 message 显示在页面上定位问题
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

/**
 * 拉取模块清单 manifest（含各模块的店铺、日期区间、SPU 数）。
 *
 * @returns {Promise<Manifest>} 模块清单。
 * @throws {Error} 接口非 2xx（常见于「还没跑过批处理」返回 404）。
 * @example
 * ```ts
 * const manifest = await fetchManifest();
 * manifest.modules[0]?.module_id; // "pop_spu_detail"
 * ```
 */
export function fetchManifest() {
  return get<Manifest>("/manifest");
}

/**
 * 模块分页明细的查询参数。
 */
export interface RowsQuery {
  /** 店铺名过滤；留空 = 全部店铺 */
  shop?: string;
  /** 区间起点 YYYY-MM-DD（与 end 构成范围） */
  start?: string;
  /** 区间终点 YYYY-MM-DD */
  end?: string;
  /** SPU 号 / 商品名称的模糊搜索词 */
  keyword?: string;
  /** 排序字段：指标 key 或 "spu" / "shop" */
  sort_by?: string;
  /** 排序方向 */
  sort_order?: "asc" | "desc";
  /** 页码，从 1 开始 */
  page: number;
  /** 每页条数，最大 500 */
  page_size: number;
}

/**
 * 拉取模块分页明细（店铺过滤 / 日期区间 / 关键词 / 排序 / 分页）。
 *
 * @param {string} moduleId - 模块标识，如 "pop_spu_detail"。
 * @param {RowsQuery} q - 查询参数；shop/start/end/keyword/sort_* 为可选，
 *      page 与 page_size 必填。
 * @returns {Promise<PagedRows>} 含 total 与当页 rows 的分页结果。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const paged = await fetchRows("pop_spu_detail", {
 *   start: "2026-09-18", end: "2026-09-24", sort_by: "amount", page: 1, page_size: 300,
 * });
 * paged.total; // 过滤后的总条数
 * ```
 */
export function fetchRows(moduleId: string, q: RowsQuery) {
  const params = new URLSearchParams();
  // 只把「有值」的可选参数拼进 query：空值拼进去会让后端把它当空字符串处理
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

/**
 * 拉取模块汇总（图表用：店铺×日期趋势、TOP SPU、店铺汇总）。
 *
 * @param {string} moduleId - 模块标识。
 * @returns {Promise<ModuleSummary>} 模块汇总数据。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const summary = await fetchSummary("pop_spu_detail");
 * ```
 */
export function fetchSummary(moduleId: string) {
  return get<ModuleSummary>(`/module/${moduleId}/summary`);
}

/**
 * 拉取上次批处理运行状态（页面顶部「上次更新 / 是否成功」展示）。
 *
 * @returns {Promise<BatchStatus>} 批处理状态；从未跑过时 success 为 false。
 * @throws {Error} 接口非 2xx（正常情况下 /status 不会 404，缺文件会返回默认结构）。
 * @example
 * ```ts
 * const st = await fetchStatus();
 * st.success; // true / false
 * ```
 */
export function fetchStatus() {
  return get<BatchStatus>("/status");
}

/**
 * 单品分析的查询参数（均可选）。
 */
export interface SpuAnalysisQuery {
  /** 店铺名；留空 = 该 SPU 跨全部店铺合计 */
  shop?: string;
  /** 起始日期 YYYY-MM-DD；留空 = 数据最早日期 */
  start?: string;
  /** 截止日期 YYYY-MM-DD；留空 = 数据最新日期 */
  end?: string;
}

/**
 * 拉取单个 SPU 的分析数据（逐日指标 + 工作日/节假日对比 + 综合分析文案）。
 *
 * @param {string} moduleId - 模块标识。
 * @param {string} spu - SPU 编号。
 * @param {SpuAnalysisQuery} [q={}] - 可选的店铺与日期区间；留空表示全店铺全区间。
 * @returns {Promise<SpuAnalysis>} 单品分析结果。
 * @throws {Error} 接口非 2xx（常见于该 SPU 在所选区间内没有数据 -> 404）。
 * @example
 * ```ts
 * const a = await fetchSpuAnalysis("pop_spu_detail", "100123", {
 *   shop: "钻芯旗舰店", start: "2026-09-18", end: "2026-09-24",
 * });
 * a.daily.length; // 区间天数
 * ```
 */
export function fetchSpuAnalysis(moduleId: string, spu: string, q: SpuAnalysisQuery = {}) {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.start) params.set("start", q.start);
  if (q.end) params.set("end", q.end);
  const qs = params.toString();
  // 无查询参数时不要留下多余的 "?"，避免后端路由匹配出意外
  return get<SpuAnalysis>(`/module/${moduleId}/spu/${spu}/analysis${qs ? "?" + qs : ""}`);
}

/**
 * 手动触发后端重跑批处理（同步执行，耗时可能数十秒，完成后后端会清缓存）。
 *
 * @returns {Promise<BatchStatus>} 本次批处理结果（success / message / 耗时）。
 * @throws {Error} 接口非 2xx；注意批处理本身失败时返回的是 success=false 的
 *      BatchStatus（HTTP 仍为 200），只有网络/服务异常才会走这里。
 * @example
 * ```ts
 * const st = await postRefresh();
 * if (!st.success) alert(st.message);
 * ```
 */
export async function postRefresh(): Promise<BatchStatus> {
  const res = await fetch(`${BASE}/refresh`, { method: "POST" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as BatchStatus;
}
