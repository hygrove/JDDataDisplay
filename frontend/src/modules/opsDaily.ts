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
 * 以数据最新日为终点，生成默认查询区间（含终点共 days 天）。
 *
 * @remarks
 * 首屏必须出真数据（D2），所以不能默认空区间；取近 30 天既保证有数据，
 * 又不会一上来把整段历史铺到表格里。全区间由页面上的按钮显式切换。
 *
 * @param through - 库内最大日期 "YYYY-MM-DD"。
 * @param days - 区间天数，默认 30。
 * @returns 起止日期。
 * @example
 * ```ts
 * defaultRange("2026-10-31"); // { from: "2026-10-02", to: "2026-10-31" }
 * ```
 */
export function defaultRange(through: string, days = 30): { from: string; to: string } {
  const DAY = 86_400_000;
  const end = Date.parse(`${through}T00:00:00Z`);
  const start = end - Math.max(1, days - 1) * DAY;
  return { from: new Date(start).toISOString().slice(0, 10), to: through };
}

/**
 * 算出与当前区间等长的「上一期」区间，供 KPI 卡环比使用。
 *
 * @remarks
 * 用 UTC 毫秒计算再截回日期串，避免本地时区夏令时导致的天数偏差。
 *
 * @param from - 当前区间起点 "YYYY-MM-DD"。
 * @param to - 当前区间终点 "YYYY-MM-DD"。
 * @returns 上一期起止日期。
 * @example
 * ```ts
 * previousRange("2026-10-01", "2026-10-31"); // { from: "2026-09-01", to: "2026-09-30" }
 * ```
 */
export function previousRange(from: string, to: string): { from: string; to: string } {
  const DAY = 86_400_000;
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  const days = Math.max(1, Math.round((end - start) / DAY) + 1);
  const prevEnd = start - DAY;
  const prevStart = prevEnd - (days - 1) * DAY;
  return {
    from: new Date(prevStart).toISOString().slice(0, 10),
    to: new Date(prevEnd).toISOString().slice(0, 10),
  };
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

/** 默认勾选列数（V1.0 只启用勾选，不实现配置面板的分组 / 全选 / 重置）。 */
export const DEFAULT_KEY_COUNT = 10;

/**
 * 生成默认勾选列：原子指标打底，并固定留出 2 个派生指标。
 *
 * @remarks
 * 为什么不是「字典前 10 项」：字典里 5 个派生排在最后（sort_order 20~24），
 * 取前 10 项会全是原子列 ⇒ 首屏看不到派生重算值。
 * 留出 2 个派生（转化率、客单价）能让「原子 → 派生」的对账关系一眼可见；
 * 表头是单行指标名，不按 kind 分组，勾选集合只决定「有哪些列」。
 * 返回的**选取集合**不决定列序：页面始终按字典 sort_order 渲染。
 *
 * @param metrics - /meta 下发的字典。
 * @param count - 默认列数，取 DEFAULT_KEY_COUNT。
 * @returns 被勾选的 metric_key 数组。
 * @example
 * ```ts
 * defaultSelectedKeys(meta.metrics).length; // 10
 * ```
 */
export function defaultSelectedKeys(
  metrics: OpsMetric[],
  count = DEFAULT_KEY_COUNT,
): string[] {
  const atomic = metrics.filter((m) => m.kind === "atomic");
  const derived = metrics.filter((m) => m.kind === "derived");
  const derivedTake = Math.min(2, derived.length, count);
  const atomicTake = Math.min(atomic.length, count - derivedTake);
  return [
    ...atomic.slice(0, atomicTake).map((m) => m.metric_key),
    ...derived.slice(0, derivedTake).map((m) => m.metric_key),
  ];
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
