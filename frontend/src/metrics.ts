// 指标清单：整个前端「展示哪些指标、怎么聚合、怎么格式化」的唯一事实来源。
//
// 为什么要单独抽一份：
//   1) 模块页的指标卡、表格列，单品分析页的指标卡、趋势图、工作日/节假日对比，
//      以前各自手写了一份（改一个字段要改 4 处），现在全部由这份 METRICS 派生；
//   2) 「指标配置」按钮勾选的也是这份清单里的 key，勾选结果存 localStorage，
//      模块页与单品分析页共用，天然同步。
//
// 字段含义：
//   title      展示名      format   展示格式（number/currency/percent/decimal）
//   group      分组（决定配置面板里的归类）        width  表格列宽
//   color      趋势图配色    defaultOn 是否默认勾选
//   agg        汇总口径：sum=直接求和；ratio=先把分子分母各自求和再相除。
//              ⚠️ 比率类指标（转化率、客单价、点击率、ROI、推广占比）必须走 ratio，
//              否则多日汇总会得到「把每天的百分比加起来」这种错误结果。
//   compare    单品分析页「工作日 vs 节假日」取值的 CompareGroup 字段名
//
// ⚠️ 新增/修改指标时，必须同步后端：backend/jobs/models.py（MetricRecord 字段）
//    与 backend/app/api.py 的 METRIC_KEYS，否则前端会拿到 undefined。
import type { MetricKey } from "./types";

/**
 * 指标的展示格式：
 * - `number`   整数（千分位），如访客数、客户数、单量；
 * - `currency` 金额（¥ + 两位小数），如成交金额、推广花费；
 * - `percent`  百分比小数（渲染时 ×100 加 %），如转化率、推广占比；
 * - `decimal`  两位小数（不带单位），如 ROI。
 */
export type MetricFormat = "number" | "currency" | "percent" | "decimal";

/** 指标分组：detail=商品明细表 / promo=推广数据表 / extra=退款及其他。 */
export type MetricGroup = "detail" | "promo" | "extra";

/**
 * 单个指标的完整定义（METRICS 里的一项）。
 */
export interface MetricSpec {
  /** 指标 key，需与后端 MetricRecord 字段名一致 */
  key: MetricKey;
  /** 线性图标名（见 components/MetricIcon.vue 支持的名字） */
  icon: string;
  /** 展示名（中文） */
  title: string;
  /** 展示格式，决定数值如何格式化 */
  format: MetricFormat;
  /** 所属分组，决定在「指标配置」面板里归到哪一类 */
  group: MetricGroup;
  /** 表格列宽（px） */
  width: number;
  /** 趋势图折线配色 */
  color: string;
  /** 是否默认勾选；未勾选的指标需用户在「指标配置」里手动打开 */
  defaultOn: boolean;
  /**
   * 区间汇总口径：
   * - `{ kind: "sum" }` 直接累加；
   * - `{ kind: "ratio", num, den }` 先分别累加分子分母再相除。
   */
  agg:
    | { kind: "sum" }
    | { kind: "ratio"; num: MetricKey; den: MetricKey };
  /** 单品分析页「工作日 vs 节假日」对比时，从 CompareGroup 取的字段名；null 表示不参与对比 */
  compare: keyof import("./types").CompareGroup | null;
  /** 对比行标题；缺省时回退用 title */
  compareLabel?: string;
}

/**
 * 指标分组元信息（「指标配置」面板的分组标题与数据来源提示）。
 *
 * @example
 * ```ts
 * METRIC_GROUPS[0].title; // "商品明细表"
 * ```
 */
export const METRIC_GROUPS: { key: MetricGroup; title: string; hint: string }[] = [
  { key: "detail", title: "商品明细表", hint: "来源：{店铺}_商品明细_*.xlsx" },
  { key: "promo", title: "推广数据表", hint: "来源：{店铺}_推广数据_*.csv" },
  { key: "extra", title: "退款 / 其他", hint: "取消及售后 + 可选的补充指标" },
];

/**
 * 全部可选指标（共 15 项，分 3 组）。顺序即页面上的展示顺序。
 *
 * @remarks
 * 注意 `search_clicks`（搜索点击次数）**不在**本清单里：
 * 它只作为 search_click_rate 的分子存在，不作为独立指标展示，
 * 但要参与区间累加（见 sumRawMetrics 的说明）。
 *
 * @example
 * ```ts
 * METRICS.length;                                  // 15
 * METRICS.filter((m) => m.defaultOn).length;       // 8（默认勾选数）
 * ```
 */
