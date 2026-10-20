<script setup lang="ts">
// 库存矩阵表：身份列（冻结在左侧）+ 每城指标展开成 8 城子列 + 全国指标单列。
// 设计要点：
// - 表头两行：第一行指标名（每城指标 colspan=8 城数），第二行 8 城名；
//   身份列与国家单列用 rowspan=2 跨两行。
// - 左侧身份列 sticky 固定，便于横向滚动时始终看清是哪一列 SKU。
// - 合计行（tfoot）：每城指标按 city_totals 逐城求和；aggregatable=false 显 "—"。
import { computed } from "vue";
import type { InventoryMetric, InvCell } from "../../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 可见的「非身份」指标（per_city + national，已按展示顺序）；身份列由本组件内置 */
  visibleMetrics: InventoryMetric[];
  /** 城市白名单（按展示顺序） */
  cities: string[];
  /** 当页行（全列 dict） */
  rows: Record<string, InvCell>[];
  /** 指标 key → 合计值（per_city 为 8 城求和单一值） */
  totals: Record<string, number | null>;
  /** 指标 key → 每城求和数组（下标对应 cities） */
  cityTotals: Record<string, number[]>;
  /** 当前排序字段（DB 字段名） */
  sortBy: string | null;
  /** 当前排序方向 */
  sortOrder: "asc" | "desc";
}>();

/** 点击列头排序（传 DB 字段名）。 */
const emit = defineEmits<{ (e: "sort", field: string): void }>();

/**
 * 内置身份列（含主键 sku）：固定顺序与宽度。
 *
 * @remarks
 * sku 是主键但不在 meta 的 identity 指标里（identity 指标是 5 个文本列），
 * 这里把它前置进来，保证矩阵总能定位到具体商品。
 */
const IDENTITY_DEFS = [
  { field: "sku", title: "SKU", width: 130 },
  { field: "sku_name", title: "商品名称", width: 200 },
  { field: "spu", title: "SPU编码", width: 130 },
  { field: "category", title: "三级类目", width: 130 },
  { field: "shelf_status", title: "上下柜", width: 96 },
  { field: "seller", title: "销售员", width: 110 },
] as const;

/** 身份列左偏移（sticky 固定列用）。 */
const identityLeft = computed<number[]>(() => {
  const arr: number[] = [];
  let acc = 0;
  for (const c of IDENTITY_DEFS) {
    arr.push(acc);
    acc += c.width;
  }
  return arr;
});

/** 每城 / 全国子列宽度。 */
const CITY_COL_W = 92;
const NAT_COL_W = 116;

/**
 * 分组表头（不含身份列）：每个可见指标一组；per_city 展开成 8 城子列。
 */
interface SubCol {
  field: string;
  title: string;
  cityIndex?: number;
}
interface Group {
  metric: InventoryMetric;
  subCols: SubCol[];
}
const groups = computed<Group[]>(() => {
  const out: Group[] = [];
  for (const m of props.visibleMetrics) {
    if (m.kind === "per_city") {
      const sub: SubCol[] = props.cities.map((c, i) => ({ field: m.fields[i], title: c, cityIndex: i }));
      out.push({ metric: m, subCols: sub });
    } else {
      out.push({ metric: m, subCols: [{ field: m.fields[0], title: m.title }] });
    }
  }
  return out;
});

/**
 * 扁平列（用于 body / tfoot 渲染）：身份列 + 每个分组的子列。
 */
const bodyCols = computed(() => {
  const cols: { field: string; kind: "identity" | "city" | "national"; agg?: boolean; metricKey?: string }[] = [];
  for (const c of IDENTITY_DEFS) cols.push({ field: c.field, kind: "identity" });
  for (const g of groups.value) {
    for (const sc of g.subCols) {
      cols.push({
        field: sc.field,
        kind: g.metric.kind === "per_city" ? "city" : "national",
        agg: g.metric.aggregatable,
        metricKey: g.metric.key,
      });
    }
  }
  return cols;
});

/**
 * 单元格数值格式化：整数带千分位；小数最多 2 位；空/非法 → "—"。
 */
function fmtNum(v: InvCell): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const r = Math.round(n * 100) / 100;
  return Number.isInteger(r)
    ? r.toLocaleString("zh-CN")
    : r.toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/**
 * 上下柜状态 → 标签样式。
 */
