<script setup lang="ts">
// 库存 KPI 卡片：展示当前筛选范围的关键聚合（来自 /api/inventory/summary）。
// 视觉：白色卡片 + 极弱阴影 + 克制蓝色点缀，贴合 StockHub 浅色 SaaS 风格。
import { computed } from "vue";
import type { InventorySummary } from "../../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** KPI 聚合数据；为空时卡片显示占位 */
  summary: InventorySummary | null;
  /** 当前筛选描述（店铺 / 日期），用于卡片组标题 */
  scopeLabel: string;
}>();

/**
 * 千分位格式化（整数）。null / undefined → "—"。
 */
function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString("zh-CN");
}

/**
 * 卡片定义：key 取 summary 字段，label 中文名，accent 小色条（克制蓝 / 中性）。
 *
 * @remarks
 * 颜色只作 4px 圆点点缀，不铺大块背景，符合「状态色只用在标签、主蓝克制」的规范。
 */
const cards = computed(() => {
  const s = props.summary;
  return [
    { label: "现货库存总量", value: fmt(s?.stock), dot: "#2563eb" },
    { label: "滞销件数", value: fmt(s?.slow), dot: "#f59e0b" },
    { label: "采购未到货", value: fmt(s?.purchase), dot: "#0ea5e9" },
    { label: "近30日出库", value: fmt(s?.out_30), dot: "#16a34a" },
    { label: "全国滞销", value: fmt(s?.nat_slow), dot: "#dc2626" },
    { label: "SKU 数", value: fmt(s?.total_skus), dot: "#64748b" },
  ];
});
</script>

<template>
  <div class="kpi-wrap">
    <div class="kpi-head">
      <span class="kpi-scope">{{ scopeLabel }}</span>
      <span class="kpi-hint">数据口径：当前筛选范围合计</span>
    </div>
    <div class="kpi-grid">
      <div
        v-for="c in cards"
        :key="c.label"
        class="kpi-card"
        data-testid="inv-kpi-card"
      >
        <span class="kpi-dot" :style="{ background: c.dot }"></span>
        <div class="kpi-label">{{ c.label }}</div>
        <div class="kpi-value">{{ c.value }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kpi-wrap {
  margin-bottom: 16px;
}
.kpi-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.kpi-scope {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-strong);
}
.kpi-hint {
  font-size: 11.5px;
  color: var(--color-text-5);
}
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}
.kpi-card {
  position: relative;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 14px 14px 14px 18px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.kpi-dot {
  position: absolute;
  left: 10px;
  top: 18px;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-circle);
}
.kpi-label {
  font-size: 12px;
  color: var(--color-text-4);
  margin-bottom: 8px;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
@media (max-width: 1100px) {
  .kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