export const METRICS: MetricSpec[] = [
  // ---------- 商品明细表 ----------
  { key: "amount", title: "成交金额", format: "currency", group: "detail", width: 130, color: "#f59e0b", icon: "yen", defaultOn: true, agg: { kind: "sum" }, compare: "amount_avg", compareLabel: "日均成交金额" },
  { key: "orders", title: "成交单量", format: "number", group: "detail", width: 110, color: "#0ea5e9", icon: "cart", defaultOn: false, agg: { kind: "sum" }, compare: "orders_avg", compareLabel: "日均成交单量" },
  { key: "buyers", title: "成交客户数", format: "number", group: "detail", width: 110, color: "#8b5cf6", icon: "users", defaultOn: true, agg: { kind: "sum" }, compare: "buyers_avg", compareLabel: "日均成交客户数" },
  { key: "items", title: "成交商品件数", format: "number", group: "detail", width: 120, color: "#0d9488", icon: "box", defaultOn: false, agg: { kind: "sum" }, compare: "items_avg", compareLabel: "日均成交件数" },
  { key: "conversion_rate", title: "成交转化率", format: "percent", group: "detail", width: 110, color: "#e1251b", icon: "funnel", defaultOn: true, agg: { kind: "ratio", num: "buyers", den: "visitors" }, compare: "conversion_rate" },
  { key: "avg_price", title: "客单价", format: "currency", group: "detail", width: 110, color: "#14b8a6", icon: "tag", defaultOn: false, agg: { kind: "ratio", num: "amount", den: "buyers" }, compare: "avg_price" },
  { key: "search_impressions", title: "搜索曝光次数", format: "number", group: "detail", width: 120, color: "#6366f1", icon: "search", defaultOn: false, agg: { kind: "sum" }, compare: "search_impressions_avg", compareLabel: "日均搜索曝光" },
  { key: "search_click_rate", title: "搜索点击率", format: "percent", group: "detail", width: 110, color: "#0891b2", icon: "mouse", defaultOn: false, agg: { kind: "ratio", num: "search_clicks", den: "search_impressions" }, compare: "search_click_rate" },
  { key: "visitors", title: "商品访客数", format: "number", group: "detail", width: 110, color: "#2563eb", icon: "eye", defaultOn: true, agg: { kind: "sum" }, compare: "visitors_avg", compareLabel: "日均访客数" },

  // ---------- 推广数据表 ----------
  { key: "promotion_cost", title: "推广花费", format: "currency", group: "promo", width: 120, color: "#db2777", icon: "mega", defaultOn: true, agg: { kind: "sum" }, compare: "promotion_cost_avg", compareLabel: "日均推广花费" },
  { key: "promotion_amount", title: "推广成交金额", format: "currency", group: "promo", width: 130, color: "#9333ea", icon: "wallet", defaultOn: true, agg: { kind: "sum" }, compare: "promotion_amount_avg", compareLabel: "日均推广成交金额" },
  { key: "roi", title: "ROI", format: "decimal", group: "promo", width: 90, color: "#ea580c", icon: "trend", defaultOn: true, agg: { kind: "ratio", num: "promotion_amount", den: "promotion_cost" }, compare: "roi" },
  { key: "promotion_ratio", title: "推广占比", format: "percent", group: "promo", width: 110, color: "#7c3aed", icon: "pie", defaultOn: true, agg: { kind: "ratio", num: "promotion_cost", den: "amount" }, compare: "promotion_ratio" },

  // ---------- 退款 / 其他 ----------
  { key: "refund_amount", title: "取消及售后退款金额", format: "currency", group: "extra", width: 160, color: "#dc2626", icon: "refund", defaultOn: false, agg: { kind: "sum" }, compare: "refund_amount_avg", compareLabel: "日均退款金额" },
  { key: "refund_orders", title: "取消及售后退款单量", format: "number", group: "extra", width: 160, color: "#f97316", icon: "refund", defaultOn: false, agg: { kind: "sum" }, compare: "refund_orders_avg", compareLabel: "日均退款单量" },
];

/**
 * 默认勾选的指标 key（由 METRICS 的 defaultOn 派生）。
 *
 * @remarks
 * 当前 8 项：商品明细表 4 项（成交金额、成交客户数、成交转化率、商品访客数）
 * + 推广数据表 4 项（推广花费、推广成交金额、ROI、推广占比）。
 *
 * @example
 * ```ts
 * DEFAULT_METRIC_KEYS.includes("amount"); // true
 * DEFAULT_METRIC_KEYS.includes("orders"); // false（默认不勾选）
 * ```
 */
export const DEFAULT_METRIC_KEYS: string[] = METRICS.filter((m) => m.defaultOn).map((m) => m.key);

/**
 * key -> MetricSpec 的快速索引（避免每次按 key 查指标都 O(n) 遍历 METRICS）。
 *
 * @example
 * ```ts
 * METRIC_MAP["amount"]?.title; // "成交金额"
 * ```
 */
export const METRIC_MAP: Record<string, MetricSpec> = Object.fromEntries(
  METRICS.map((m) => [m.key as string, m]),
);

/**
 * 按 key 取指标定义。
 *
 * @param {string} key - 指标 key（如 "amount"、"conversion_rate"）。
 * @returns {MetricSpec | undefined} 对应的指标定义；key 不存在时返回 undefined。
 * @throws 无（查不到只返回 undefined，不抛异常，方便调用方用 `??` 兜底）。
 * @example
 * ```ts
 * getMetric("roi")?.format;        // "decimal"
 * getMetric("not_exist");          // undefined
 * ```
 */
export function getMetric(key: string): MetricSpec | undefined {
  return METRIC_MAP[key];
}