function shelfTag(v: InvCell): { text: string; cls: string } | null {
  if (typeof v !== "string" || !v) return null;
  if (v.includes("上")) return { text: v, cls: "up" };
  if (v.includes("下")) return { text: v, cls: "down" };
  return { text: v, cls: "neutral" };
}

/**
 * 合计行单元格取值：可累加指标按城 / 按指标求和；否则 "—"。
 */
function totalFor(g: Group, sc: SubCol): string {
  if (!g.metric.aggregatable) return "—";
  if (g.metric.kind === "per_city") {
    const arr = props.cityTotals[g.metric.key];
    if (!arr || sc.cityIndex === undefined) return "—";
    return fmtNum(arr[sc.cityIndex]);
  }
  return fmtNum(props.totals[g.metric.key]);
}

/**
 * 点击列头排序：再次点同一列切换方向由父级处理，这里只抛出字段名。
 */
function onSort(field: string): void {
  emit("sort", field);
}

/** 排序箭头方向。 */
function arrow(field: string): string {
  if (props.sortBy !== field) return "";
  return props.sortOrder === "asc" ? "▲" : "▼";
}
</script>

<template>
  <div class="mt-scroll" data-testid="inv-matrix-scroll">
    <table class="mt" data-testid="inv-matrix-table">
      <thead>
        <tr>
          <!-- 身份列（rowspan=2，冻结） -->
          <th
            v-for="(c, i) in IDENTITY_DEFS"
            :key="'id-' + c.field"
            class="th th-id"
            :class="{ 'th-sortable': true, active: sortBy === c.field }"
            :style="{ left: identityLeft[i] + 'px', width: c.width + 'px', minWidth: c.width + 'px' }"
            rowspan="2"
            @click="onSort(c.field)"
          >
            <span class="th-label">{{ c.title }}</span>
            <span class="th-arrow" v-if="arrow(c.field)">{{ arrow(c.field) }}</span>
          </th>

          <!-- 指标分组：per_city colspan=城数；national colspan=1 -->
          <th
            v-for="g in groups"
            :key="'g-' + g.metric.key"
            class="th th-group"
            :class="{ 'th-sortable': true, active: sortBy === g.metric.key || (g.metric.kind === 'per_city' && g.subCols.some((s) => sortBy === s.field)) }"
            :colspan="g.subCols.length"
          >
            <span class="th-label">{{ g.metric.title }}</span>
          </th>
        </tr>
        <tr>
          <!-- 第二行：仅每城指标展开的城市名。
               ⚠️ 不能再用外层 <th> 包裹：HTML 不允许 th 嵌套 th，浏览器会把外层
               解析成额外的空单元格，导致全部城市名右移一列（实测截图复现）。
               national / 身份列的格子被第一行 rowspan 覆盖，这里跳过即自动对齐。 -->
          <template v-for="g in groups" :key="'g2-' + g.metric.key">
            <th
              v-for="sc in (g.metric.kind === 'per_city' ? g.subCols : [])"
              :key="sc.field"
              class="th th-city"
              :class="{ 'th-sortable': true, active: sortBy === sc.field }"
              :style="{ width: CITY_COL_W + 'px', minWidth: CITY_COL_W + 'px' }"
              @click="onSort(sc.field)"
            >
              <span class="th-label">{{ sc.title }}</span>
              <span class="th-arrow" v-if="arrow(sc.field)">{{ arrow(sc.field) }}</span>
            </th>
          </template>
        </tr>
      </thead>

      <tbody>
        <tr v-if="rows.length === 0">
          <td class="td empty" :colspan="bodyCols.length">当前筛选条件下没有库存数据</td>
        </tr>
        <tr v-for="(row, ri) in rows" :key="(row.sku as string) + '-' + ri" class="tr">
          <template v-for="(col, ci) in bodyCols" :key="col.field + '-' + ci">
            <!-- 身份列（冻结） -->
            <td
              v-if="col.kind === 'identity'"
              class="td td-id"
              :class="{ 'td-sku': col.field === 'sku', 'td-name': col.field === 'sku_name' }"
              :style="{ left: identityLeft[ci] + 'px', width: IDENTITY_DEFS[ci].width + 'px', minWidth: IDENTITY_DEFS[ci].width + 'px' }"
            >
              <span v-if="col.field === 'shelf_status' && shelfTag(row[col.field])"
                    class="tag" :class="shelfTag(row[col.field])!.cls">
                {{ shelfTag(row[col.field])!.text }}
              </span>
              <span v-else-if="col.field === 'sku_name'" class="name">{{ fmtNum(row[col.field]) }}</span>
              <span v-else>{{ fmtNum(row[col.field]) }}</span>
            </td>
            <!-- 数值列 -->
            <td v-else class="td td-num" :class="{ 'td-nat': col.kind === 'national' }">
              {{ fmtNum(row[col.field]) }}
            </td>
          </template>
        </tr>
      </tbody>

      <tfoot>
        <tr class="tf">
          <td
            v-for="(c, i) in IDENTITY_DEFS"
            :key="'ft-' + c.field"
            class="td td-id td-total"
            :style="{ left: identityLeft[i] + 'px', width: c.width + 'px', minWidth: c.width + 'px' }"
          >
            {{ i === 0 ? "合计" : "—" }}
          </td>
          <template v-for="g in groups" :key="'fg-' + g.metric.key">
            <td
              v-for="sc in g.subCols"
              :key="'ft-' + sc.field"
              class="td td-num td-total"
              :class="{ 'td-nat': g.metric.kind === 'national' }"
            >
              {{ totalFor(g, sc) }}
            </td>
          </template>
        </tr>
      </tfoot>
    </table>
  </div>
