<script setup lang="ts">
// 库存概览「结构分布卡」（2026-10 定稿：上汇总 + 下店铺矩阵，纯文字）。
//
// 设计要点：
// - 上下布局：顶部汇总（标题 + 主数字 + 单位），下方「店铺小矩阵」；
// - 矩阵：列头是店铺名（可点筛选），行名在行首且绝不截断（nowrap，
//   卡片高度自适应——行名长（如「生活电器配件」）或店铺多时整卡变高）；
// - 数字列用 minmax(34px, 1fr) 均分填满卡片宽度，右侧不留空位；
// - 纯文字无装饰：无色条、无底色，0 值淡显；颜色 text-2 起；
// - 联动只做店铺层：点矩阵列头店铺名 → 抛 filter-shop，父级改店铺筛选；
// - 数据源 /api/inventory/distributions（后端已按 (店铺, SKU) 去重并带店铺拆分）。
import { computed } from "vue";
import type { InventoryDistributions } from "../../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 结构分布数据；为空时卡片显示占位 */
  dist: InventoryDistributions | null;
  /** 当前选中的店铺（高亮 + 再次点击取消） */
  activeShop?: string;
}>();

/** 点击店铺 → 通知父级按该店铺过滤（再次点击同一店铺 = 取消）。 */
const emit = defineEmits<{ (e: "filter-shop", shop: string): void }>();

/** 店铺色板：列头小色点用（与矩阵表风格一致，跨卡可对照）。 */
const SHOP_COLORS = ["#2563eb", "#7c3aed", "#0891b2", "#db2777", "#ea580c", "#65a30d"];

/** 店铺 → 颜色（按 scope.shops 顺序稳定映射）。 */
function shopColor(shop: string): string {
  const i = (props.dist?.scope.shops ?? []).indexOf(shop);
  return SHOP_COLORS[(i < 0 ? 0 : i) % SHOP_COLORS.length];
}

/** 上下柜状态列表（按数量降序）。 */
const statusList = computed<{ name: string; count: number }[]>(() => {
  const t = props.dist?.shelf.total ?? {};
  return Object.entries(t)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);
});

/** 上柜 / 下柜状态名与计数（无对应状态时 0；兼容「已下柜」）。 */
const upStatus = computed<string | null>(
  () => statusList.value.find((s) => s.name.includes("上"))?.name ?? null,
);
const downStatus = computed<string | null>(
  () => statusList.value.find((s) => s.name.includes("下"))?.name ?? null,
);
const upCount = computed(() => statusList.value.find((s) => s.name === upStatus.value)?.count ?? 0);
const downCount = computed(() => statusList.value.find((s) => s.name === downStatus.value)?.count ?? 0);

/** 店铺列（列顺序 = scope.shops）。 */
const shopColumns = computed<string[]>(() => props.dist?.scope.shops ?? []);

/** SKU 覆盖：店铺 → { sku_count, share } 映射。 */
const skuByShop = computed<Record<string, { sku_count: number; share: number }>>(() => {
  const m: Record<string, { sku_count: number; share: number }> = {};
  for (const it of props.dist?.sku.by_shop ?? []) m[it.shop] = it;
  return m;
});

/** 上下柜：店铺 → 状态计数映射。 */
const shelfByShop = computed<Record<string, Record<string, number>>>(() => {
  const m: Record<string, Record<string, number>> = {};
  for (const r of props.dist?.shelf.by_shop ?? []) m[r.shop] = r.shelf;
  return m;
});

/** 类目行：含每店数量。 */
const categoryRows = computed(() =>
  (props.dist?.category.items ?? []).map((it) => ({
    ...it,
    cells: shopColumns.value.map((s) => it.by_shop[s] ?? 0),
  })),
);

/** 销售员行：同上。 */
const sellerRows = computed(() =>
  (props.dist?.seller.items ?? []).map((it) => ({
    ...it,
    cells: shopColumns.value.map((s) => it.by_shop[s] ?? 0),
  })),
);

/** 千分位。 */
function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString("zh-CN");
}

/** 百分比。 */
function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

/** 点击店铺：同一店铺再点一次 = 取消筛选。 */
function onShopClick(shop: string): void {
  emit("filter-shop", props.activeShop === shop ? "" : shop);
}
</script>

