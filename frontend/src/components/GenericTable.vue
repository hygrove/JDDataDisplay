<script setup lang="ts">
// 通用虚拟滚动表格：只渲染可视行，滚动到底自动加载下一页。
// 列由 ColumnDef 配置驱动；特殊列（product）通过具名插槽自定义。
//
// 滚动结构（对齐 style-tech.html 的单容器思路）：
//   .gt 既是纵向也是横向的【唯一滚动容器】，表头与表体处于同一滚动上下文。
//   表头用 position:sticky; top:0 钉在顶部；右侧固定列用 position:sticky; right:0 钉在右缘。
//   两者共享同一滚动容器，因此横向滚动时「表头 ↔ 固定列 ↔ 数据」始终逐列对齐，
//   且固定列绝不会脱离容器去遮挡整页（早期用 absolute 叠加层 + translateX 同步时才会出现遮挡）。
import { computed, ref } from "vue";
import { useVirtualizer } from "@tanstack/vue-virtual";
import type { ColumnDef } from "../modules";
import { fmtCurrency, fmtNumber, fmtPercent } from "../modules";
import type { MetricKey, Row } from "../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 列定义数组（由 ColumnDef 配置驱动，顺序即展示顺序） */
  columns: ColumnDef[];
  /** 当前已加载的行数据；虚拟滚动只渲染其中的可视部分 */
  rows: Row[];
  /** 过滤后的总条数（用于底部「已加载全部」提示，以及是否还有更多） */
  total: number;
  /** 是否正在加载（加载中不再触发 reach-end，避免重复请求） */
  loading: boolean;
  /** 当前排序字段，用于表头高亮与箭头方向 */
  sortBy: string;
  /** 当前排序方向 */
  sortOrder: "asc" | "desc";
  /** 行高（px），虚拟滚动据此估算总高度；默认 196（商品卡较矮胖） */
  rowHeight?: number;
}>();

/**
 * 组件事件。
 *
 * - `sort`：点击可排序列时触发，参数为列 key 与新的排序方向（由组件内部决定升降序切换）；
 * - `reach-end`：滚动接近底部（距底 < 300px）时触发，父组件据此加载下一页。
 */
const emit = defineEmits<{
  (e: "sort", key: string, order: "asc" | "desc"): void;
  (e: "reach-end"): void;
}>();

// 唯一滚动容器
const scrollRef = ref<HTMLElement | null>(null);
const rowHeight = computed(() => props.rowHeight ?? 196);
// 表格总列宽：表头与表体共用，保证两侧列边界完全一致
const totalWidth = computed(() => props.columns.reduce((a, c) => a + c.width, 0));

/**
 * 计算每个右侧固定列的 `right` 偏移量（key -> px）。
 *
 * @remarks
 * 多个固定列并排时，不能都写 right:0（会重叠成一列）。
 * 正确做法：某列的 right = 它**右侧**所有固定列宽度之和。
 * 所以这里从最右一列开始倒序累加，越靠右的列偏移越小。
 *
 * @returns {Map<string, number>} 列 key -> right 偏移像素值；没有固定列时为空 Map。
 * @example
 * ```ts
 * // 若只有 analysis 列固定：Map { "analysis" => 0 }
 * // 若 analysis(110) + ops(80) 都固定：Map { "ops" => 0, "analysis" => 80 }
 * ```
 */
const stickyRightOffset = computed(() => {
  const stickies = props.columns.filter((c) => c.sticky === "right");
  const map = new Map<string, number>();
  let acc = 0;
  // 倒序遍历：先处理最右侧的列（偏移 0），再往左累加
  for (let i = stickies.length - 1; i >= 0; i--) {
    map.set(stickies[i].key, acc);
    acc += stickies[i].width;
  }
  return map;
});

const virtualizer = useVirtualizer(
  computed(() => ({
    count: props.rows.length,
    getScrollElement: () => scrollRef.value,
    estimateSize: () => rowHeight.value,
    overscan: 8,
  }))
);

