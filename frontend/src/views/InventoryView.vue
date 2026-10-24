<script setup lang="ts">
// 库存管理主视图：加载 meta → 拉取矩阵明细与 KPI → 渲染筛选栏 + KPI 卡 + 指标配置 + 矩阵表。
//
// 配色：本视图根节点把 --color-brand 等重写为克制蓝（#2563eb），与全站红品牌隔离，
// 严格对齐 preview/inventory_ui 的 StockHub 浅色 SaaS 风格（白底 / 极浅灰 / 弱阴影 / 单一蓝点缀）。
import { computed, onMounted, ref } from "vue";
import {
  fetchInventoryAlerts,
  fetchInventoryDistributions,
  fetchInventoryMeta,
  fetchInventoryRows,
  fetchInventorySlowCities,
  putAlertThresholds,
} from "../api";
import { useInventoryMetricsStore } from "../stores/inventoryMetrics";
import type {
  InventoryAlertsSummary,
  InventoryDistributions,
  InventoryFilters,
  InventoryFilterOptions,
  InventoryMeta,
  InventoryMetric,
  InventoryPagedRows,
  InventorySlowCity,
} from "../types";
import AlertCityStrip from "../components/inventory/AlertCityStrip.vue";
import InventoryDistCards from "../components/inventory/InventoryDistCards.vue";
import InventoryMetricPanel from "../components/inventory/InventoryMetricPanel.vue";
import InventoryMatrixTable from "../components/inventory/InventoryMatrixTable.vue";
import RestockPanel from "../components/inventory/RestockPanel.vue";

/** 库存元信息（指标清单 + 城市/店铺/日期）。 */
const meta = ref<InventoryMeta | null>(null);
/** 明细 + 合计响应。 */
const data = ref<InventoryPagedRows | null>(null);
/** 结构分布（概览卡数据源）。 */
const dist = ref<InventoryDistributions | null>(null);
/** 全城市滞销聚合（不限 8 城，矩阵表列头悬浮展示）。 */
const slowCities = ref<InventorySlowCity[]>([]);
/** 8 城预警汇总（顶部预警条 + 矩阵着色共用；null = 未加载/失败，不着色）。 */
const alerts = ref<InventoryAlertsSummary | null>(null);
/** 阈值保存中（预警条输入禁用态）。 */
const alertsSaving = ref(false);
/** 当前视图 tab：库存矩阵（默认） / 补货工作台（2026-10 新增，变体 B）。 */
const activeTab = ref<"matrix" | "restock">("matrix");

/** 当前是否矩阵视图 / 补货视图（模板按钮高亮用 computed，避免 v-if pane 内
 *  TS 控制流收窄 activeTab 后与其字面量比较报“无重叠”）。 */
const isMatrix = computed<boolean>(() => activeTab.value === "matrix");
const isRestock = computed<boolean>(() => activeTab.value === "restock");
/** 加载中 / 错误。 */
const loading = ref(false);
const error = ref<string | null>(null);

/** 城市码 → 阈值（传给矩阵表做现货列三档着色）。 */
const thresholds = computed<Record<string, number>>(() => {
  const m: Record<string, number> = {};
  for (const c of alerts.value?.cities ?? []) m[c.code] = c.threshold;
  return m;
});

/** 城市码数组（与 meta.cities 同序，取自预警汇总）。 */
const cityCodes = computed<string[]>(() => (alerts.value?.cities ?? []).map((c) => c.code));

/** 筛选条件。 */
const shop = ref("");
const date = ref("");
/** 关键词（已提交）：防抖后的值，矩阵表与补货清单共用同一份。 */
const keyword = ref("");
/** 关键词输入框的即时值：输入即回显，防抖结束后才提交给后端。 */
const keywordInput = ref("");
const page = ref(1);
const pageSize = ref(50);

/**
 * 矩阵表「信息」列的筛选条件（三级类目 / 销售员 / 上下柜状态，多选）。
 *
 * @remarks
 * 空数组 = 该维度不限。值即后端 inventory_daily 的原始字段值。
 */
