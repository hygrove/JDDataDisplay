// 模块注册表 + 数值格式化：新增模块 = 在这里加一份配置，不写新页面。
// 配置驱动：表格列、指标卡片都由「指标清单（METRICS）+ 用户勾选」共同生成，
// 具体口径见 metrics.ts（那里是整个前端指标的唯一事实来源）。
import type { MetricFormat, MetricSpec } from "../metrics";
import { METRICS } from "../metrics";

export type { MetricSpec };

/**
 * 表格列的类型，决定单元格如何渲染与格式化。
 * - `index` 序号列
 * - `product` 商品信息列（图片 + 名称 + SPU）
 * - `text` 纯文本
 * - `number` / `currency` / `percent` 按对应格式渲染数值
 * - `action` 操作列（如「单品分析」入口），内容通过同名具名插槽渲染
 */
export type ColumnType =
  | "index"
  | "product"
  | "text"
  | "number"
  | "currency"
  | "percent"
  | "action";

/**
 * 表格列定义。
 */
export interface ColumnDef {
  /** 列 key：MetricKey，或 'index' / 'product' / 自定义操作列 key */
  key: string;
  /** 列标题 */
  title: string;
  /** 列宽（px） */
  width: number;
  /** 列类型，决定渲染方式 */
  type: ColumnType;
  /** 是否可排序；缺省 false */
  sortable?: boolean;
  /** 是否固定在表格右侧（横向滚动时不隐藏）；目前仅操作列使用 */
  sticky?: "right";
}

/**
 * 指标卡定义（由 MetricSpec 派生而来）。
 */
export interface MetricCardDef {
  /** 指标 key */
  key: string;
  /** 卡片标题 */
  title: string;
  /** 展示格式 */
  format: MetricFormat;
}

/**
 * 模块级配置：固定列 + 尾列，驱动表格结构。
 */
export interface ModuleConfig {
  /** 模块 id，与路由参数、后端 module_id 一致 */
  id: string;
  /** 模块标题 */
  title: string;
  /** 表格固定列（不参与指标配置）：序号、商品信息…… */
  leadColumns: ColumnDef[];
  /** 表格尾列：固定在右侧的操作列 */
  tailColumns: ColumnDef[];
}

/**
 * 模块配置注册表：id -> 配置。新增模块在这里加一份即可，不用写新页面。
 *
 * @example
 * ```ts
 * MODULE_CONFIGS["pop_spu_detail"].title; // "POP 单品明细"
 * ```
 */
export const MODULE_CONFIGS: Record<string, ModuleConfig> = {
  pop_spu_detail: {
    id: "pop_spu_detail",
    title: "POP 单品明细",
    leadColumns: [
      // index 与 product 是「固定列」：不参与指标配置，永远显示
      { key: "index", title: "序号", width: 60, type: "index" },
      { key: "product", title: "商品信息", width: 460, type: "product" },
    ],
    tailColumns: [
      // sticky: "right" 让「分析」入口在横向滚动时始终可见（区间模式列很多）
      { key: "analysis", title: "分析", width: 110, type: "action", sticky: "right" },
    ],
  },
};

/**
 * 取模块配置。
 *
 * @param {string} id - 模块 id，如 "pop_spu_detail"。
 * @returns {ModuleConfig} 对应的模块配置。
 * @throws {Error} 模块未注册时抛出 `未注册的模块：${id}`。
 *      刻意抛错而不是返回 undefined：模块没注册说明是配置遗漏，属于必须暴露的开发期错误。
 * @example
 * ```ts
 * const cfg = getModuleConfig("pop_spu_detail");
 * cfg.leadColumns.length; // 2
 * ```
 */
export function getModuleConfig(id: string): ModuleConfig {
  const c = MODULE_CONFIGS[id];
  if (!c) throw new Error(`未注册的模块：${id}`);
  return c;
}

// ---------- 指标 -> 展示结构 ----------
/**
 * 把指标的展示格式映射成表格列类型。
 *
 * @param {MetricFormat} format - 指标展示格式。
 * @returns {ColumnType} currency / percent 原样映射，其余（number / decimal）统一按 number 列渲染。
 * @example
 * ```ts
 * columnTypeOf("currency"); // "currency"
 * columnTypeOf("decimal");  // "number"
 * ```
 */
function columnTypeOf(format: MetricFormat): ColumnType {
  switch (format) {
    case "currency":
      return "currency";
    case "percent":
      return "percent";
    default:
      // decimal（如 ROI）也按数字列渲染，只是格式化时保留两位小数
      return "number";
  }
}

/**
 * 指标清单条目 -> 表格列定义。
 *
 * @param {MetricSpec} spec - 指标定义。
 * @returns {ColumnDef} 对应的表格列（默认可排序）。
 * @example
 * ```ts
 * specToColumn(METRICS[0]);
 * // { key: "amount", title: "成交金额", width: 130, type: "currency", sortable: true }
 * ```
 */
