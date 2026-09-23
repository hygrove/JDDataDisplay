// 模块注册表：新增模块 = 在这里加一份配置，不写新页面。
// 配置驱动：表格列、指标卡片都由「指标清单（METRICS）+ 用户勾选」共同生成，
// 具体口径见 metrics.ts（那里是整个前端指标的唯一事实来源）。
import type { MetricFormat, MetricSpec } from "../metrics";
import { METRICS } from "../metrics";

export type { MetricSpec };

export type ColumnType =
  | "index" // 序号
  | "product" // 商品信息（图片+名称+SPU）
  | "text"
  | "number"
  | "currency"
  | "percent"
  | "action"; // 操作列（如「单品分析」入口），内容通过同名具名插槽渲染

export interface ColumnDef {
  key: string; // MetricKey 或 'index' / 'product' / 自定义操作列 key
  title: string;
  width: number;
  type: ColumnType;
  sortable?: boolean;
  sticky?: "right"; // 固定在表格右侧，横向滚动时不隐藏
}

export interface MetricCardDef {
  key: string; // MetricKey
  title: string;
  format: MetricFormat;
}

export interface ModuleConfig {
  id: string;
  title: string;
  /** 表格固定列（不参与指标配置）：序号、商品信息…… */
  leadColumns: ColumnDef[];
  /** 表格尾列：固定在右侧的操作列 */
  tailColumns: ColumnDef[];
}

export const MODULE_CONFIGS: Record<string, ModuleConfig> = {
  pop_spu_detail: {
    id: "pop_spu_detail",
    title: "POP 单品明细",
    leadColumns: [
      { key: "index", title: "序号", width: 60, type: "index" },
      { key: "product", title: "商品信息", width: 460, type: "product" },
    ],
    tailColumns: [
      { key: "analysis", title: "分析", width: 110, type: "action", sticky: "right" },
    ],
  },
};

export function getModuleConfig(id: string): ModuleConfig {
  const c = MODULE_CONFIGS[id];
  if (!c) throw new Error(`未注册的模块：${id}`);
  return c;
}

// ---------- 指标 -> 展示结构 ----------
function columnTypeOf(format: MetricFormat): ColumnType {
  switch (format) {
    case "currency":
      return "currency";
    case "percent":
      return "percent";
    default:
      return "number";
  }
}

/** 指标清单条目 -> 表格列定义。 */
export function specToColumn(spec: MetricSpec): ColumnDef {
  return {
    key: spec.key as string,
    title: spec.title,
    width: spec.width,
    type: columnTypeOf(spec.format),
    sortable: true,
  };
}

/** 指标清单条目 -> 指标卡定义。 */
export function specToCard(spec: MetricSpec): MetricCardDef {
  return { key: spec.key as string, title: spec.title, format: spec.format };
}

/** 全部指标（含未勾选的）对应的列，按需使用。 */
export const ALL_METRIC_COLUMNS: ColumnDef[] = METRICS.map(specToColumn);

// ---------- 数值格式化（缺失一律展示 --）----------
export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0";
  return Math.round(v).toLocaleString("zh-CN");
}

export function fmtCurrency(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "¥0.00";
  return "¥" + v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function fmtPercent(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0.00%";
  return (v * 100).toFixed(2) + "%";
}

export function fmtDecimal(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0.00";
  return v.toFixed(2);
}

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