/**
 * 滚动回调：接近底部时通知父组件加载下一页（无限滚动）。
 *
 * @remarks
 * 两个守卫缺一不可：
 * - `props.loading` 时不触发：否则一次触底会连续 emit 多次，打出并发重复请求；
 * - 预留 300px 提前量：等真的滚到底再加载会看到明显卡顿，提前一屏开始拉更顺滑。
 *
 * @returns {void} 无返回值；满足条件时 emit "reach-end"。
 * @example
 * ```ts
 * // 由 .gt 容器的 scroll 事件触发，父组件：@reach-end="store.loadMore()"
 * ```
 */
function onScroll() {
  const el = scrollRef.value;
  if (!el || props.loading) return;
  // 距底部不足 300px 即认为「快到底了」
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 300) {
    emit("reach-end");
  }
}

/**
 * 点击表头切换排序：三态循环「未选中 -> 降序 -> 升序 -> 降序」。
 *
 * @param {ColumnDef} col - 被点击的列定义；sortable 为 false 的列直接忽略。
 * @returns {void} 无返回值；通过 emit("sort") 把新排序交给父组件（本组件不持有排序状态）。
 * @example
 * ```ts
 * // 首次点「成交金额」-> emit sort(amount, desc)
 * // 再点一次      -> emit sort(amount, asc)
 * ```
 */
function toggleSort(col: ColumnDef) {
  // 不可排序的列（序号、商品信息、操作列）点了没反应
  if (!col.sortable) return;
  if (props.sortBy !== col.key) emit("sort", col.key, "desc");
  else if (props.sortOrder === "desc") emit("sort", col.key, "asc");
  else emit("sort", col.key, "desc");
}

/**
 * 按列类型把单元格的原始值格式化成展示文本。
 *
 * @param {Row} row - 当前行数据（只读取 row.metrics，区间模式的 days 由外部另处理）。
 * @param {ColumnDef} col - 列定义，其 type 决定格式化方式，key 决定取哪个指标。
 * @returns {string} 格式化后的文本；值为 null 时由各 fmt 函数返回 "0" / "¥0.00" / "0.00%"。
 * @throws 无。取不到字段时 v 为 undefined，格式化函数会按缺失处理。
 * @example
 * ```ts
 * cellValue(row, { key: "amount", type: "currency" } as ColumnDef); // "¥1,234.50"
 * ```
 */
function cellValue(row: Row, col: ColumnDef): string {
  const v = row.metrics[col.key as MetricKey];
  switch (col.type) {
    case "currency":
      return fmtCurrency(v);
    case "percent":
      return fmtPercent(v);
    default:
      // number 与 decimal 都按整数/数字格式处理
      return fmtNumber(v);
  }
}
</script>

