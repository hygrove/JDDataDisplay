// 后端接口封装：统一走 /api 前缀，所有请求失败都转成 Error 抛出，由调用方捕获展示。
//
// ⚠️ BASE 用相对路径 "/api" 而不是写死 http://127.0.0.1:8000：
//    生产是单端口 8000 同源部署，开发时由 vite 的 proxy 转发，
//    写死域名会让「换端口部署」或「走 https」时全部请求失败。
import type {
  BatchStatus,
  InventoryAlertsSummary,
  InventoryDistributions,
  InventoryMeta,
  InventoryPagedRows,
  InventoryRowsQuery,
  InventorySlowCity,
  InventorySummary,
  Manifest,
  ModuleSummary,
  PagedRows,
  RestockListResponse,
  RestockParams,
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
 * 通用 PUT 请求：与 get 相同的错误约定（非 2xx 抛 Error，message 带状态码与详情）。
 *
 * @template T 期望的响应数据类型。
 * @param {string} url - 以 "/" 开头的接口路径（不含 /api 前缀）。
 * @param {unknown} body - 请求体（JSON 序列化）。
 * @returns {Promise<T>} 解析后的响应 JSON。
 */
async function put<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

/**
 * 导出 xlsx 时单个指标的口径描述（与后端 ExportMetricSpec 对齐）。
 */
export interface ExportMetricAgg {
  /** "sum" 直接求和；"ratio" 先分别累加分子分母再相除 */
  kind: "sum" | "ratio";
  /** ratio 类型的分子指标 key */
  num?: string;
  /** ratio 类型的分母指标 key */
  den?: string;
}

/**
 * 导出 xlsx 时单个指标的定义（key + 中文标题 + 口径）。
 */
export interface ExportMetricSpec {
  /** 指标 key，需与后端 MetricRecord 字段名一致 */
  key: string;
  /** 中文展示名（导出表头） */
  title: string;
  /** 区间汇总口径 */
  agg: ExportMetricAgg;
}

/**
 * 导出 xlsx 的请求体：过滤条件 + 要导出的指标清单。
 */
export interface ExportPayload {
  /** 店铺名过滤；留空 = 全部店铺 */
  shop?: string;
  /** 区间起点 YYYY-MM-DD；留空 = 不限 */
  start?: string;
  /** 区间终点 YYYY-MM-DD；留空 = 不限 */
  end?: string;
  /** SPU 号 / 商品名称模糊搜索词；留空 = 不搜索 */
  keyword?: string;
  /** 排序字段：指标 key 或 "spu" / "shop" */
  sort_by?: string;
  /** 排序方向 */
  sort_order?: "asc" | "desc";
  /** 有序指标清单（来自 METRICS 唯一事实来源） */
  metrics: ExportMetricSpec[];
}

/**
 * 触发后端导出当前筛选条件的数据为 xlsx，返回文件 Blob。
 *
 * 后端按「后端全量」生成（不受前端无限滚动分页影响），返回 xlsx 二进制流；
 * 前端拿 Blob 后用 <a download> 触发下载（文件名在前端拼，见调用方）。
 *
 * @param {string} moduleId - 模块标识。
 * @param {ExportPayload} body - 过滤条件 + 指标清单。
 * @returns {Promise<Blob>} xlsx 文件 Blob。
 * @throws {Error} 接口非 2xx（如所选范围无数据 -> 404）。
 * @example
 * ```ts
 * const blob = await exportModuleXlsx("pop_spu_detail", { metrics: [...] });
 * ```
 */
export async function exportModuleXlsx(moduleId: string, body: ExportPayload): Promise<Blob> {
  const res = await fetch(`${BASE}/module/${moduleId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.blob();
}

// ====================== 库存管理模块 ======================

/**
 * 拉取库存模块元信息（指标清单 + 城市/店铺/日期列表）。
 *
 * @returns {Promise<InventoryMeta>} 首屏元信息；指标清单驱动矩阵列与勾选面板。
 * @throws {Error} 接口非 2xx（库存库尚未摄入时可能 500）。
 * @example
 * ```ts
 * const meta = await fetchInventoryMeta();
 * meta.cities; // ["北京", "武汉", ...]
 * ```
 */
export function fetchInventoryMeta(): Promise<InventoryMeta> {
  return get<InventoryMeta>("/inventory/meta");
}

/**
 * 拉取库存分页明细（店铺 / 日期 / 关键词 / 信息列筛选 / 排序 / 分页）。后端返回全列 dict，前端投影成矩阵。
 *
 * @remarks
 * 排序参数作用于**全部行**（后端先排序再分页），传 sort_by 即可跨页排序，
 * 而不是在当页 50 条内部排。
 *
 * @param {InventoryRowsQuery} q - 查询参数；shop/date/keyword/category/seller/shelf_status/sort_by/sort_order 可选，page/page_size 必填。
 * @returns {Promise<InventoryPagedRows>} 含 total、当页 rows、totals（指标合计）、city_totals（每城合计）。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const p = await fetchInventoryRows({ shop: "家配", date: "2026-10-18", page: 1, page_size: 50 });
 * p.total;          // 过滤后总行数
 * p.city_totals["city_stock"]; // 8 城现货库存合计数组
 * // 按现货库存合计降序取第 2 页（排序跨全量数据）
 * const q = await fetchInventoryRows({ sort_by: "city_stock", sort_order: "desc", page: 2, page_size: 50 });
 * ```
 */
export function fetchInventoryRows(q: InventoryRowsQuery): Promise<InventoryPagedRows> {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.date) params.set("date", q.date);
  if (q.keyword) params.set("keyword", q.keyword);
  // 多选筛选：后端按逗号分隔解析（category / seller / shelf_status）
  if (q.category?.length) params.set("category", q.category.join(","));
  if (q.seller?.length) params.set("seller", q.seller.join(","));
  if (q.shelf_status?.length) params.set("shelf_status", q.shelf_status.join(","));
  // 排序：指标 key + 方向，缺省 desc
  if (q.sort_by) {
    params.set("sort_by", q.sort_by);
    params.set("sort_order", q.sort_order ?? "desc");
  }
  params.set("page", String(q.page));
  params.set("page_size", String(q.page_size));
  return get<InventoryPagedRows>(`/inventory/rows?${params.toString()}`);
}

/**
 * 构建库存矩阵导出的 URL（GET，供 `<a href>` 浏览器原生下载，与补货导出同一套路）。
 *
 * @remarks
 * - 筛选 / 排序参数与 `fetchInventoryRows` 同口径，导出的是当前筛选下的**全量**行
 *   （不受分页影响）；`metrics` 即「指标配置」勾选的指标 key，决定导出的指标列。
 * - 文件名由后端 `Content-Disposition` 决定（前端不必猜最新快照日期）；
 *   失败时后端也返回附件（错误说明 txt），浏览器只会下载、不会导航走页面。
 *
 * @param {object} q - 查询参数；metrics 必填（空数组 = 只用后端默认勾选）。
 * @returns {string} 完整的导出 URL（含 /api 前缀与查询串）。
 * @example
 * ```ts
 * const url = inventoryMatrixExportUrl({
 *   shop: "家配", date: "2026-10-18", keyword: "滤芯",
 *   sort_by: "city_stock", sort_order: "desc",
 *   metrics: ["city_stock", "nat_slow"],
 * });
 * const a = document.createElement("a");
 * a.href = url; a.click(); // 触发原生下载
 * ```
 */
export function inventoryMatrixExportUrl(q: {
  shop?: string;
  date?: string;
  keyword?: string;
  category?: string[];
  seller?: string[];
  shelf_status?: string[];
  sort_by?: string;
  sort_order?: "asc" | "desc";
  metrics: string[];
}): string {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.date) params.set("date", q.date);
  if (q.keyword) params.set("keyword", q.keyword);
  // 多选筛选：后端按逗号分隔解析（与 fetchInventoryRows 一致）
  if (q.category?.length) params.set("category", q.category.join(","));
  if (q.seller?.length) params.set("seller", q.seller.join(","));
  if (q.shelf_status?.length) params.set("shelf_status", q.shelf_status.join(","));
  if (q.sort_by) {
    params.set("sort_by", q.sort_by);
    params.set("sort_order", q.sort_order ?? "desc");
  }
  if (q.metrics.length) params.set("metrics", q.metrics.join(","));
  return `${BASE}/inventory/matrix/export?${params.toString()}`;
}

/**
 * 拉取库存 KPI 聚合（供顶部卡片）。
 *
 * @param {string} [shop] - 店铺过滤；留空 = 全部店铺。
 * @param {string} [date] - 快照日期；留空 = 全部日期。
 * @returns {Promise<InventorySummary>} KPI 聚合。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const s = await fetchInventorySummary("家配", "2026-10-18");
 * s.stock; // 现货库存总量
 * ```
 */
export function fetchInventorySummary(shop?: string, date?: string): Promise<InventorySummary> {
  const params = new URLSearchParams();
  if (shop) params.set("shop", shop);
  if (date) params.set("date", date);
  return get<InventorySummary>(`/inventory/summary?${params.toString()}`);
}

/**
 * 拉取全城市滞销聚合（不限于 8 城）：城市 → 滞销件数，降序。
 *
 * @param {string} [shop] - 店铺过滤；留空 = 全部店铺。
 * @param {string} [date] - 快照日期；留空 = 全部日期。
 * @returns {Promise<InventorySlowCity[]>} 每城一项，core=true 为 8 城白名单。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const cities = await fetchInventorySlowCities("家配", "2026-10-18");
 * cities[0]; // { city: "上海", units: 1424, core: true }
 * ```
 */
export function fetchInventorySlowCities(
  shop?: string,
  date?: string,
): Promise<InventorySlowCity[]> {
  const params = new URLSearchParams();
  if (shop) params.set("shop", shop);
  if (date) params.set("date", date);
  return get<{ cities: InventorySlowCity[] }>(`/inventory/slow-cities?${params.toString()}`)
    .then((r) => r.cities);
}

/**
 * 拉取结构分布（概览卡数据源）：SKU 覆盖 / 上下柜 / 三级类目 / 销售员四维度。
 *
 * @param {InventoryRowsQuery} [q] - 与明细一致的筛选条件（shop / date / keyword）。
 * @returns {Promise<InventoryDistributions>} 四维度 + 店铺拆分。
 * @throws {Error} 接口非 2xx。
 * @example
 * ```ts
 * const d = await fetchInventoryDistributions({ shop: "家配", date: "2026-10-19" });
 * d.sku.total;            // 201
 * d.category.items[0].by_shop; // { 家配: 89, 医拜: 0, 卡求: 0 }
 * ```
 */
export function fetchInventoryDistributions(
  q: Pick<InventoryRowsQuery, "shop" | "date" | "keyword"> = {},
): Promise<InventoryDistributions> {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.date) params.set("date", q.date);
  if (q.keyword) params.set("keyword", q.keyword);
  return get<InventoryDistributions>(`/inventory/distributions?${params.toString()}`);
}

