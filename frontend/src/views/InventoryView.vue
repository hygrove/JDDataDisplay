<script setup lang="ts">
// 库存管理主视图：加载 meta → 拉取矩阵明细与 KPI → 渲染筛选栏 + KPI 卡 + 指标配置 + 矩阵表。
//
// 配色：本视图根节点把 --color-brand 等重写为克制蓝（#2563eb），与全站红品牌隔离，
// 严格对齐 preview/inventory_ui 的 StockHub 浅色 SaaS 风格（白底 / 极浅灰 / 弱阴影 / 单一蓝点缀）。
import { computed, onMounted, ref } from "vue";
import { fetchInventoryMeta, fetchInventoryRows, fetchInventorySummary } from "../api";
import { useInventoryMetricsStore } from "../stores/inventoryMetrics";
import type {
  InventoryMeta,
  InventoryMetric,
  InventoryPagedRows,
  InventorySummary,
} from "../types";
import InventoryKpiCards from "../components/inventory/InventoryKpiCards.vue";
import InventoryMetricPanel from "../components/inventory/InventoryMetricPanel.vue";
import InventoryMatrixTable from "../components/inventory/InventoryMatrixTable.vue";

/** 库存元信息（指标清单 + 城市/店铺/日期）。 */
const meta = ref<InventoryMeta | null>(null);
/** 明细 + 合计响应。 */
const data = ref<InventoryPagedRows | null>(null);
/** KPI 聚合。 */
const summary = ref<InventorySummary | null>(null);
/** 加载中 / 错误。 */
const loading = ref(false);
const error = ref<string | null>(null);

/** 筛选条件。 */
const shop = ref("");
const date = ref("");
const keyword = ref("");
const sortBy = ref<string | null>(null);
const sortOrder = ref<"asc" | "desc">("desc");
const page = ref(1);
const pageSize = ref(50);

const metricsStore = useInventoryMetricsStore();

/**
 * 传给「指标配置」面板与矩阵表的「已勾选非锁定指标 key」。
 * 未手动勾选过时回落到后端 default_visible（与 store 的语义一致）。
 */
const chosenKeys = computed<string[]>(() => {
  if (!meta.value) return [];
  const saved = metricsStore.keys();
  if (saved) return saved;
  return meta.value.metrics.filter((m) => !m.locked && m.default_visible).map((m) => m.key);
});

/**
 * 矩阵表要渲染的可见「非身份」指标（按 meta 顺序），身份列由表内置。
 */
const visibleMetrics = computed<InventoryMetric[]>(() => {
  if (!meta.value) return [];
  const set = new Set(chosenKeys.value);
  return meta.value.metrics.filter((m) => !m.locked && set.has(m.key));
});

/** 当前筛选范围描述（KPI 卡片标题用）。 */
const scopeLabel = computed(() => {
  const s = shop.value || "全部店铺";
  const d = date.value || "全部日期";
  return `${s} · ${d}`;
});

/** 总页数。 */
const totalPages = computed(() =>
  data.value ? Math.max(1, Math.ceil(data.value.total / pageSize.value)) : 1,
);

/** 首次加载 meta，并把日期默认设为最新快照。 */
async function loadMeta(): Promise<void> {
  meta.value = await fetchInventoryMeta();
  if (!date.value && meta.value.dates.length) date.value = meta.value.dates[0];
}