const filters = ref<InventoryFilters>({ category: [], seller: [], shelf_status: [] });

/**
 * 矩阵表排序状态（指标 key + 方向）。
 *
 * @remarks
 * 排序由后端在**分页之前**执行（作用于全部行，不是当页 50 条），所以状态必须
 * 放在视图层随每次请求下发；翻页 / 改筛选时保持不变。
 * key 为 null = 未排序；顺序 null → desc → asc → desc（由表头点击推进）。
 */
const sort = ref<{ key: string | null; order: "asc" | "desc" | null }>({
  key: null,
  order: null,
});

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

/** 总页数。 */
const totalPages = computed(() =>
  data.value ? Math.max(1, Math.ceil(data.value.total / pageSize.value)) : 1,
);

/**
 * 分页条里的一项：具体页码，或「省略号」占位（不可点）。
 */
type PageItem = number | "gap";

/**
 * 页码按钮序列：首末页 + 当前页及其左右邻页恒显，中间折叠为省略号。
 *
 * @remarks
 * 折叠不是「只留 1/总页数」那种激进做法：先取 `{1, tp, cur-1, cur, cur+1}`，
 * 相邻两项间距为 1 时不出现省略号、间距为 2 时直接把中间那页补出来，
 * 只有真的被折叠掉 ≥3 页才渲染一个「…」占位。
 * 这样短序列（≤7 页）永远是全量页码，不需要再单独分支。
 */
const pageItems = computed<PageItem[]>(() => {
  const tp = totalPages.value;
  const cur = page.value;
  if (tp <= 1) return [1];
  const picked = new Set<number>([1, tp, cur]);
  if (cur - 1 >= 1) picked.add(cur - 1);
  if (cur + 1 <= tp) picked.add(cur + 1);
  const nums = [...picked].sort((a, b) => a - b);
  const out: PageItem[] = [];
  let prev = 0;
  for (const n of nums) {
    if (prev && n - prev > 1) {
      // 只差 1 页就别放省略号了，直接把这一页列出来更好点
      if (n - prev === 2) out.push(prev + 1);
      else out.push("gap");
    }
    out.push(n);
    prev = n;
  }
  return out;
});

/**
 * 信息列筛选项候选：直接复用概览卡的结构分布（三级类目 / 销售员 / 上下柜状态）。
 *
 * @remarks
 * 结构分布本身已按当前店铺/日期/关键词聚合，正好可当筛选项来源，
 * 不必再单独拉一次接口；空值统一显示为「未标注」（与后端过滤口径一致）。
 */
const filterOptions = computed<InventoryFilterOptions>(() => {
  const d = dist.value;
  return {
    categories: (d?.category.items ?? []).map((i) => i.category),
    sellers: (d?.seller.items ?? []).map((i) => i.seller),
    shelf_statuses: Object.keys(d?.shelf.total ?? {}),
  };
});

/** 拉取 8 城预警汇总（顶部预警条 + 矩阵着色共用）。
 *
 * @remarks
 * 与 loadData 同源筛选（shop/date）；失败不阻断主表（预警条降级为不渲染，
 * 矩阵不着色），由 try/catch 吞掉并在下次筛选变化时重试。
 */
async function loadAlerts(): Promise<void> {
  try {
    alerts.value = await fetchInventoryAlerts(shop.value || undefined, date.value || undefined);
  } catch {
    alerts.value = null;
  }
}

/**
 * 阈值编辑提交（预警条抛出）：PUT 保存后重拉汇总（刷新徽章计数与矩阵着色）。
 *
 * @param {object} v - { code, threshold }：城市码与新阈值。
 */
async function onThreshold(v: { code: string; threshold: number }): Promise<void> {
  alertsSaving.value = true;
  try {
    await putAlertThresholds({ [v.code]: v.threshold });
    await loadAlerts();
  } catch (e) {
    error.value = String(e);
  } finally {
    alertsSaving.value = false;
  }
}