</template>

<style scoped>
.mt-scroll {
  overflow: auto;
  max-height: calc(100vh - 320px);
  min-height: 320px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.mt {
  border-collapse: separate;
  border-spacing: 0;
  width: max-content;
  min-width: 100%;
  font-size: 12.5px;
}
/* ---- 表头 ---- */
.th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-weight: 600;
  text-align: center;
  padding: 9px 8px;
  border-bottom: 1px solid var(--color-border);
  border-right: 1px solid var(--color-border-4);
  white-space: nowrap;
  user-select: none;
}
.th-group {
  background: var(--color-surface-3);
  color: var(--color-text-strong);
}
.th-city {
  font-weight: 500;
  color: var(--color-text-4);
  font-size: 11.5px;
}
/* 身份列表头：顶层 + 冻结，z-index 最高 */
.th-id {
  position: sticky;
  top: 0;
  left: 0;
  z-index: 3;
  background: var(--color-surface-2);
  text-align: left;
}
.th-sortable {
  cursor: pointer;
}
.th-sortable:hover {
  color: var(--color-brand);
}
.th-sortable.active {
  color: var(--color-brand);
}
.th-label {
  display: inline-block;
  vertical-align: middle;
}
.th-arrow {
  margin-left: 3px;
  font-size: 9px;
  color: var(--color-brand);
}
/* ---- 单元格 ---- */
.td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--color-border-4);
  border-right: 1px solid var(--color-border-4);
  white-space: nowrap;
  color: var(--color-text-2);
}
.td-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.td-nat {
  background: var(--color-surface-3);
}
.td-id {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--color-surface);
  text-align: left;
  color: var(--color-text-3);
}
.td-sku {
  color: var(--color-text-4);
  font-variant-numeric: tabular-nums;
}
.td-name {
  color: var(--color-text-strong);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tr:hover .td {
  background: var(--color-surface-2);
}
.tr:hover .td-id {
  background: var(--color-surface-2);
}
.empty {
  text-align: center;
  color: var(--color-text-5);
  padding: 40px 0;
}
/* ---- 合计行 ---- */
.tf .td {
  position: sticky;
  bottom: 0;
  background: var(--color-surface-2);
  font-weight: 600;
  color: var(--color-text-strong);
  border-top: 1px solid var(--color-border);
}
.tf .td-id {
  z-index: 1;
  background: var(--color-surface-2);
}
.tf .td-nat {
  background: var(--color-surface-3);
}
/* ---- 上下柜标签 ---- */
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  line-height: 1.6;
}
.tag.up {
  color: #16a34a;
  background: #ecfdf3;
}
.tag.down {
  color: #dc2626;
  background: #fef2f2;
}
.tag.neutral {
  color: var(--color-text-4);
  background: var(--color-surface-2);
}
</style>