<template>
  <div class="dist-grid" data-testid="inv-dist-cards">
    <!-- 卡 1：SKU -->
    <div class="dcard" data-testid="dist-card-sku">
      <div class="dc-top">
        <span class="dc-title">
          <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 12l9 4 9-4"/></svg>
          SKU
        </span>
        <span class="dc-hero"><b class="dc-num">{{ fmt(dist?.sku.total) }}</b><span class="dc-unit">{{ dist?.scope.shops.length ?? 0 }} 店铺</span></span>
      </div>
      <div class="dm" :style="{ '--cols': `auto repeat(${shopColumns.length}, minmax(34px, 1fr))` }">
        <span class="dm-h dm-corner"></span>
        <span v-for="s in shopColumns" :key="'h-' + s" class="dm-h click" :class="{ active: activeShop === s }" @click="onShopClick(s)">
          <i class="shopdot" :style="{ background: shopColor(s) }"></i>{{ s }}
        </span>
        <span class="dm-r">SKU 数</span>
        <span v-for="s in shopColumns" :key="'n-' + s" class="dm-v">{{ fmt(skuByShop[s]?.sku_count ?? 0) }}</span>
        <span class="dm-r">占比</span>
        <span v-for="s in shopColumns" :key="'p-' + s" class="dm-v">{{ skuByShop[s] ? pct(skuByShop[s].share) : "—" }}</span>
      </div>
    </div>

    <!-- 卡 2：上下柜（223/166 等大、黑色） -->
    <div class="dcard" data-testid="dist-card-shelf">
      <div class="dc-top">
        <span class="dc-title">
          <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 21h18"/><rect x="5" y="3" width="14" height="12" rx="1"/></svg>
          上下柜
        </span>
        <span class="dc-hero">
          <span class="dc-pair"><b class="dc-num">{{ fmt(upCount) }}</b><span class="dc-tag">{{ upStatus ?? "上柜" }}</span></span>
          <span class="dc-pair"><b class="dc-num">{{ fmt(downCount) }}</b><span class="dc-tag">{{ downStatus ?? "下柜" }}</span></span>
        </span>
      </div>
      <div class="dm" :style="{ '--cols': `auto repeat(${shopColumns.length}, minmax(34px, 1fr))` }">
        <span class="dm-h dm-corner"></span>
        <span v-for="s in shopColumns" :key="'h-' + s" class="dm-h click" :class="{ active: activeShop === s }" @click="onShopClick(s)">
          <i class="shopdot" :style="{ background: shopColor(s) }"></i>{{ s }}
        </span>
        <span class="dm-r">{{ upStatus ?? "上柜" }}</span>
        <span v-for="s in shopColumns" :key="'u-' + s" class="dm-v">{{ fmt(shelfByShop[s]?.[upStatus ?? ""] ?? 0) }}</span>
        <span class="dm-r">{{ downStatus ?? "下柜" }}</span>
        <span v-for="s in shopColumns" :key="'d-' + s" class="dm-v">{{ fmt(shelfByShop[s]?.[downStatus ?? ""] ?? 0) }}</span>
      </div>
    </div>

    <!-- 卡 3：三级类目 -->
    <div class="dcard" data-testid="dist-card-category">
      <div class="dc-top">
        <span class="dc-title">
          <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>
          三级类目
        </span>
        <span class="dc-hero"><b class="dc-num">{{ fmt(dist?.category.count) }}</b><span class="dc-unit">覆盖 {{ shopColumns.length }} 店铺</span></span>
      </div>
      <div class="dm" :style="{ '--cols': `auto repeat(${shopColumns.length}, minmax(34px, 1fr))` }">
        <span class="dm-h dm-corner"></span>
        <span v-for="s in shopColumns" :key="'h-' + s" class="dm-h click" :class="{ active: activeShop === s }" @click="onShopClick(s)">
          <i class="shopdot" :style="{ background: shopColor(s) }"></i>{{ s }}
        </span>
        <template v-for="(row, ri) in categoryRows" :key="row.category">
          <span class="dm-r" :class="{ 'no-border': ri === 0 }" :title="row.category">{{ row.category }}</span>
          <span
            v-for="(c, i) in row.cells"
            :key="'c-' + row.category + '-' + i"
            class="dm-v"
            :class="{ 'no-border': ri === 0, zero: !(c > 0) }"
          >{{ c > 0 ? fmt(c) : "0" }}</span>
        </template>
      </div>
    </div>

    <!-- 卡 4：销售员 -->
    <div class="dcard" data-testid="dist-card-seller">
      <div class="dc-top">
        <span class="dc-title">
          <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5"/><path d="M16 11h5M18.5 8.5v5"/></svg>
          销售员
        </span>
        <span class="dc-hero"><b class="dc-num">{{ fmt(dist?.seller.person_count) }}</b><span class="dc-unit">按负责 SKU 数</span></span>
      </div>
      <div class="dm" :style="{ '--cols': `auto repeat(${shopColumns.length}, minmax(34px, 1fr))` }">
        <span class="dm-h dm-corner"></span>
        <span v-for="s in shopColumns" :key="'h-' + s" class="dm-h click" :class="{ active: activeShop === s }" @click="onShopClick(s)">
          <i class="shopdot" :style="{ background: shopColor(s) }"></i>{{ s }}
        </span>
        <template v-for="(row, ri) in sellerRows" :key="row.seller">
          <span class="dm-r" :class="{ 'no-border': ri === 0 }" :title="row.seller">{{ row.seller }}</span>
          <span
            v-for="(c, i) in row.cells"
            :key="'s-' + row.seller + '-' + i"
            class="dm-v"
            :class="{ 'no-border': ri === 0, zero: !(c > 0) }"
          >{{ c > 0 ? fmt(c) : "0" }}</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- 网格：4 卡横排（等高 stretch，行数最多的卡决定整块高度） ---- */