/** 首次加载 meta，并把日期默认设为最新快照。 */
async function loadMeta(): Promise<void> {
  meta.value = await fetchInventoryMeta();
  if (!date.value && meta.value.dates.length) date.value = meta.value.dates[0];
}

/** 拉取明细 + KPI（并行）。筛选/翻页变化时调用。 */
async function loadData(): Promise<void> {
  if (!meta.value) return;
  loading.value = true;
  error.value = null;
  try {
    const [rows, distData, slow] = await Promise.all([
      fetchInventoryRows({
        shop: shop.value,
        date: date.value,
        keyword: keyword.value,
        category: filters.value.category,
        seller: filters.value.seller,
        shelf_status: filters.value.shelf_status,
        sort_by: sort.value.key ?? undefined,
        sort_order: sort.value.order ?? undefined,
        page: page.value,
        page_size: pageSize.value,
      }),
      fetchInventoryDistributions({
        shop: shop.value,
        date: date.value,
        keyword: keyword.value,
      }),
      fetchInventorySlowCities(shop.value, date.value),
    ]);
    data.value = rows;
    dist.value = distData;
    slowCities.value = slow;
    // 预警汇总随筛选联动（不阻塞主表；失败静默，预警条不渲染）
    void loadAlerts();
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

/**
 * 信息列筛选条件变化（矩阵表筛选面板）：回到第一页并重拉。
 *
 * @param {InventoryFilters} v - 新的筛选条件（三级类目 / 销售员 / 上下柜状态）。
 */
function onFilters(v: InventoryFilters): void {
  filters.value = v;
  page.value = 1;
  void loadData();
}

/** 筛选（店铺/日期）变化：回到第一页并重拉。 */
function onFilterChange(): void {
  page.value = 1;
  void loadData();
}

/**
 * 关键词输入：回显立即、提交防抖 300ms。
 *
 * @remarks
 * 输入框的值（keywordInput）即时回显；防抖结束后才写入 keyword 并重拉。
 * keyword 是矩阵表与补货清单**共用**的已提交值——两个视图都只对它做响应，
 * 因此一次输入只发一轮请求（两个视图各一次），不会逐字打接口。
 */
let kwTimer: number | undefined;
function onKeywordInput(e: Event): void {
  keywordInput.value = (e.target as HTMLInputElement).value;
  page.value = 1;
  if (kwTimer) clearTimeout(kwTimer);
  kwTimer = window.setTimeout(() => {
    keyword.value = keywordInput.value;
    void loadData();
  }, 300);
}

/**
 * 跳到指定页并重拉数据（页码按钮 / 上一页 / 下一页 / 跳转选择器共用）。
 *
 * @param {number} p - 目标页码；越界会被夹到 [1, totalPages]，同页则不发请求。
 */
function goPage(p: number): void {
  const np = Math.min(Math.max(1, Math.round(p) || 1), totalPages.value);
  if (np === page.value) return;
  page.value = np;
  void loadData();
}

/** 上一页 / 下一页。 */
function onPageDelta(d: number): void {
  goPage(page.value + d);
}

/** 「跳至 __ 页」选择器：任意页码直达（页码按钮被省略号折叠时的兜底入口）。 */
function onPagePick(e: Event): void {
  goPage(Number((e.target as HTMLSelectElement).value));
}

/**
 * 表头点击排序（矩阵表上报）：回到第一页并按新的排序条件重拉。
 *
 * @remarks
 * 回第一页是必须的——排序一变，「第 1 名」已不在当前页开头，
 * 停在原页码会让人以为排序没生效。
 */
function onSort(v: { key: string | null; order: "asc" | "desc" | null }): void {
  sort.value = { key: v.key, order: v.order };
  page.value = 1;
  void loadData();
}

/** 指标勾选变化：回写 store（自动持久化）；列是同一批行的投影，无需重拉数据。 */
function onKeys(v: string[]): void {
  metricsStore.set(v);
}

/**
 * 概览卡点击店铺 → 联动店铺筛选（只联动店铺这一层）。
 *
 * @param {string} s - 目标店铺名；空串 = 取消筛选。
 */
function onFilterShop(s: string): void {
  shop.value = s;
  onFilterChange();
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
        <p class="inv-sub">多门店 · 多城市现货 / 周转 / 出库矩阵 · 库存预警</p>
      </div>
    </header>

    <!-- 筛选条不再放在这里：矩阵 / 补货两个 pane 各渲染一份，位置随模式不同
         （矩阵在阈值条下方、补货在模式切换条下方），筛选状态 shop/date/keyword
         仍由本组件持有，切 Tab 不丢选择。 -->

    <template v-if="activeTab === 'matrix'">
      <!-- 概览：4 张结构分布卡（点击店铺名/店铺列头 → 联动下方表格按该店铺过滤） -->
      <InventoryDistCards
        :dist="dist"
        :activeShop="shop"
        @filter-shop="onFilterShop"
      />

      <!-- 8 城预警条（2026-10 调整至概览卡下方）：红/黄徽章 + 城市级阈值编辑 + 建议补货合计。
           阈值 PUT 后自动刷新计数与矩阵着色；alerts 未就绪时不渲染（不影响主表）。 -->
      <AlertCityStrip
        v-if="alerts"
        :cities="alerts.cities"
        :saving="alertsSaving"
        @update:threshold="onThreshold"
      />

      <!-- 筛选条（2026-10 调整至阈值条下方）：店铺 / 日期 / 搜索 + 指标配置。
           「指标配置」只对矩阵表有意义，故仅在矩阵 pane 渲染；筛选状态由父级共享。 -->
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
            <span>日期</span>
            <select v-model="date" data-testid="inv-date" @change="onFilterChange">
              <option value="">全部日期</option>
              <option v-for="d in meta?.dates ?? []" :key="d" :value="d">{{ d }}</option>
            </select>
          </label>
          <label class="inv-field inv-search">
            <span>搜索</span>
            <input
              :value="keywordInput"
              data-testid="inv-keyword"
              type="text"
              placeholder="SKU / 商品名称 / SPU"
              @input="onKeywordInput"
            />
          </label>
        </div>
        <div v-if="meta" class="inv-tools">
          <InventoryMetricPanel
            :metrics="meta.metrics"
            :keys="chosenKeys"
            @update:keys="onKeys"
          />
        </div>
      </div>

      <!-- 视图切换（2026-10 新增，原型变体 B）：库存矩阵 / 补货工作台 -->
      <div class="inv-viewtabs">
        <div class="view-tabs">
          <button :class="{ on: isMatrix }" data-testid="inv-tab-matrix" @click="activeTab = 'matrix'">库存矩阵</button>
          <button :class="{ on: isRestock }" data-testid="inv-tab-restock" @click="activeTab = 'restock'">补货工作台</button>
        </div>
        <span v-if="activeTab === 'matrix'" class="viewtabs-legend">
          <i class="sw sw-y"></i>在途（现货不足，在途可补足）
          <i class="sw sw-r"></i>预警（现货+在途 &lt; 阈值）
          <i class="sw sw-off"></i>下柜（不计入预警与补货）
        </span>
        <span v-else class="viewtabs-legend">
          按「有效日均销速 × 期望库存覆盖天数 × (1+安全系数) − 现货库存 − 在途库存」生成每周采购建议，下柜 SKU 已排除。
        </span>
      </div>

    <div v-if="error" class="inv-error" data-testid="inv-error">{{ error }}</div>

    <div v-if="data" class="inv-table-meta" data-testid="inv-table-meta">
      <span class="inv-count">共 <b>{{ data.total }}</b> 条</span>
      <span class="inv-pagesize" data-testid="inv-page-size-hint">每页 <b>{{ pageSize }}</b> 条</span>
      <span class="inv-pageinfo">第 {{ page }} / {{ totalPages }} 页</span>
      <div class="inv-pager" data-testid="inv-pager">
        <button data-testid="inv-prev" :disabled="page <= 1" title="上一页" @click="onPageDelta(-1)">上一页</button>
        <!-- 页码按钮：首末页 + 当前页邻页恒显，其余折叠为省略号 -->
        <template v-for="(it, i) in pageItems" :key="'pg-' + i">
          <span v-if="it === 'gap'" class="inv-page-gap" aria-hidden="true">…</span>
          <button
            v-else
            class="inv-page"
            :class="{ on: it === page }"
            data-testid="inv-page-btn"
            :data-page="it"
            :aria-current="it === page ? 'page' : undefined"
            :title="'第 ' + it + ' 页'"
            @click="goPage(it)"
          >
            {{ it }}
          </button>
        </template>
        <button data-testid="inv-next" :disabled="page >= totalPages" title="下一页" @click="onPageDelta(1)">下一页</button>
        <!-- 跳转选择器：页码按钮被省略号折叠时，仍可直达任意页 -->
        <label class="inv-jump">
          跳至
          <select data-testid="inv-page-select" :value="page" @change="onPagePick">
            <option v-for="p in totalPages" :key="p" :value="p">第 {{ p }} 页</option>
          </select>
        </label>
      </div>
    </div>

    <InventoryMatrixTable
      v-if="meta"
      :visibleMetrics="visibleMetrics"
      :cities="meta.cities"
      :rows="data?.rows ?? []"
      :totals="data?.totals ?? {}"
      :cityTotals="data?.city_totals ?? {}"
      :slowCities="slowCities"
      :filters="filters"
      :filterOptions="filterOptions"
      :sortKey="sort.key"
      :sortOrder="sort.order"
      :thresholds="thresholds"
      :cityCodes="cityCodes"
      @update:filters="onFilters"
      @update:sort="onSort"
    />

    <div v-if="loading" class="inv-loading" data-testid="inv-loading">加载中…</div>
      </template>

      <!-- 补货工作台：参数可调的采购清单（单 sheet Excel 导出）。
           顶部同样渲染预警条 + 视图切换 + 筛选条（与矩阵 pane 同构、共享 activeTab 绑定），
           保证 Tab 可达、预警信息常驻；两个 pane 一次仅渲染一个，DOM 中只存在一份。 -->
      <template v-else>
        <AlertCityStrip
          v-if="alerts"
          :cities="alerts.cities"
          :saving="alertsSaving"
          @update:threshold="onThreshold"
        />
        <div class="inv-viewtabs">
          <div class="view-tabs">
            <button :class="{ on: isMatrix }" data-testid="inv-tab-matrix" @click="activeTab = 'matrix'">库存矩阵</button>
            <button :class="{ on: isRestock }" data-testid="inv-tab-restock" @click="activeTab = 'restock'">补货工作台</button>
          </div>
          <span class="viewtabs-legend">
            按「有效日均销速 × 期望库存覆盖天数 × (1+安全系数) − 现货库存 − 在途库存」生成每周采购建议，下柜 SKU 已排除。
          </span>
        </div>
        <!-- 筛选条（2026-10 调整至模式切换条下方）：店铺 / 日期 / 搜索。
             补货视图无「指标配置」；筛选状态与矩阵 pane 共用父级的 shop/date/keyword。 -->
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
              <span>日期</span>
              <select v-model="date" data-testid="inv-date" @change="onFilterChange">
                <option value="">全部日期</option>
                <option v-for="d in meta?.dates ?? []" :key="d" :value="d">{{ d }}</option>
              </select>
            </label>
            <label class="inv-field inv-search">
              <span>搜索</span>
              <input
                :value="keywordInput"
                data-testid="inv-keyword"
                type="text"
                placeholder="SKU / 商品名称 / SPU"
                @input="onKeywordInput"
              />
            </label>
          </div>
        </div>
        <RestockPanel :shop="shop" :date="date" :keyword="keyword" />
      </template>
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
  /* 矩阵表表头底色：品牌蓝强档 (#dbeafe, blue-100)，比数据区纯白高一档半。
     表头文字本身是品牌蓝，铺同族浅蓝底属于「同色系分层」；强档下第一眼即能读出列名，
     与页面容器底色 (#f6f8fb) 的区分靠表头下方 1px 硬边线，不靠底色。 */
  --color-surface-head: #dbeafe;
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
  /* 高度：内容驱动（随矩阵表的高度预算一起长高），min-height 只做兜底，
     保证内容少时也铺满主区；纵向滚动由 .main 承担，
     滚轮下拉可完整看到概览卡 → 筛选条 → 矩阵表底部（合计行）。 */
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
/* 视图切换（库存矩阵 / 补货工作台）+ 图例（2026-10 新增） */
.inv-viewtabs {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.view-tabs {
  display: flex;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: 10px;
  padding: 3px;
  gap: 2px;
}
.view-tabs button {
  border: none;
  background: transparent;
  padding: 7px 16px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--color-text-3);
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
}
.view-tabs button:hover {
  color: var(--color-brand);
}
.view-tabs button.on {
  background: var(--color-brand);
  color: #fff;
  font-weight: 600;
}
.viewtabs-legend {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--color-text-4);
}
.viewtabs-legend .sw {
  display: inline-block;
  width: 13px;
  height: 13px;
  border-radius: 3px;
  margin-left: 8px;
  border: 1px solid var(--color-border-2);
}
/* 图例色块（2026-10-22 加深）：原色过淡（#fef2f2/#fffbeb）几乎看不出边界，
   一律提到 red-300/amber-300 填充 + red-500/amber-500 描边（描边与矩阵表
   单元格左侧色条同色系），灰纹改用 slate-200/slate-300 拉开条纹对比。 */
.viewtabs-legend .sw-r {
  background: #fca5a5;
  border-color: #ef4444;
}
.viewtabs-legend .sw-y {
  background: #fcd34d;
  border-color: #f59e0b;
}
.viewtabs-legend .sw-off {
  background: repeating-linear-gradient(45deg, #e5e7eb, #e5e7eb 4px, #cbd5e1 4px, #cbd5e1 8px);
  border-color: #94a3b8;
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
  /* 页码按钮变多后窄屏要能换行，避免把右侧的跳转选择器挤出容器 */
  flex-wrap: wrap;
  margin-bottom: 10px;
  font-size: 12.5px;
  color: var(--color-text-4);
}
.inv-count b {
  color: var(--color-text-strong);
}
/* 每页条数提示：与「共 N 条」同一视觉层级，弱化到 text-4 不抢主信息 */
.inv-pagesize {
  color: var(--color-text-4);
}
.inv-pagesize b {
  color: var(--color-text-3);
  font-variant-numeric: tabular-nums;
}
.inv-pager {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-wrap: wrap;
}
.inv-pager button {
  min-width: 30px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
/* 上一页 / 下一页：文字较宽，给足横向内边距 */
.inv-pager button[data-testid="inv-prev"],
.inv-pager button[data-testid="inv-next"] {
  padding: 0 12px;
}
.inv-pager button:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.inv-pager button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
/* 当前页：品牌蓝实心 + 白字，与表头蓝保持同一套蓝色语言 */
.inv-pager button.inv-page.on,
.inv-pager button.inv-page.on:hover:not(:disabled) {
  background: var(--color-brand);
  border-color: var(--color-brand);
  color: #fff;
  font-weight: 600;
}
/* 省略号占位：只占位不可点 */
.inv-page-gap {
  padding: 0 2px;
  color: var(--color-text-5);
  user-select: none;
}
/* 跳转选择器：与页码按钮同一高度，圆角 / 边框一致 */
.inv-jump {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  color: var(--color-text-4);
  white-space: nowrap;
}
.inv-jump select {
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: 12.5px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.inv-jump select:hover {
  border-color: var(--color-brand);
}
.inv-jump select:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 233, 0.1);
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