/**
 * 拉取 8 城预警汇总（每城阈值、红/黄/绿计数、建议补货合计）。
 *
 * @param {string} [shop] - 店铺过滤；留空 = 全部店铺。
 * @param {string} [date] - 快照日期；留空 = 最新快照。
 * @returns {Promise<InventoryAlertsSummary>} 8 城汇总（cities 顺序与 meta.cities 一致）。
 * @throws {Error} 接口非 2xx。
 */
export function fetchInventoryAlerts(shop?: string, date?: string): Promise<InventoryAlertsSummary> {
  const params = new URLSearchParams();
  if (shop) params.set("shop", shop);
  if (date) params.set("date", date);
  return get<InventoryAlertsSummary>(`/inventory/alerts/summary?${params.toString()}`);
}

/**
 * 批量保存城市阈值（UPSERT，持久化到 SQLite；负数按 0 = 不检测）。
 *
 * @param {Record<string, number>} thresholds - 城市码 → 阈值，只提交要改的城市即可。
 * @returns {Promise<{ ok: boolean; thresholds: Record<string, number> }>} 规范化后的全量阈值。
 * @throws {Error} 接口非 2xx（422 = 未包含任何合法城市码）。
 */
export function putAlertThresholds(
  thresholds: Record<string, number>,
): Promise<{ ok: boolean; thresholds: Record<string, number> }> {
  return put("/inventory/alerts/thresholds", { thresholds });
}

