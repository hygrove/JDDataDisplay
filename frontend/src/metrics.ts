// 指标清单：整个前端「展示哪些指标」的唯一事实来源。
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
import type { MetricKey } from "./types";

export type MetricFormat = "number" | "currency" | "percent" | "decimal";
export type MetricGroup = "detail" | "promo" | "extra";

export interface MetricSpec {
  key: MetricKey;
  icon: string; // 指标线性图标名（见 components/MetricIcon.vue）
  title: string;
  format: MetricFormat;
  group: MetricGroup;
  width: number;
  color: string;
  defaultOn: boolean;
  agg:
    | { kind: "sum" }
    | { kind: "ratio"; num: MetricKey; den: MetricKey };
  compare: keyof import("./types").CompareGroup | null;
  compareLabel?: string; // 对比行标题，缺省用 title
}

export const METRIC_GROUPS: { key: MetricGroup; title: string; hint: string }[] = [
  { key: "detail", title: "商品明细表", hint: "来源：{店铺}_商品明细_*.xlsx" },
  { key: "promo", title: "推广数据表", hint: "来源：{店铺}_推广数据_*.csv" },
  { key: "extra", title: "退款 / 其他", hint: "取消及售后 + 可选的补充指标" },
];

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

// 默认勾选：由 defaultOn 决定（成交金额/成交客户数/成交转化率/商品访客数 + 推广花费/推广成交金额/ROI/推广占比）
export const DEFAULT_METRIC_KEYS: string[] = METRICS.filter((m) => m.defaultOn).map((m) => m.key);

export const METRIC_MAP: Record<string, MetricSpec> = Object.fromEntries(
  METRICS.map((m) => [m.key as string, m]),
);

export function getMetric(key: string): MetricSpec | undefined {
  return METRIC_MAP[key];
}

/** 按勾选顺序+清单顺序返回 spec（配置里存的是 key 数组）。 */
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
 */
export function sumRawMetrics(
  points: Array<Partial<Record<MetricKey, number | null>>>,
): Partial<Record<MetricKey, number>> {
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
      if (typeof v === "number" && !Number.isNaN(v)) {
        sums[k] = (sums[k] ?? 0) + v;
      }
    }
  }
  return sums;
}

/**
 * 通用汇总：把若干「一天的指标」合并成一个区间的指标。
 * sum 类直接累加；ratio 类先分别累加分子分母再相除（与后端 aggregate 口径一致）。
 * 传入空数组时每一项都是 null，前端展示 --。
 *
 * ⚠️ 需要累加的原始字段 = 所有 sum 指标 ∪ 所有 ratio 的分子/分母。
 * 不能只遍历 METRICS 累加：像 search_clicks 只是 search_click_rate 的分子、
 * 本身不在清单里（不作为独立指标展示），只按 METRICS 遍历会让它的和恒为 0，
 * 进而把搜索点击率永远算成 0%（历史 bug：单品分析页搜索点击率一直 0.00%）。
 * 需要「原始累计值」（如展示分子/分母明细）时用 sumRawMetrics，不要读本函数返回值。
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
      out[key] = den ? num / den : null;
    } else {
      out[key] = sums[key] ?? null;
    }
  }
  return out;
}