/**
 * 按「清单顺序」把勾选的 key 数组还原成 MetricSpec 列表。
 *
 * @remarks
 * 刻意**不按传入数组的顺序**返回，而是按 METRICS 的声明顺序：
 * 这样无论用户以什么顺序勾选，页面上的指标排列始终稳定一致，不会来回跳。
 *
 * @param {string[]} keys - 用户勾选的指标 key 数组（来自指标配置 / localStorage）。
 * @returns {MetricSpec[]} 对应顺序的指标定义列表；未命中的 key 被忽略。
 * @example
 * ```ts
 * metricsOf(["visitors", "amount"]).map((m) => m.title);
 * // ["成交金额", "商品访客数"] —— 按清单顺序而非传入顺序
 * ```
 */
export function metricsOf(keys: string[]): MetricSpec[] {
  const set = new Set(keys);
  return METRICS.filter((m) => set.has(m.key as string));
}

/**
 * 只做「原始字段累加」：返回所有 sum 指标 ∪ 所有 ratio 分子/分母 的区间累计值。
 *
 * 为什么要单独暴露它：aggregateMetrics 的返回值只含 METRICS 清单里的 key
 * （即 `{} as Record<MetricKey, ...>` 后只遍历 METRICS 赋值），而像 search_clicks
 * 这类「隐藏分子」不在清单里 —— 直接读 aggregateMetrics(...)["search_clicks"] 恒为
 * undefined（前端会显示 0）。需要展示比率类指标的分子/分母区间累计时，请用本函数。
 *
 * @param {Array<Partial<Record<MetricKey, number | null>>>} points - 逐日的指标点数组。
 * @returns {Partial<Record<MetricKey, number>>} 各原始字段的区间累计值；
 *      值为 null / NaN 的项被跳过（不污染累加结果）；空数组时返回空对象。
 * @throws 无。
 * @example
 * ```ts
 * sumRawMetrics([{ search_clicks: 10 }, { search_clicks: 5 }]);
 * // { visitors: 0, buyers: 0, ..., search_clicks: 15, ... }
 * ```
 */
export function sumRawMetrics(
  points: Array<Partial<Record<MetricKey, number | null>>>,
): Partial<Record<MetricKey, number>> {
  // 先收集「需要累加哪些原始字段」：sum 指标自身 + 所有 ratio 的分子分母
  const sumKeys = new Set<MetricKey>();
  for (const m of METRICS) {
    if (m.agg.kind === "sum") {
      sumKeys.add(m.key);
    } else {
      sumKeys.add(m.agg.num);
      sumKeys.add(m.agg.den);
    }
  }
  const sums: Partial<Record<MetricKey, number>> = {};
  for (const p of points) {
    for (const k of sumKeys) {
      const v = p[k];
      // 只累加「确实是数字」的值：null 表示当日缺失，NaN 表示脏数据，都跳过
      if (typeof v === "number" && !Number.isNaN(v)) {
        sums[k] = (sums[k] ?? 0) + v;
      }
    }
  }
  return sums;
}

/**
 * 通用汇总：把若干「一天的指标」合并成一个区间的指标。
 *
 * sum 类直接累加；ratio 类先分别累加分子分母再相除（与后端 aggregate 口径一致）。
 * 传入空数组时每一项都是 null，前端展示 `--`。
 *
 * ⚠️ 需要累加的原始字段 = 所有 sum 指标 ∪ 所有 ratio 的分子/分母。
 * 不能只遍历 METRICS 累加：像 search_clicks 只是 search_click_rate 的分子、
 * 本身不在清单里（不作为独立指标展示），只按 METRICS 遍历会让它的和恒为 0，
 * 进而把搜索点击率永远算成 0%（历史 bug：单品分析页搜索点击率一直 0.00%）。
 * 需要「原始累计值」（如展示分子/分母明细）时用 sumRawMetrics，不要读本函数返回值。
 *
 * @param {Array<Partial<Record<MetricKey, number | null>>>} points - 逐日的指标点数组。
 * @returns {Record<MetricKey, number | null>} 区间汇总后的指标；
 *      ratio 类分母为 0 时为 null（前端展示 `--`），绝不返回 Infinity/NaN。
 * @throws 无。
 * @example
 * ```ts
 * aggregateMetrics([{ buyers: 5, visitors: 100 }, { buyers: 5, visitors: 100 }]);
 * // conversion_rate = 10 / 200 = 0.05（先求和再相除，而不是 0.05 与 0.05 求平均）
 * ```
 */
export function aggregateMetrics(
  points: Array<Partial<Record<MetricKey, number | null>>>,
): Record<MetricKey, number | null> {
  const sums = sumRawMetrics(points);
  const out = {} as Record<MetricKey, number | null>;
  for (const m of METRICS) {
    const key = m.key;
    if (m.agg.kind === "ratio") {
      const num = sums[m.agg.num] ?? 0;
      const den = sums[m.agg.den] ?? 0;
      // 分母为 0 时给 null 而不是 Infinity —— Infinity 序列化后会变成 null 且前端显示异常
      out[key] = den ? num / den : null;
    } else {
      out[key] = sums[key] ?? null;
    }
  }
  return out;
}