export function specToColumn(spec: MetricSpec): ColumnDef {
  return {
    key: spec.key as string,
    title: spec.title,
    width: spec.width,
    type: columnTypeOf(spec.format),
    sortable: true,
  };
}

/**
 * 指标清单条目 -> 指标卡定义。
 *
 * @param {MetricSpec} spec - 指标定义。
 * @returns {MetricCardDef} 指标卡所需的 key / title / format。
 * @example
 * ```ts
 * specToCard(getMetric("roi")!); // { key: "roi", title: "ROI", format: "decimal" }
 * ```
 */
export function specToCard(spec: MetricSpec): MetricCardDef {
  return { key: spec.key as string, title: spec.title, format: spec.format };
}

/**
 * 全部指标（含未勾选的）对应的表格列。按需使用——实际渲染要用「用户勾选后」的列。
 *
 * @example
 * ```ts
 * ALL_METRIC_COLUMNS.length; // 15（等于 METRICS 条数）
 * ```
 */
export const ALL_METRIC_COLUMNS: ColumnDef[] = METRICS.map(specToColumn);

// ---------- 数值格式化（缺失一律展示 --）----------
/**
 * 整数格式化：四舍五入 + 千分位。
 *
 * ⚠️ 本函数会**取整**（Math.round）。用来展示「访客数 / 客户数 / 单量」这类本身是整数的值没问题，
 * 但**不要**拿它格式化「日均 / 平均值」——例如 15 单 ÷ 22 天 = 0.68 会被显示成 1。
 * 日均请改用各视图里的 fmtAvg（number 格式最多保留 2 位小数）。
 *
 * @param {number | null | undefined} v - 待格式化的值；null / undefined / NaN 一律返回 "0"。
 * @returns {string} 带千分位的整数字符串。
 * @example
 * ```ts
 * fmtNumber(1234.6); // "1,235"
 * fmtNumber(null);   // "0"
 * ```
 */
export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0";
  return Math.round(v).toLocaleString("zh-CN");
}

/**
 * 金额格式化：¥ 前缀 + 千分位 + 固定两位小数。
 *
 * @param {number | null | undefined} v - 金额；null / undefined / NaN 返回 "¥0.00"。
 * @returns {string} 形如 "¥12,345.67"。
 * @example
 * ```ts
 * fmtCurrency(12345.6); // "¥12,345.60"
 * fmtCurrency(null);    // "¥0.00"
 * ```
 */
export function fmtCurrency(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "¥0.00";
  // minimumFractionDigits 保证「整金额」也显示成 ¥100.00，金额列对齐好看
  return "¥" + v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * 百分比格式化：小数 × 100 + 保留两位 + % 后缀。
 *
 * @param {number | null | undefined} v - 比率小数（0.1234 表示 12.34%）；
 *      null / undefined / NaN 返回 "0.00%"。
 * @returns {string} 形如 "12.34%"。
 * @example
 * ```ts
 * fmtPercent(0.1234); // "12.34%"
 * fmtPercent(null);   // "0.00%"
 * ```
 */
export function fmtPercent(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0.00%";
  return (v * 100).toFixed(2) + "%";
}

/**
 * 小数格式化：固定两位小数（不带单位，用于 ROI）。
 *
 * @param {number | null | undefined} v - 数值；null / undefined / NaN 返回 "0.00"。
 * @returns {string} 形如 "3.42"。
 * @example
 * ```ts
 * fmtDecimal(3.423); // "3.42"
 * fmtDecimal(null);  // "0.00"
 * ```
 */
export function fmtDecimal(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0.00";
  return v.toFixed(2);
}

/**
 * 按指标的展示格式分派到对应的格式化函数。
 *
 * @param {MetricFormat} format - 展示格式：currency / percent / decimal / number。
 * @param {number | null | undefined} v - 待格式化的值。
 * @returns {string} 格式化后的字符串。
 * @throws 无。未识别的 format 走 default 分支按整数处理（TypeScript 已约束取值）。
 * @example
 * ```ts
 * fmtBy("currency", 1234.5); // "¥1,234.50"
 * fmtBy("percent", 0.05);    // "5.00%"
 * fmtBy("decimal", 3.4);     // "3.40"
 * fmtBy("number", 1234.5);   // "1,235"（注意会取整）
 * ```
 */
export function fmtBy(
  format: MetricFormat,
  v: number | null | undefined,
): string {
  switch (format) {
    case "currency":
      return fmtCurrency(v);
    case "percent":
      return fmtPercent(v);
    case "decimal":
      return fmtDecimal(v);
    default:
      return fmtNumber(v);
  }
}