/**
 * 拉取补货建议清单（按城市分组，只含建议量 > 0 的行，下柜排除）。
 *
 * @param {object} [q] - 查询与算法参数。
 * @param {string} [q.shop] - 店铺过滤；留空 = 全部店铺。
 * @param {string} [q.date] - 快照日期；留空 = 最新快照。
 * @param {string} [q.keyword] - 关键词（SKU / 商品名称 / SPU）；留空 = 不过滤。口径与矩阵表一致。
 * @param {"city" | "sku"} [q.view] - 视图：city=按城市分组（默认）/ sku=平铺长表。两者共用同一份计算。
 * @param {RestockParams} [q.params] - 算法参数（days/safety/window）；非法值由后端夹回默认。
 * @returns {Promise<RestockListResponse>} 分组清单或平铺清单 + 参数回显 + 总计。
 * @throws {Error} 接口非 2xx。
 */
export function fetchRestockList(
  q: {
    shop?: string;
    date?: string;
    keyword?: string;
    view?: "city" | "sku";
    params?: Partial<RestockParams>;
  } = {},
): Promise<RestockListResponse> {
  const params = new URLSearchParams();
  if (q.shop) params.set("shop", q.shop);
  if (q.date) params.set("date", q.date);
  if (q.keyword) params.set("keyword", q.keyword);
  params.set("view", q.view ?? "city");
  const p = q.params ?? {};
  if (p.days !== undefined) params.set("days", String(p.days));
  if (p.safety !== undefined) params.set("safety", String(p.safety));
  if (p.window !== undefined) params.set("window", String(p.window));
  return get<RestockListResponse>(`/inventory/restock?${params.toString()}`);
}
