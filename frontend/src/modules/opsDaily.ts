// 店铺运营数据页的纯函数层：类型镜像、格式化判定、CSV 拼装、分批参数、涨跌判定。
//
// ⚠️ 本文件**不承载列定义**：指标列与公式的唯一来源是后端 ops_metric_dict，
//    由 /api/ops-daily/meta 下发。这里只镜像响应类型与枚举，新增指标 0 改码。
// 判定与渲染分离（ai-rules §5）：所有「显示什么」的判断放这里并写 node 断言，
// OpsDailyView.vue 只负责渲染；格式化函数自己写，不复用通用 formatter
// （通用 formatter 会把 null 降级成 0.00，与本页 NULL/0 分离契约冲突）。

/** 指标字典的一项（后端 OpsMetricOut 的镜像）。 */
export interface OpsMetric {
  metric_key: string;
  cn_name: string;
  source_name: string;
  kind: "atomic" | "derived";
  db_column: string;
  formula: string;
  unit: string;
  is_baseline: boolean;
  sort_order: number;
  precision: number;
}

/** /meta 响应镜像。 */
export interface OpsDailyMeta {
  metrics: OpsMetric[];
  data_from: string | null;
  data_through: string | null;
  shops: string[];
  shop_types: string[];
  /** 店铺 → 类型映射（D9）。老服务可能没有此字段 ⇒ 可选，前端须降级为扁平下拉。 */
  shop_type_by_shop?: Record<string, string>;
  total_rows: number;
}

/** 一行店铺日粒度数据；values 的键 = metric_key。 */
export interface OpsDailyRow {
  date: string;
  shop: string;
  shop_type: string;
  values: Record<string, number | null>;
}

/**
 * 一个月度汇总点（后端 `OpsMonthlyPoint` 的镜像）。
 *
 * @remarks
 * `by_type` 的键来自库内 `DISTINCT shop_type`，前端不写死 self / pop；
 * 每个月都由后端补齐全部键，缺的给 null ⇒ 双折线不会因某月缺某类型而错位。
 */
export interface OpsMonthlyPoint {
  month: string;
  sales_amount: number | null;
  visitors: number | null;
  by_type: Record<string, number | null>;
}

/** /data 响应镜像。 */
export interface OpsDailyData {
  total: number;
  page: number;
  page_size: number;
  rows: OpsDailyRow[];
  summary: Record<string, number | null>;
  /** 月度趋势点：方案 C 图表唯一数据源，不受分页影响。 */
  monthly: OpsMonthlyPoint[];
  data_through: string | null;
}

/** /data 的查询参数。 */
export interface OpsQuery {
  from: string;
  to: string;
  shop?: string;
  shop_type?: string;
  page?: number;
  page_size?: number;
}

/** 缺失占位符：真实 0 显示 0，只有 NULL / 无数据 / 分母为 0 显示它。 */
export const EMPTY = "--";
/** 每页行数：DOM 单元格数 ≈ 50 × 24，与数据总量解耦（SPEC §4.1）。 */
export const PAGE_SIZE = 50;
/** 导出每批行数。 */
export const EXPORT_BATCH = 500;
/** 导出并发上限。 */
export const EXPORT_CONCURRENCY = 4;
/** 筛选防抖时长（毫秒）。 */
export const FILTER_DEBOUNCE_MS = 250;

/**
 * 判定一个值是否为「缺失」。
 *
 * @remarks
 * 0 不是缺失：源表里 0 是真实统计到的零，NULL 才是没抓到。
 * NaN 也按缺失处理，避免表格里出现 "NaN" 字样。
 *
 * @param value - 后端返回的数值或 null / undefined。
 * @returns 是否为缺失。
 * @example
 * ```ts
 * isMissing(0);      // false
 * isMissing(null);   // true
 * isMissing(NaN);    // true
 * ```
 */
export function isMissing(value: number | null | undefined): boolean {
  return value === null || value === undefined || Number.isNaN(value);
}

/**
 * 格式化一个指标值（表格与 CSV **共用同一个函数**，保证导出与屏幕逐字一致）。
 *
 * @remarks
 * 百分比以小数存储（后端 0.075 = 7.5%），这里乘 100 并补 %；
 * 真实 0 一律显示 "0" 而不是 "0.00"，让「零」和「缺失」一眼可分。
 *
 * @param value - 后端返回的数值。
 * @param metric - 对应指标的字典项；缺失时按默认 2 位小数处理。
 * @returns 展示文本；缺失返回 "--"，且 "--" 在 CSV 里原样保留。
 * @example
 * ```ts
 * formatValue(0, { unit: "元", precision: 2 } as OpsMetric);          // "0"
 * formatValue(null, { unit: "元", precision: 2 } as OpsMetric);        // "--"
 * formatValue(0.075, { unit: "%", precision: 2 } as OpsMetric);        // "7.50%"
 * formatValue(2178.9, { unit: "元", precision: 2 } as OpsMetric);      // "2178.90"
 * ```
 */
export function formatValue(
  value: number | null | undefined,
  metric?: Pick<OpsMetric, "unit" | "precision">,
): string {
  if (isMissing(value)) return EMPTY;
  const v = value as number;
  if (v === 0) return "0";
  if (metric?.unit === "%") return `${(v * 100).toFixed(2)}%`;
  return v.toFixed(metric?.precision ?? 2);
}

/**
 * 把查询参数序列化成 URL 查询串（不编码中文以外的结构性字符，交给 URLSearchParams）。
 *
 * @param q - 查询参数。
 * @returns 形如 "from=2026-08-01&to=2026-10-31&page=1&page_size=50"。
 * @example
 * ```ts
 * queryString({ from: "2026-08-01", to: "2026-08-31" }); // 含 page=1&page_size=50
 * ```
 */
export function queryString(q: OpsQuery): string {
  const p = new URLSearchParams();
  p.set("from", q.from);
  p.set("to", q.to);
  if (q.shop) p.set("shop", q.shop);
  if (q.shop_type) p.set("shop_type", q.shop_type);
  p.set("page", String(q.page ?? 1));
  p.set("page_size", String(q.page_size ?? PAGE_SIZE));
  return p.toString();
}

/**
 * 算出导出的分批页码列表。
 *
 * @remarks
 * 为什么分批：单次 `all=true` 会把全量 JSON 一次性塞进后端内存与浏览器主线程，
 * 数据量增长后是首要爆点。分批后单次响应恒为 batchSize 行。
 *
 * @param total - 筛选后的总行数。
 * @param batchSize - 每批行数，默认 500。
 * @returns 页码数组，从 1 开始。
 * @example
 * ```ts
 * exportPages(1564); // [1, 2, 3, 4]
 * exportPages(0);    // []
 * ```
 */