/** 拉取明细 + KPI（并行）。筛选/排序/翻页变化时调用。 */
async function loadData(): Promise<void> {
  if (!meta.value) return;
  loading.value = true;
  error.value = null;
  try {
    const [rows, summ] = await Promise.all([
      fetchInventoryRows({
        shop: shop.value,
        date: date.value,
        keyword: keyword.value,
        sort_by: sortBy.value ?? undefined,
        sort_order: sortOrder.value,
        page: page.value,
        page_size: pageSize.value,
      }),
      fetchInventorySummary(shop.value, date.value),
    ]);
    data.value = rows;
    summary.value = summ;
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

/** 点击列头排序：同一列切换升/降序，换列默认降序。 */
function onSort(field: string): void {
  if (sortBy.value === field) {
    sortOrder.value = sortOrder.value === "asc" ? "desc" : "asc";
  } else {
    sortBy.value = field;
    sortOrder.value = "desc";
  }
  page.value = 1;
  void loadData();
}

/** 筛选（店铺/日期）变化：回到第一页并重拉。 */
function onFilterChange(): void {
  page.value = 1;
  void loadData();
}

/** 关键词输入：防抖 300ms 后重拉。 */
let kwTimer: number | undefined;
function onKeywordInput(): void {
  page.value = 1;
  if (kwTimer) clearTimeout(kwTimer);
  kwTimer = window.setTimeout(() => void loadData(), 300);
}

/** 翻页。 */
function onPageDelta(d: number): void {
  const np = page.value + d;
  if (np < 1 || np > totalPages.value) return;
  page.value = np;
  void loadData();
}

/** 指标勾选变化：回写 store（自动持久化）；列是同一批行的投影，无需重拉数据。 */
function onKeys(v: string[]): void {
  metricsStore.set(v);
}

onMounted(async () => {
  try {
    await loadMeta();
    await loadData();
  } catch (e) {
    error.value = String(e);
  }
});
</script>

<template>
  <div class="inv" data-testid="inv-view">
    <header class="inv-head">
      <div>
        <h1 class="inv-title">库存管理</h1>
        <p class="inv-sub">多门店 · 多城市现货 / 周转 / 出库矩阵</p>
      </div>
    </header>

    <InventoryKpiCards :summary="summary" :scopeLabel="scopeLabel" />

    <div class="inv-toolbar">
      <div class="inv-filters">
        <label class="inv-field">
          <span>店铺</span>
          <select v-model="shop" data-testid="inv-shop" @change="onFilterChange">
            <option value="">全部店铺</option>
            <option v-for="s in meta?.shops ?? []" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="inv-field">
          <span>快照日期</span>
          <select v-model="date" data-testid="inv-date" @change="onFilterChange">
            <option value="">全部日期</option>
            <option v-for="d in meta?.dates ?? []" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="inv-field inv-search">
          <span>搜索</span>
          <input
            v-model="keyword"
            data-testid="inv-keyword"
            type="text"
            placeholder="SKU / 商品名称 / SPU"
            @input="onKeywordInput"
          />
        </label>
      </div>
      <div class="inv-tools">
        <InventoryMetricPanel
          v-if="meta"
          :metrics="meta.metrics"
          :keys="chosenKeys"
          @update:keys="onKeys"
        />
      </div>
    </div>

    <div v-if="error" class="inv-error" data-testid="inv-error">{{ error }}</div>

    <div v-if="data" class="inv-table-meta">
      <span class="inv-count">共 <b>{{ data.total }}</b> 条</span>
      <span class="inv-pageinfo">第 {{ page }} / {{ totalPages }} 页</span>
      <div class="inv-pager">
        <button data-testid="inv-prev" :disabled="page <= 1" @click="onPageDelta(-1)">上一页</button>
        <button data-testid="inv-next" :disabled="page >= totalPages" @click="onPageDelta(1)">下一页</button>
      </div>
    </div>

    <InventoryMatrixTable
      v-if="meta"
      :visibleMetrics="visibleMetrics"
      :cities="meta.cities"
      :rows="data?.rows ?? []"
      :totals="data?.totals ?? {}"
      :cityTotals="data?.city_totals ?? {}"
      :sortBy="sortBy"
      :sortOrder="sortOrder"
      @sort="onSort"
    />

    <div v-if="loading" class="inv-loading" data-testid="inv-loading">加载中…</div>
  </div>
</template>

<style scoped>
/* 库存视图局部覆盖设计为「克制蓝」主题，与全站红品牌隔离 */
.inv {
  --color-brand: #2563eb;
  --color-brand-dark: #1d4ed8;
  --color-brand-darker: #1e40af;
  --color-brand-tint: #eff6ff;
  --color-brand-tint-2: #f0f6ff;
  --color-brand-tint-3: #f5f8ff;
  --color-brand-tint-4: #f5f8ff;
  --color-brand-border: #bfdbfe;
  --color-brand-border-2: #dbeafe;
  --color-bg: #f6f8fb;
  --color-surface: #ffffff;
  --color-surface-2: #f8fafc;
  --color-surface-3: #fafbfc;
  --color-border: #e5e7eb;
  --color-border-2: #eef2f7;
  --color-border-4: #f1f5f9;

  background: var(--color-bg);
  border-radius: var(--radius-lg);
  padding: 18px;
  min-height: 100%;
  box-sizing: border-box;
}
.inv-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 16px;
}
.inv-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text-strong);
  letter-spacing: 0.5px;
}
.inv-sub {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--color-text-5);
}
.inv-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.inv-filters {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}
.inv-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-4);
}
.inv-field select,
.inv-field input {
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.inv-field select:focus,
.inv-field input:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.inv-search input {
  width: 220px;
}
.inv-tools {
  display: flex;
  align-items: center;
}
.inv-table-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 10px;
  font-size: 12.5px;
  color: var(--color-text-4);
}
.inv-count b {
  color: var(--color-text-strong);
}
.inv-pager {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.inv-pager button {
  height: 30px;
  padding: 0 14px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.inv-pager button:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.inv-pager button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.inv-error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md);
  padding: 10px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}
.inv-loading {
  margin-top: 12px;
  font-size: 13px;
  color: var(--color-text-5);
}
</style>