<template>
  <div class="gt" ref="scrollRef" @scroll="onScroll">
    <!-- 表头：sticky top:0；固定列 sticky right:0；横纵滚动都在 .gt 内，自动对齐 -->
    <div class="gt-header" :style="{ minWidth: totalWidth + 'px' }">
      <div
        v-for="col in columns"
        :key="col.key"
        class="gt-th"
        :class="[
          `t-${col.type}`,
          { sortable: col.sortable, active: sortBy === col.key, sticky: col.sticky === 'right' },
        ]"
        :style="{
          width: col.width + 'px',
          right: col.sticky === 'right' ? stickyRightOffset.get(col.key) + 'px' : undefined,
        }"
        @click="toggleSort(col)"
      >
        <span class="th-title">{{ col.title }}</span>
        <span v-if="col.sortable" class="arrow">
          {{ sortBy === col.key ? (sortOrder === "desc" ? "↓" : "↑") : "⇅" }}
        </span>
      </div>
    </div>

    <!-- 表体（虚拟滚动） -->
    <div
      class="gt-virt"
      :style="{ height: virtualizer.getTotalSize() + 'px', minWidth: totalWidth + 'px' }"
    >
      <div
        v-for="vRow in virtualizer.getVirtualItems()"
        :key="String(vRow.key)"
        class="gt-row"
        :style="{ transform: `translateY(${vRow.start}px)`, height: rowHeight + 'px' }"
      >
        <div
          v-for="col in columns"
          :key="col.key"
          class="gt-td"
          :class="[`t-${col.type}`, { sticky: col.sticky === 'right' }]"
          :style="{
            width: col.width + 'px',
            right: col.sticky === 'right' ? stickyRightOffset.get(col.key) + 'px' : undefined,
          }"
        >
          <template v-if="col.type === 'index'">{{ vRow.index + 1 }}</template>
          <template v-else-if="col.type === 'product'">
            <slot name="product" :row="rows[vRow.index]" />
          </template>
          <template v-else-if="col.type === 'action'">
            <slot :name="col.key" :row="rows[vRow.index]" />
          </template>
          <template v-else>{{ cellValue(rows[vRow.index], col) }}</template>
        </div>
      </div>
    </div>

    <div v-if="loading" class="gt-foot">加载中…</div>
    <div v-else-if="rows.length === 0" class="gt-foot">暂无数据</div>
    <div v-else-if="rows.length >= total" class="gt-foot">已加载全部 {{ total }} 条</div>
  </div>
</template>

<style scoped>
.gt {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  /* 唯一滚动容器：横纵都在此滚动，使表头 sticky-top 与固定列 sticky-right 共享同一上下文 */
  overflow: auto;
  height: 100%;
  position: relative;
}
/* 表头：钉在容器顶部，横向滚动时与表体一并移动 */
.gt-header {
  display: flex;
  position: sticky;
  top: 0;
  z-index: 4;
  background: #f8fafc;
  border-bottom: 1px solid #e5e7eb;
}
.gt-th {
  flex: none;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  padding: 12px 10px;
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  user-select: none;
  white-space: nowrap;
}
/* 列间竖向分隔线（表头） */
.gt-th:not(:last-child) {
  border-right: 1px solid #e2e8f0;
}
/* 数值类表头右对齐，与下方数值一致 */
.gt-th.t-number,
.gt-th.t-currency,
.gt-th.t-percent {
  justify-content: flex-end;
}
.gt-th.sortable {
  cursor: pointer;
}
.gt-th.sortable:hover {
  color: #e1251b;
}
.gt-th.active {
  color: #e1251b;
}
.arrow {
  font-size: 11px;
  margin-left: 3px;
}
.gt-virt {
  position: relative;
}
.gt-row {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  align-items: center;
  border-bottom: 1px solid #f1f5f9;
  width: 100%;
  box-sizing: border-box;
}
.gt-row:hover {
  background: #f8fafc;
}
.gt-td {
  flex: none;
  box-sizing: border-box;
  padding: 8px 10px;
  font-size: 14px;
  color: #1e293b;
  overflow: hidden;
}
/* 列间竖向分隔线（数据） */
.gt-td:not(:last-child) {
  border-right: 1px solid #eef2f7;
}
.t-number,
.t-currency,
.t-percent {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.gt-foot {
  padding: 14px;
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
}
/* ---------- 右侧固定列（表头与表体共用同一套实现） ---------- */
.gt-th.sticky {
  position: sticky;
  right: 0;
  z-index: 5;
  justify-content: center;
  background: #f8fafc;
  border-left: 1px solid #e2e8f0;
  box-shadow: -6px 0 10px rgba(15, 23, 42, 0.06);
}
.gt-td.sticky {
  position: sticky;
  right: 0;
  z-index: 2;
  background: #fff;
  text-align: center;
  border-left: 1px solid #eef2f7;
  box-shadow: -6px 0 10px rgba(15, 23, 42, 0.06);
}
.gt-row:hover .gt-td.sticky {
  background: #f8fafc;
}
</style>