export function exportPages(total: number, batchSize = EXPORT_BATCH): number[] {
  const pages: number[] = [];
  for (let p = 1; (p - 1) * batchSize < total; p++) pages.push(p);
  return pages;
}

/**
 * CSV 单元格转义：含逗号、引号、换行时用双引号包裹，内部引号翻倍。
 *
 * @param text - 已格式化的单元格文本。
 * @returns 可直接拼进 CSV 行的文本。
 * @example
 * ```ts
 * csvCell('含,逗号'); // '"含,逗号"'
 * ```
 */
export function csvCell(text: string): string {
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

/**
 * 把多批行拼成 CSV 文本（**不含 BOM**，BOM 在下载时由 Blob 加）。
 *
 * @remarks
 * 用数组 + join 而不是 `str += line`：字符串反复累加在行数上万时会退化成
 * O(n²) 拷贝。列顺序严格按 metrics 传入顺序（= 当前勾选顺序）。
 *
 * @param metrics - 当前勾选的指标字典项（顺序即列序）。
 * @param rows - 按页码顺序拼好的全量行。
 * @returns CSV 文本，首行为表头，行尾用 CRLF（Excel 兼容更好）。
 * @example
 * ```ts
 * buildOpsCsv([{ cn_name: "销售额", metric_key: "sales_amount" } as OpsMetric],
 *             [{ date: "2026-08-01", shop: "A", shop_type: "pop",
 *                values: { sales_amount: null } }]); // 数据行为 -- 原样保留
 * ```
 */
export function buildOpsCsv(metrics: OpsMetric[], rows: OpsDailyRow[]): string {
  const lines: string[] = [];
  lines.push(
    ["日期", "店铺", "类型", ...metrics.map((m) => m.cn_name)]
      .map(csvCell)
      .join(","),
  );
  const byKey = new Map(metrics.map((m) => [m.metric_key, m]));
  for (const row of rows) {
    const cells = [row.date, row.shop, row.shop_type];
    for (const m of metrics) {
      cells.push(formatValue(row.values[m.metric_key], byKey.get(m.metric_key)));
    }
    lines.push(cells.map(csvCell).join(","));
  }
  return lines.join("\r\n");
}

/**
 * 默认查询区间：**整月自然段**（D13）。
 *
 * @remarks
 * 用户口径「日期默认最近的一个月」⇒ 给的是**自然月**（1 号 ~ 月末），
 * ⛔ 不是滚动 N 天（D13 之前是滚动 30 天，那给的是 `2026-10-02~10-31`）。
 *
 * 「11 月没数据就往前推一个月」由 `data_through` 天然满足：
 * `data_through` 是库内 `max(date)`，它所在的月份定义上就是**最后一个有数据的月份**
 * ⇒ ⛔ 不需要逐月回溯，⛔ 后端不必新增 `months` 字段。
 *
 * `to` 取 `min(月末, data_through)`：当月数据未满时收在数据截至日，
 * 既不越界到未来（EP 的 `disabledDate` 只拦用户点选、不钳制程序赋值），
 * 也避免出现「未来日期的空行」。
 *
 * ⚠️ **不保证「本期 / 上期」等长**：整月默认下 10 月（31 天）的上期是 9 月（30 天），
 * 天数天然不等——这正是「跟月份走」的含义（比自然月），SPEC §17.3 已登记为口径变更。
 * ⛔ 别把它当回归。
 *
 * @param through - 库内最大日期 "YYYY-MM-DD"。
 * @returns 整月自然段的起止日期。
 * @example
 * ```ts
 * defaultMonthRange("2026-10-31"); // { from: "2026-10-01", to: "2026-10-31" }
 * defaultMonthRange("2026-11-05"); // { from: "2026-11-01", to: "2026-11-05" }
 * ```
 */
export function defaultMonthRange(through: string): { from: string; to: string } {
  const y = Number(through.slice(0, 4));
  const m = Number(through.slice(5, 7)); // 1-12
  const monthEnd = `${through.slice(0, 7)}-${String(lastDayOfMonth(y, m)).padStart(2, "0")}`;
  return { from: `${through.slice(0, 7)}-01`, to: monthEnd < through ? monthEnd : through };
}

/**
 * 某年某月的最后一天（UTC 口径，避免本地时区与夏令时偏移）。
 *
 * @param year - 完整年份，如 2026。
 * @param month - 月份，1-12。
 * @returns 该月天数（28~31）。
 */
function lastDayOfMonth(year: number, month: number): number {
  // Date.UTC 的 month 是 0-based ⇒ 传 month 本身即「下个月 0 号」，正是本月最后一天。
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * 日期串是否为本月的**最后一天**。
 *
 * @remarks
 * D13 的上期平移要区分两种语义：
 * - `to` 是月末 ⇒ 它表达「这个月的结束」，平移后应落在**目标月的月末**；
 * - `to` 不是月末 ⇒ 它表达「第几天」，平移后按日号平移并钳制。
 * ⛔ 混为一谈会让 9 月整月的上期变成 `08-30`（少一天）——静默错一截。
 *
 * @param date - 日期 "YYYY-MM-DD"。
 * @returns true = 该日是所在月的最后一天。
 */
function isMonthEnd(date: string): boolean {
  const y = Number(date.slice(0, 4));
  const m = Number(date.slice(5, 7));
  return Number(date.slice(8, 10)) === lastDayOfMonth(y, m);
}

/**
 * 把日期串平移若干个自然月，日号**钳制**到目标月的最后一天（D13）。
 *
 * @remarks
 * ⚠️ 钳制而非让 `Date` 自动进位：`10-31` 平移到 9 月（30 天）必须得到 `09-30`，
 * 若放任 `Date` 进位会变成 `10-01` —— 那是「下个月」，静默错一个月。
 * 闰年 2 月同理：`03-31` → `02-29`（2028）/ `02-28`（平年）。
 *
 * @param date - 原日期 "YYYY-MM-DD"。
 * @param months - 平移月数，负数表示往前。
 * @param monthEnd - true = 按「月末」语义平移（落目标月的最后一天），false = 按日号平移。
 * @returns 平移并钳制后的日期。
 */
function shiftMonths(date: string, months: number, monthEnd = false): string {
  const y = Number(date.slice(0, 4));
  const m = Number(date.slice(5, 7)); // 1-12
  const d = Number(date.slice(8, 10));
  // 用「绝对月序」做加减，跨年（1 月 -1 月）不用特判。
  const total = y * 12 + (m - 1) + months;
  const ny = Math.floor(total / 12);
  const nm = (total - ny * 12) + 1; // 回到 1-12
  const nd = monthEnd ? lastDayOfMonth(ny, nm) : Math.min(d, lastDayOfMonth(ny, nm));
  return `${String(ny).padStart(4, "0")}-${String(nm).padStart(2, "0")}-${String(nd).padStart(2, "0")}`;
}

/**
 * 算出「上期」区间：**跟月份走**（D13）。
 *
 * @remarks
 * 用户口径「上期改整月（跟月份走，不固定 30 天）」⇒ 上期是本期的**自然月平移**，
 * ⛔ 不再是旧口径的「等长 N 天」。
 *
 * 为什么不是「取上一个自然月」：本期可能跨多个月（用户手选区间），
 * 此时上期必须整体前移**所跨的月数**，⛔ 否则上期会与本期重叠、环比失真。
 * 单月区间（首屏默认）两种算法一致：10/01~10/31 ⇒ 09/01~09/30。
 *
 * ⚠️ **两端必须用同一套语义**：`from` 与 `to` 各自按「是月末就按月末平移、
 * 否则按日号平移并钳制」处理（见 `isMonthEnd`）。
 * ⛔ 若只给 `to` 用月末语义、`from` 恒按日号，会出现
 * `11-30~11-30`（1 天）的上期变成 `10-30~10-31`（2 天）——上期比本期还长，
 * 环比被污染。实测手选「月末起」的区间必踩。
 *
 * @param from - 当前区间起点 "YYYY-MM-DD"。
 * @param to - 当前区间终点 "YYYY-MM-DD"。
 * @returns 上期起止日期。
 * @example
 * ```ts
 * previousMonthRange("2026-10-01", "2026-10-31"); // { from: "2026-09-01", to: "2026-09-30" }
 * previousMonthRange("2026-03-31", "2026-03-31"); // { from: "2026-02-28", to: "2026-02-28" }
 * previousMonthRange("2026-11-30", "2026-11-30"); // { from: "2026-10-31", to: "2026-10-31" }
 * ```
 */
export function previousMonthRange(from: string, to: string): { from: string; to: string } {
  const span =
    (Number(to.slice(0, 4)) * 12 + Number(to.slice(5, 7))) -
    (Number(from.slice(0, 4)) * 12 + Number(from.slice(5, 7))) + 1;
  return {
    from: shiftMonths(from, -span, isMonthEnd(from)),
    to: shiftMonths(to, -span, isMonthEnd(to)),
  };
}

/**
 * 默认店铺（D13）：首屏预选的店铺名。
 *
 * @remarks
 * ⚠️ 店铺名是**业务事实**、不在 `ops_metric_dict` 里 ⇒ 走前端配置常量，
 * ⛔ 不进字典表（字典只管指标列，掺入店铺会让「字典驱动列」语义变浑）。
 *
 * ⛔ 落地时**必须**配合 `resolveDefaultShop()` 做存在性校验：
 * 店铺改名 / 库未导入该店时降级为「全部店铺」，否则会静默筛空、页面看着正常却是 0 行。
 */
export const DEFAULT_SHOP = "钻芯旗舰店";

/**
 * 校验默认店铺是否存在，不存在则降级为「全部店铺」（D13）。
 *
 * @remarks
 * 「全部店铺」在本页的约定值是**空串**（`/data` 的 `shop` 参数为空即不过滤）。
 *
 * @param shops - `/meta.shops`（库内 `DISTINCT shop`）；`meta` 未就绪时为 undefined。
 * @returns 默认店铺名，或空串（全部店铺）。
 * @example
 * ```ts
 * resolveDefaultShop(["钻芯旗舰店", "京喜自营"]); // "钻芯旗舰店"
 * resolveDefaultShop(["京喜自营"]);               // ""
 * ```
 */
export function resolveDefaultShop(shops?: readonly string[] | null): string {
  if (!shops || !shops.length) return "";
  return shops.includes(DEFAULT_SHOP) ? DEFAULT_SHOP : "";
}

/** 涨跌方向：up=涨（绿）、down=跌（红）、flat=持平或无法比较。 */
export type TrendClass = "up" | "down" | "flat";

/**
 * 判定涨跌方向（D3 绿涨红跌：up 对应 --color-up，down 对应 --color-down）。
 *
 * @remarks
 * 上一期为 0 或缺失时不做除法、返回 flat——否则会出现无穷大百分比，
 * 或把「上期没数据」误标成暴涨。变化小于 0.05% 也按持平，避免浮点噪声染色。
 *
 * @param current - 本期值。
 * @param previous - 上期值。
 * @returns 涨跌方向。
 * @example
 * ```ts
 * trendClass(120, 100);   // "up"
 * trendClass(80, 100);    // "down"
 * trendClass(100, null);  // "flat"
 * ```
 */
export function trendClass(
  current: number | null | undefined,
  previous: number | null | undefined,
): TrendClass {
  if (isMissing(current) || isMissing(previous)) return "flat";
  const cur = current as number;
  const prev = previous as number;
  if (prev === 0) return "flat";
  const ratio = (cur - prev) / Math.abs(prev);
  if (Math.abs(ratio) < 0.0005) return "flat";
  return ratio > 0 ? "up" : "down";
}

/**
 * 环比变化文案（带正负号与百分号）；无法比较时返回空串。
 *
 * @param current - 本期值。
 * @param previous - 上期值。
 * @returns 形如 "+12.34%" / "-8.00%"；无法比较为 ""。
 * @example
 * ```ts
 * formatTrend(120, 100); // "+20.00%"
 * ```
 */
export function formatTrend(
  current: number | null | undefined,
  previous: number | null | undefined,
): string {
  if (isMissing(current) || isMissing(previous)) return "";
  const prev = previous as number;
  if (prev === 0) return "";
  const ratio = ((current as number) - prev) / Math.abs(prev);
  const sign = ratio > 0 ? "+" : "";
  return `${sign}${(ratio * 100).toFixed(2)}%`;
}

/** KPI 卡定义：4 张卡，后两项是派生指标。 */
export const KPI_CARDS = [
  { key: "sales_amount", label: "销售额", derived: false },
  { key: "visitors", label: "访客数", derived: false },
  { key: "conversion_rate", label: "转化率", derived: true },
  { key: "roi", label: "ROI", derived: true },
] as const;

/**
 * 生成默认勾选列：字典全选（2026-11-08 D7）。
 *
 * @remarks
 * 用户要求「指标勾选默认全选」，首屏直接给全 24 列。性能契约 §4.1 本就按
 * 50 行 × 24 列预算，故不新增 DOM 开销；横向滚动与 sticky 前两列由表格承担。
 * 返回顺序 = 字典顺序（DB sort_order），**实际展示顺序由
 * `orderMetricsForDisplay()` 决定**，勾选集合只回答「有哪些列」。
 * ⛔ 用户手动取消到 0 列时由页面独立空态提示，不在此处兜底重新全选。
 *
 * @param metrics - /meta 下发的字典。
 * @returns 全部 metric_key，顺序与字典一致。
 * @example
 * ```ts
 * defaultSelectedKeys([{ metric_key: "a" } as OpsMetric]); // ["a"]
 * defaultSelectedKeys([]); // []
 * ```
 */
export function defaultSelectedKeys(metrics: OpsMetric[]): string[] {
  return metrics.map((m) => m.metric_key);
}

/**
 * 解析派生指标的 `分子key/分母key` 公式。
 *
 * @remarks
 * 表头中文公式与展示列序都要读同一个结构，抽出来避免两处各自 split
 * 却对「非法公式」给出不同的回退行为（判定与渲染分离，ai-rules §3）。
 * 空串、缺分子或分母、多段一律返回 null，由调用方决定如何回退。
 *
 * @param formula - 字典下发的公式字符串；原子指标为空串。
 * @returns [分子 key, 分母 key]；无法解析时返回 null。
 * @example
 * ```ts
 * parseFormula("buyers/visitors"); // ["buyers", "visitors"]
 * parseFormula(""); // null
 * ```
 */
function parseFormula(formula: string): [string, string] | null {
  if (!formula) return null;
  const parts = formula.split("/");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return [parts[0], parts[1]];
}

/**
 * 表头悬停文案：把字典里的 key 表达式翻译成中文口径（D7）。
 *
 * @remarks
 * 旧写法 `:title="m.formula || m.cn_name"` 会让 5 个派生列弹出
 * `buyers/visitors` 这类代码变量名。这里用公式的 key 反查字典
 * `cn_name` 拼出「转化率 = 成交人数 ÷ 访客数」——中文全部来自字典，
 * 前端硬编码公式数仍为 0（SPEC F-08），改字典即改文案。
 * ⚠️ 分子或分母查不到中文名、公式不是 `a/b` 时，只回中文名 + 单位：
 * 宁可少给信息，也绝不把 key 漏给用户。
 *
 * @param m - 当前列的字典项。
 * @param dict - /meta 下发的完整字典，用于 key → 中文名反查。
 * @returns 可直接放进 `title` 的中文文案。
 * @example
 * ```ts
 * metricTitle(derived, meta.metrics); // "转化率 = 成交人数 ÷ 访客数"
 * metricTitle(atomic, meta.metrics);  // "访客数（人）"
 * ```
 */
export function metricTitle(m: OpsMetric, dict: OpsMetric[]): string {
  const unit = m.unit ? `（${m.unit}）` : "";
  const fallback = `${m.cn_name}${unit}`;
  if (m.kind !== "derived") return fallback;
  const parts = parseFormula(m.formula);
  if (!parts) return fallback;
  const byKey = new Map(dict.map((x) => [x.metric_key, x.cn_name]));
  const num = byKey.get(parts[0]);
  const den = byKey.get(parts[1]);
  if (!num || !den) return fallback;
  return `${m.cn_name} = ${num} ÷ ${den}`;
}

/**
 * 展示列序：派生指标紧跟其公式的分母列（D7「分母锚点」）。
 *
 * @remarks
 * 字典 sort_order 把 5 个派生排在最后（20~24），读表时「访客数 / 成交人数」
 * 与由它们算出的「转化率」隔了十几列。这里按 formula 的分母把派生插回源头
 * 指标中间：转化率落在访客数与成交人数之间、客单价落在成交人数之后，
 * 依此类推。位置从字典现推，⛔ 不硬编码任何 key 或序号，新增指标 0 改码。
 *
 * ⚠️ 这是**视图投影**：DB `ops_metric_dict.sort_order` 仍是存储序的唯一
 * 事实来源，本函数只决定页面与导出 CSV 的排列。若未来出现第 2 个列序
 * 消费者，应把重排下沉到 `/meta` 下发，避免两处各排一次。
 * 分母不在字典、或公式无法解析的派生列，按字典相对顺序追加末尾，
 * 保证不丢列、不重复。
 *
 * @param metrics - /meta 下发的字典（已按 sort_order 升序）。
 * @returns 新的数组，顺序为「原子列 + 紧跟其后的派生列」；不修改入参。
 * @example
 * ```ts
 * orderMetricsForDisplay(meta.metrics).length; // 24（与入参等长）
 * ```
 */
export function orderMetricsForDisplay(metrics: OpsMetric[]): OpsMetric[] {
  const byKey = new Map(metrics.map((m) => [m.metric_key, m]));
  const derived = metrics.filter((m) => m.kind === "derived");
  const inserted = new Set<string>();
  const out: OpsMetric[] = [];
  for (const m of metrics) {
    if (m.kind === "derived") continue;
    out.push(m);
    for (const d of derived) {
      if (inserted.has(d.metric_key)) continue;
      const parts = parseFormula(d.formula);
      if (parts && parts[1] === m.metric_key && byKey.has(parts[1])) {
        out.push(d);
        inserted.add(d.metric_key);
      }
    }
  }
  for (const d of derived) if (!inserted.has(d.metric_key)) out.push(d);
  return out;
}

// ====================== 指标业务域分组表头（2026-11-09 拍板：闭合框线） ======================

/** 一组的视图层配置；`keys` 的顺序即组内列序。 */
export interface OpsMetricGroup {
  /** 稳定键，供 v-for 的 :key 使用，不参与展示。 */
  key: string;
  /** 表头第一行显示的组名。 */
  title: string;
  /** 组内指标的 metric_key；未列入任何组的指标自动进「未分类」。 */
  keys: readonly string[];
}

/** 一个可渲染单元格：指标 + 组边界标记，模板零判定（ai-rules §3 判定与渲染分离）。 */
export interface OpsMetricCell {
  metric: OpsMetric;
  /** 该组首列 ⇒ 加左框线。 */
  groupStart: boolean;
  /** 该组末列 ⇒ 加右框线。 */
  groupEnd: boolean;
}

/** 一组的可渲染投影；`span` 直接供 thead 的 `colspan` 使用。 */
export interface OpsMetricGroupView {
  key: string;
  title: string;
  span: number;
  cells: OpsMetricCell[];
}

/**
 * 六组业务域划分（**视图层配置**，⛔ 不是 DB `sort_order` 的替代）。
 *
 * @remarks
 * 组内顺序按拍板时用户列出的顺序，例如京东联盟组是「佣金 → 佣金比例 → 成交金额」，
 * 与「分母锚点」投影把派生列挂在分母之后略有不同；`orderMetricsForDisplay()`
 * 仍作为**成员来源**复用，保证派生列不丢、字典新增项不漏。
 * 组名「售后服务类」刻意带「类」字，避开与动态评分指标「售后服务」的术语重载。
 * ⚠️ 这里按 key 列举是短期方案；`groupMetricsForDisplay()` 已把 groups 做成入参，
 * 未来由 `/meta` 下发分组时直接替换实参即可，视图 0 改码。
 */
export const METRIC_GROUPS: readonly OpsMetricGroup[] = [
  {
    key: "core",
    title: "店铺核心数据",
    keys: ["sales_amount", "visitors", "conversion_rate", "buyers", "avg_price", "search_traffic"],
  },
  {
    key: "aftersale",
    title: "售后服务类",
    keys: ["compensation_amount", "refund_amount", "refund_duration"],
  },
  {
    key: "supplement",
    title: "补单类",
    keys: ["supplement_principal", "supplement_commission", "supplement_orders"],
  },
  {
    key: "jzt",
    title: "京准通推广类",
    keys: ["promo_clicks", "ppc", "promo_order_amount", "promo_cost", "roi"],
  },
  {
    key: "union",
    title: "京东联盟类",
    keys: ["jd_union_commission", "jd_union_commission_rate", "jd_union_order_amount"],
  },
  {
    key: "score",
    title: "动态评分类",
    keys: ["service_consult_score", "logistics_score", "aftersale_score", "product_experience_score"],
  },
];

/** 未列入配置的指标的兜底组键。 */
const UNCLASSIFIED_GROUP_KEY = "__unclassified__";

/**
 * 把指标字典投影成「逐组 → 逐列」的可渲染结构。
 *
 * @remarks
 * 先复用 `orderMetricsForDisplay()` 拿到全量列（派生列由 formula 定位，不丢不重），
 * 再按配置把每组的 key 取出来排成列序；**空组不输出**，避免 thead 出现 `colspan=0`。
 * 未被任何组认领的指标统一追加到「未分类」组 ⇒ 字典新增指标前端 0 改码。
 * 组首/组尾标记在这里算好，视图只按标记加 class，⛔ 不用 `:nth-child`
 * （勾选改变列数时按位置选列必然错位，ai-rules §5）。单列组的首列会同时带
 * `groupStart` 与 `groupEnd`，这样闭合框四面都有线。
 *
 * @param metrics - /meta 下发的字典（可只含当前勾选的列）。
 * @param groups - 分组配置；默认 `METRIC_GROUPS`，预留给未来后端字典下发。
 * @returns 非空组的投影数组；空输入返回空数组。
 * @example
 * ```ts
 * const g = groupMetricsForDisplay(meta.metrics);
 * g[0].span;                 // 6（店铺核心数据 6 列）
 * g[0].cells[0].groupStart; // true
 * ```
 */
export function groupMetricsForDisplay(
  metrics: OpsMetric[],
  groups: readonly OpsMetricGroup[] = METRIC_GROUPS,
): OpsMetricGroupView[] {
  const ordered = orderMetricsForDisplay(metrics);
  const byKey = new Map(ordered.map((m) => [m.metric_key, m]));
  const used = new Set<string>();
  const out: OpsMetricGroupView[] = [];

  const push = (g: OpsMetricGroup, items: OpsMetric[]): void => {
    if (!items.length) return;
    for (const m of items) used.add(m.metric_key);
    const last = items.length - 1;
    out.push({
      key: g.key,
      title: g.title,
      span: items.length,
      cells: items.map((metric, i) => ({
        metric,
        groupStart: i === 0,
        groupEnd: i === last,
      })),
    });
  };

  for (const g of groups) {
    const seen = new Set<string>();
    const items: OpsMetric[] = [];
    for (const k of g.keys) {
      // 配置里重复写同一个 key、或跨组重复时只保留首次出现的那一列。
      if (seen.has(k) || used.has(k)) continue;
      seen.add(k);
      const m = byKey.get(k);
      if (m) items.push(m);
    }
    push(g, items);
  }

  const rest = ordered.filter((m) => !used.has(m.metric_key));
  if (rest.length) {
    push(
      {
        key: UNCLASSIFIED_GROUP_KEY,
        title: "未分类",
        keys: rest.map((m) => m.metric_key),
      },
      rest,
    );
  }
  return out;
}

/**
 * 把分组投影摊平回指标数组。
 *
 * @remarks
 * 表头、`tbody` 数据行与 `buildOpsCsv()` 导出**必须共用这一份列序**，
 * 否则组名行的 `colspan` 会与数据列错位（SPEC F-05 的分组版）。
 *
 * @param groups - `groupMetricsForDisplay()` 的结果。
 * @returns 按组顺序展开的指标数组，无重复。
 */
export function flattenGroupedMetrics(groups: OpsMetricGroupView[]): OpsMetric[] {
  return groups.flatMap((g) => g.cells.map((c) => c.metric));
}

// ====================== 方案 C：月度趋势与「月份之间」的环比标注 ======================

/** 月份间环比徽章的展示契约（判定与渲染分离，判定全在本文件）。 */
export interface MonthMom {
  /** "+5.92%" / "-8.37%"；无法比较时为 "--" */
  text: string;
  /** up=绿涨 / down=红跌 / flat=中性（D3） */
  cls: TrendClass;
}

/**
 * 计算相邻两个月的环比文案与涨跌方向。
 *
 * @remarks
 * 复用 `formatTrend` / `trendClass`，与 KPI 卡环比**同一套口径**：上期为 0 或缺失
 * ⇒ 不做除法，显示 `--` 且不染色；变化 < 0.05% 按持平。这样图表徽章与 KPI 卡
 * 不会出现「同一段数据算出两个百分比」。
 *
 * @param current - 本月值。
 * @param previous - 上月值。
 * @returns 徽章文案与涨跌 class。
 * @example
 * ```ts
 * monthMom(1784381.57, 1684635.96); // { text: "+5.92%", cls: "up" }
 * monthMom(887928.02, 969031.55);   // { text: "-8.37%", cls: "down" }
 * monthMom(100, null);              // { text: "--", cls: "flat" }
 * ```
 */
export function monthMom(
  current: number | null | undefined,
  previous: number | null | undefined,
): MonthMom {
  const text = formatTrend(current, previous);
  return { text: text || EMPTY, cls: trendClass(current, previous) };
}

/**
 * 环比徽章的涨跌符号（方案 1 纯文字徽章专用）。
 *
 * @remarks
 * 删掉白底描边胶囊后，颜色成了唯一线索；补一个 ▲/▼ 让涨跌不依赖颜色也能读，
 * 与「实线/虚线 + 实心/空心点」是同一套冗余编码思路（WCAG 1.4.1）。
 * flat 用破折号，与无法比较时的 `--` 语义一致，不染色。
 *
 * @param cls - 涨跌方向。
 * @returns "▲" / "▼" / "—"。
 * @example
 * ```ts
 * momSymbol("up");   // "▲"
 * momSymbol("flat"); // "—"
 * ```
 */
export function momSymbol(cls: TrendClass): string {
  if (cls === "up") return "▲";
  if (cls === "down") return "▼";
  return "—";
}

/**
 * 把「元」格式化成图表用的「万」；缺失返回 `--`。
 *
 * @remarks
 * 数值标签用默认 2 位小数；Y 轴刻度用 1 位（`digits=1`）并去掉「万」后缀，
 * 单位改由图表左上角的轴标题统一标注——这样刻度变短，不会再向左越出画布。
 *
 * @param value - 元为单位的金额。
 * @param digits - 小数位数，默认 2。
 * @returns 形如 "168.46" / "198.1"；缺失为 "--"。
 * @example
 * ```ts
 * formatWan(1684635.96);      // "168.46"
 * formatWan(1980905.76, 1);   // "198.1"
 * formatWan(null);            // "--"
 * ```
 */
export function formatWan(value: number | null | undefined, digits = 2): string {
  if (isMissing(value)) return EMPTY;
  return ((value as number) / 10_000).toFixed(digits);
}

/**
 * 图表画布几何常量（与静态预览页同一套坐标，方便对照验收）。
 *
 * @remarks
 * `left` 从 50 提到 82：Y 轴刻度右对齐在 `left - 8`，旧值 42 放不下
 * 「198.09 万」，会向左越出 viewBox 被裁成「8.09 万」。
 * `badgeDy` 正值放线下方、负值放线上方；2026-11-06 用户要求环比徽章统一
 * 移到折线上方 ⇒ 改为 -13。徽章在两月之间的线段中点，数值标签在月份点上，
 * X 方向天然错开，同侧也不会重叠。
 * 方案 1 后徽章是纯文字、不再绘制 rect ⇒ 删除 badgeWidth / badgeHeight。
 */
export const MONTHLY_CHART = {
  width: 640,
  height: 220,
  left: 82,
  right: 610,
  top: 30,
  bottom: 170,
  /** 徽章相对线段中点的垂直偏移（负值 = 线上方）。 */
  badgeDy: -13,
} as const;

/** 折线顺序（只影响配色与图例先后，不限制类型集合；新类型自动追加）。 */
const TYPE_ORDER = ["self", "pop"];
const TYPE_LABEL: Record<string, string> = { self: "自营", pop: "POP" };
// 只用页面局部已有的两个蓝令牌，⛔ 不新增 hex / 不改 :root：
// 自营用最深的 --color-brand-darker，POP 用 --color-brand，深浅一眼可分。
const TYPE_COLOR: Record<string, string> = {
  self: "var(--color-brand-darker)",
  pop: "var(--color-brand)",
};

/**
 * 线型区分（方案 A）：两条线不再只靠蓝色深浅分辨，改用「实线 + 实心点 /
 * 虚线 + 空心点」。不依赖颜色 ⇒ 色弱、黑白打印、缩略图下都能读。
 *
 * @remarks
 * 只约定已知类型的样式；新增的第 3 类及以上走 FALLBACK_DASHES 自动追加，
 * 保持「类型集合取自数据、前端 0 改码」的既有契约。
 */
const TYPE_LINE_STYLE: Record<string, { dash: string; hollow: boolean }> = {
  self: { dash: "", hollow: false },
  pop: { dash: "6 4", hollow: true },
};
// 虚线段长随折线 stroke-width 一起收细（3 → 2），否则细线上挂粗虚线会显脏。
const FALLBACK_DASHES = ["2 3", "8 3 2 3", "10 5"];

export interface MonthlyChartPoint {
  x: number;
  y: number;
  value: number | null;
  label: string;
}

export interface MonthlyMomBadge {
  x: number;
  y: number;
  text: string;
  cls: TrendClass;
  /** 涨跌符号 ▲ / ▼ / —：与颜色互为冗余编码，方案 1 纯文字徽章靠它表意。 */
  symbol: string;
  /** 无障碍读数，如「自营 2026-09 环比 +5.92%」。 */
  title: string;
}

export interface MonthlyChartLine {
  key: string;
  label: string;
  color: string;
  /** SVG stroke-dasharray；空串 = 实线。 */
  dash: string;
  /** true = 空心点（画布色填充 + 本色描边），false = 实心点。 */
  hollow: boolean;
  points: MonthlyChartPoint[];
  /** 非 null 点连成的 SVG path；缺失月份会断开，不画假线。 */
  path: string;
  badges: MonthlyMomBadge[];
}

export interface MonthlyChart {
  months: string[];
  lines: MonthlyChartLine[];
  ticks: Array<{ y: number; label: string }>;
  /** 合计（不分类型）的月环比，供右侧 mini 卡用。 */
  totalMoms: Array<{ month: string; mom: MonthMom }>;
}

/** 月份在 X 轴上的位置：n 个点均分 left~right；只有 1 个月时居中。 */
function xAt(index: number, count: number): number {
  const { left, right } = MONTHLY_CHART;
  if (count <= 1) return (left + right) / 2;
  return left + ((right - left) * index) / (count - 1);
}

/**
 * 由后端月度点构造 SVG 几何（折线、点、环比徽章、Y 轴刻度）。
 *
 * @remarks
 * 为什么把坐标算成纯函数：SVG 里最容易出错的是「比值算错却看不出来」，
 * 放到这里就能用 node 断言锁死 X 均分、徽章个数与文案，Vue 只负责渲染。
 * Y 轴是**非零起点的放大区间**（所有分类型值的 min/max 再各留 ≥12% 边距），
 * 必须在副标题标注，否则 1% 的波动会被画成暴涨。
 * 线型（实/虚、实心/空心点）与涨跌符号也在这里判定，Vue 只画出来；
 * 徽章统一放折线上方（badgeDy < 0），与月份点上的数值标签在 X 上错开。
 *
 * @param monthly - /data 下发的月度点（升序）。
 * @returns 画布所需的全部几何数据；空数组时返回空 lines / ticks。
 * @example
 * ```ts
 * const c = buildMonthlyChart(monthly);
 * c.lines.length;                 // 2（自营 / POP）
 * c.lines[0].badges[0].text;     // "+5.92%"
 * ```
 */
export function buildMonthlyChart(monthly: OpsMonthlyPoint[]): MonthlyChart {
  const months = monthly.map((m) => m.month);
  const n = months.length;
  const { top, bottom, badgeDy } = MONTHLY_CHART;
  if (!n) return { months, lines: [], ticks: [], totalMoms: [] };

  // 类型集合取自数据，不写死 self/pop；TYPE_ORDER 只决定谁排前面（深蓝在上更好读）。
  const keys = new Set<string>();
  for (const m of monthly) Object.keys(m.by_type ?? {}).forEach((k) => keys.add(k));
  const ordered = [
    ...TYPE_ORDER.filter((k) => keys.has(k)),
    ...[...keys].filter((k) => !TYPE_ORDER.includes(k)).sort(),
  ];

  const values = monthly.flatMap((m) =>
    ordered
      .map((k) => m.by_type?.[k])
      .filter((v): v is number => !isMissing(v)),
  );
  const rawMin = values.length ? Math.min(...values) : 0;
  const rawMax = values.length ? Math.max(...values) : 1;
  const pad = Math.max((rawMax - rawMin) * 0.12, rawMax * 0.02, 1);
  const min = rawMin - pad;
  const max = rawMax + pad;
  const yAt = (v: number): number =>
    max === min ? (top + bottom) / 2 : bottom - ((v - min) / (max - min)) * (bottom - top);

  const lines: MonthlyChartLine[] = ordered.map((key, typeIndex) => {
    // 已知类型走固定线型；新增类型按顺序取点线 / 点划线，仍不写死类型枚举。
    const style = TYPE_LINE_STYLE[key] ?? {
      dash: FALLBACK_DASHES[
        Math.max(0, typeIndex - TYPE_ORDER.length) % FALLBACK_DASHES.length
      ],
      hollow: true,
    };
    const points: MonthlyChartPoint[] = monthly.map((m, i) => {
      const value = m.by_type?.[key] ?? null;
      return {
        x: xAt(i, n),
        y: isMissing(value) ? (top + bottom) / 2 : yAt(value as number),
        value,
        label: formatWan(value),
      };
    });
    // 只连非 null 点：中间缺一个月时线断开，不用 0 伪装缺失。
    let open = false;
    const path = points
      .map((p) => {
        if (isMissing(p.value)) {
          open = false;
          return "";
        }
        const cmd = `${open ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`;
        open = true;
        return cmd;
      })
      .filter(Boolean)
      .join(" ");

    const badges: MonthlyMomBadge[] = [];
    for (let i = 1; i < n; i++) {
      const prev = points[i - 1];
      const cur = points[i];
      const mom = monthMom(cur.value, prev.value);
      badges.push({
        x: (prev.x + cur.x) / 2,
        y: (prev.y + cur.y) / 2 + badgeDy,
        text: mom.text,
        cls: mom.cls,
        symbol: momSymbol(mom.cls),
        title: `${TYPE_LABEL[key] ?? key} ${months[i]} 环比 ${mom.text}`,
      });
    }
    return {
      key,
      label: TYPE_LABEL[key] ?? key,
      color: TYPE_COLOR[key] ?? "var(--color-brand)",
      dash: style.dash,
      hollow: style.hollow,
      points,
      path,
      badges,
    };
  });

  // 1 位小数、不带「万」：单位由图表左上角轴标题统一标注，刻度才不会顶出画布。
  const ticks = [
    { y: top, label: formatWan(max, 1) },
    { y: (top + bottom) / 2, label: formatWan((max + min) / 2, 1) },
    { y: bottom, label: formatWan(min, 1) },
  ];
  const totalMoms = monthly.slice(1).map((m, i) => ({
    month: m.month,
    mom: monthMom(m.sales_amount, monthly[i].sales_amount),
  }));
  return { months, lines, ticks, totalMoms };
}

/**
 * 区间首尾对比（右侧 mini 卡用：自营 8 月 → 10 月 涨了多少）。
 *
 * @param values - 按时间升序的一组值。
 * @returns 首尾环比；不足 2 个点返回 `--` / flat。
 */
export function spanMom(values: Array<number | null | undefined>): MonthMom {
  if (values.length < 2) return { text: EMPTY, cls: "flat" };
  return monthMom(values[values.length - 1], values[0]);
}

/** `classifyOpsError()` 的结果：页面状态 + 可直接显示的错误文案。 */
export interface OpsErrorVerdict {
  state: "denied" | "error";
  message: string;
}

/**
 * 把接口异常映射成页面状态（判定与渲染分离，ai-rules §3 / 手册 §1.3）。
 *
 * @remarks
 * 401 / 403 单独走「无权限」态，避免把权限问题显示成「网络错误」误导用户重试。
 * 放在纯函数层是为了能写 node 断言：视图只负责把结果赋给 `state` / `errorText`。
 * 只匹配「状态码 + 明确的权限词」，不把任意含 40x 的业务文案误判成无权限。
 *
 * @param e - catch 到的任意值（Error / 字符串 / 网络异常的 DOMException）。
 * @returns 状态判定与原始错误文案；`denied` 时 message 仍填原文，便于排查。
 * @example
 * ```ts
 * classifyOpsError(new Error("401 Unauthorized")).state; // "denied"
 * classifyOpsError(new Error("500 网关超时")).state;     // "error"
 * ```
 */
export function classifyOpsError(e: unknown): OpsErrorVerdict {
  const message = e instanceof Error ? e.message : String(e);
  const denied = /\b(401|403)\b|无权限|Forbidden|Unauthorized/i.test(message);
  return { state: denied ? "denied" : "error", message };
}

/**
 * 店铺类型的显示名。
 *
 * @remarks
 * 类型集合本身来自 `/meta.shop_types`（库内 `DISTINCT shop_type`），⛔ 本函数
 * 只做**已知类型的中文标注**，不参与筛选、不决定是否画线；遇到新增的第 3 类
 * 原样返回其 key，避免像旧写法 `x === "pop" ? "POP" : "自营"` 那样把新类型
 * 错标成「自营」。
 *
 * @param t - 库内的 shop_type 原始值。
 * @returns 下拉框 / 表格里显示的文案。
 * @example
 * ```ts
 * shopTypeLabel("self");      // "自营"
 * shopTypeLabel("pop");       // "POP"
 * shopTypeLabel("new_type");  // "new_type"
 * ```
 */
export function shopTypeLabel(t: string): string {
  const labels: Record<string, string> = { self: "自营", pop: "POP" };
  return labels[t] ?? t;
}

/**
 * 「指标勾选」浮层是否应因一次页面点击而收起（判定与渲染分离，ai-rules §3）。
 *
 * @remarks
 * 只接收「浮层是否打开」与「点击是否落在浮层根内」两个布尔量，⛔ 不接收 DOM 节点：
 * 这样纯函数能在 node 断言里直接跑，视图只负责把 `root.contains(target)` 的结果传进来。
 * 两个条件缺一不可：未打开时不做无谓赋值；点在根内（按钮本身 / 勾选项）必须保持展开，
 * 否则一勾选项面板就消失。
 *
 * @param open - 浮层当前是否展开。
 * @param targetInside - 本次点击目标是否在浮层根元素内。
 * @returns `true` 表示应收起浮层。
 * @example
 * ```ts
 * shouldCloseMetricPanel(true, false); // true  → 点了页面别处，收起
 * shouldCloseMetricPanel(true, true);  // false → 点面板内部，保持
 * shouldCloseMetricPanel(false, false); // false → 本来就没开
 * ```
 */
export function shouldCloseMetricPanel(open: boolean, targetInside: boolean): boolean {
  return open && !targetInside;
}

/** 店铺下拉选项（供收敛层 `AppSelect` 消费）。 */
export interface ShopSelectOption {
  /** 选项值：店铺名；空串代表「全部店铺」 */
  value: string;
  /** 显示文案 */
  label: string;
  /** 分组键（类型原始值）；缺省表示不分组 */
  group?: string;
  /** 分组标题（含数量），由字典生成 */
  groupLabel?: string;
  /** 类型徽章文案 */
  badge?: string;
  /** 语义色调键，供 CSS 决定悬停底色（未知类型走中性 hover） */
  tone?: string;
}

/** 已知类型的展示优先序：只决定谁排前面，不决定集合（与图表 TYPE_ORDER 一致）。 */
const SHOP_TYPE_ORDER = ["self", "pop"];
/** 映射缺失或类型不在字典时归入的兜底分组。 */
const UNCLASSIFIED_GROUP = "__unclassified__";

/**
 * 由字典构建店铺下拉选项（判定与渲染分离，ai-rules §3）。
 *
 * @remarks
 * 分组与徽章**由 `/meta.shop_types` 字典驱动**：新增第 3 类时 `shopTypes`
 * 多一项即自动多一组，本函数 0 改码；中文标注复用 `shopTypeLabel`，不会
 * 像写死 `self/pop` 那样把新类型错标成「自营」。
 *
 * ⚠️ 边界：`tone` 只是字符串键，收敛层 CSS 目前只为 self/pop 定义了
 * **hover 专属底色**，第 3 类会自动成组但 hover 走中性浅灰（功能不受损）。
 * 若要给它配色，需在 `style.css` 加令牌 + `AppSelect.vue` 的 `.as-host-*`
 * 加一组变量，见 SPEC §14「已知边界②」。
 *
 * ⚠️ 映射必须来自 `/meta.shop_type_by_shop`（库内 `GROUP BY shop`），⛔ 不能
 * 从当前页 `/data` 的 50 行反推——那受筛选影响会漏店。**映射缺失时整体降级
 * 为扁平下拉**（不分组 / 无徽章 / 无底色），保证老服务配新前端不白屏。
 *
 * @param shops - `/meta.shops`，后端已按店名排序。
 * @param shopTypes - `/meta.shop_types`，库内 `DISTINCT shop_type`。
 * @param typeByShop - `/meta.shop_type_by_shop`；可缺省（老服务）。
 * @returns 选项数组；首项恒为「全部店铺」（value 为空串，不分组）。
 * @example
 * ```ts
 * buildShopSelectOptions(["A", "B"], ["pop"], { A: "pop", B: "pop" })[1];
 * // { value: "A", label: "A", group: "pop", groupLabel: "POP（2 家）", badge: "POP", tone: "pop" }
 * ```
 */
export function buildShopSelectOptions(
  shops: string[],
  shopTypes: string[],
  typeByShop?: Record<string, string> | null,
): ShopSelectOption[] {
  const leading: ShopSelectOption[] = [{ value: "", label: "全部店铺" }];
  const map = typeByShop ?? {};
  // 老服务 / 未导入数据：整体扁平降级，页面仍可用。
  if (!Object.keys(map).length) {
    return [...leading, ...shops.map((s) => ({ value: s, label: s }))];
  }

  const known = SHOP_TYPE_ORDER.filter((t) => shopTypes.includes(t));
  const extra = shopTypes.filter((t) => !SHOP_TYPE_ORDER.includes(t)).sort();
  const order = [...known, ...extra];

  const buckets = new Map<string, string[]>();
  for (const s of shops) {
    const t = map[s];
    const key = t && order.includes(t) ? t : UNCLASSIFIED_GROUP;
    const bucket = buckets.get(key);
    if (bucket) bucket.push(s);
    else buckets.set(key, [s]);
  }

  const out: ShopSelectOption[] = [...leading];
  for (const t of order) {
    const items = buckets.get(t) ?? [];
    if (!items.length) continue;
    const label = shopTypeLabel(t);
    for (const s of items) {
      out.push({
        value: s,
        label: s,
        group: t,
        groupLabel: `${label}（${items.length} 家）`,
        badge: label,
        tone: t,
      });
    }
  }
  const unclassified = buckets.get(UNCLASSIFIED_GROUP) ?? [];
  for (const s of unclassified) {
    out.push({
      value: s,
      label: s,
      group: UNCLASSIFIED_GROUP,
      groupLabel: `未分类（${unclassified.length} 家）`,
      badge: "未分类",
    });
  }
  return out;
}