.dist-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
/* ---- 卡片：上汇总 + 下矩阵（高度自适应，不截断任何文字） ---- */
.dcard {
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: 10px;
  padding: 10px 12px 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
/* ---- 顶部汇总：标题左 + 主数字右 ---- */
.dc-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.dc-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-2);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.dc-title .ic {
  width: 13px;
  height: 13px;
  color: var(--color-brand);
}
.dc-hero {
  display: inline-flex;
  align-items: baseline;
  gap: 10px;
  margin-left: auto;
}
.dc-pair {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
}
/* 汇总数字：不加粗、缩小（2026-10 反馈：21px/700 太大太重） */
.dc-num {
  font-size: 16px;
  font-weight: 500;
  color: var(--color-text);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.dc-tag {
  font-size: 11px;
  color: var(--color-text-3);
  white-space: nowrap;
}
.dc-unit {
  font-size: 11.5px;
  color: var(--color-text-3);
  white-space: nowrap;
}
/* ---- 店铺小矩阵（纯文字，无装饰；列头右对齐与数字齐） ---- */
.dm {
  display: grid;
  grid-template-columns: var(--cols);
  gap: 1px 8px;
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  border-top: 1px dashed var(--color-border-2);
  padding-top: 7px;
}
.dm-corner {
  cursor: default;
}
.dm-h {
  color: var(--color-text-3);
  font-weight: 600;
  padding: 1px 2px;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  cursor: pointer;
  border-radius: 3px;
  user-select: none;
}
.dm-h:hover {
  background: var(--color-brand-tint);
  color: var(--color-brand-dark);
}
.dm-h.active {
  background: var(--color-brand);
  color: #fff;
}
.shopdot {
  width: 7px;
  height: 7px;
  border-radius: 3px;
  flex: none;
}
/* 行名：绝不截断（nowrap，卡片变高也不藏字） */
.dm-r {
  color: var(--color-text-2);
  padding: 1px 2px;
  white-space: nowrap;
  max-width: none;
  overflow: visible;
  border-bottom: 1px solid var(--color-border-4);
}
.dm-r.no-border {
  border-bottom: none;
}
.dm-v {
  color: var(--color-text-2);
  font-weight: 600;
  text-align: right;
  padding: 1px 2px;
  border-bottom: 1px solid var(--color-border-4);
}
.dm-v.no-border {
  border-bottom: none;
}
/* 0 值淡显 */
.dm-v.zero {
  color: var(--color-text-4);
  font-weight: 400;
}
</style>
