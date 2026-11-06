<script setup lang="ts">
// 店铺运营数据页（方案 A+B+C 图表）：B 指标矩阵打底 + A 顶部 4 张 KPI 卡 +
// C 月度销售额趋势（按店铺类型拆解 + 月份之间环比徽章）。
// ⚠️ 表头是单行指标名，不再标注「原子 / 派生」分组；派生公式走 title 悬停。
//
// 三重隔离：独立路由 /ops-daily，不注册 modules/index.ts、不进 manifest v-for、
// 不落入 /module/:moduleId ⇒ 单品明细与库存页 0 改动。
// 列与公式的唯一来源是后端 ops_metric_dict（/meta 下发），本页 0 硬编码列数。
//
// 性能（SPEC §4.1）：服务端每页 50 行锁死 DOM 量；rows / summary 用 shallowRef
// 避免大数组深层 Proxy；v-memo 缓存行渲染；筛选防抖 + AbortController 取消在途请求；
// 导出按 500 行/批、并发 4 拉取，不经 DOM，用 Array.join 拼 CSV。
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { useUiStore } from "../stores/ui";
import { fetchOpsDailyData, fetchOpsDailyMeta } from "../api";
import {
  EXPORT_BATCH,
  EXPORT_CONCURRENCY,
  FILTER_DEBOUNCE_MS,
  KPI_CARDS,
  PAGE_SIZE,
  MONTHLY_CHART,
  buildMonthlyChart,
  buildOpsCsv,
  defaultRange,
  defaultSelectedKeys,
  exportPages,
  formatTrend,
  formatValue,
  isMissing,
  previousRange,
  spanMom,
  trendClass,
  type OpsDailyMeta,
  type OpsDailyRow,
  type OpsMetric,
  type OpsMonthlyPoint,
  type OpsQuery,
  type MonthlyMomBadge,
} from "../modules/opsDaily";

const ui = useUiStore();

/** 页面四态：加载 / 正常 / 空 / 错误 / 无权限，各有独立文案与出口。 */
type PageState = "loading" | "ok" | "empty" | "error" | "denied";

const state = ref<PageState>("loading");
const errorText = ref("");
const meta = ref<OpsDailyMeta | null>(null);
// shallowRef：一次响应最多 50 行 × 24 值，深层响应式代理是纯开销；整体替换即可。
const rows = shallowRef<OpsDailyRow[]>([]);
const summary = shallowRef<Record<string, number | null>>({});
const prevSummary = shallowRef<Record<string, number | null>>({});
// 方案 C 月度趋势点：只有月份数那么多行（当前 3 行），整体替换即可，翻页不重建。
const monthly = shallowRef<OpsMonthlyPoint[]>([]);
const total = ref(0);
const page = ref(1);

const from = ref("");
const to = ref("");
const shop = ref("");
const shopType = ref("");
const selectedKeys = ref<string[]>([]);
const panelOpen = ref(false);
const formulaOpen = ref(false);
const exporting = ref(false);
const exportDoneBatches = ref(0);
const exportTotalBatches = ref(0);
const exportNote = ref("");

let controller: AbortController | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
/** 请求序号：即使旧请求未被及时 abort，也不允许它覆盖新结果。 */
let requestSeq = 0;

/** 当前筛选条件（不含分页参数）。 */
const baseQuery = computed<OpsQuery>(() => ({
  from: from.value,
  to: to.value,
  shop: shop.value || undefined,
  shop_type: shopType.value || undefined,
}));

const metricsByKey = computed(() => {
  const map = new Map<string, OpsMetric>();
  for (const m of meta.value?.metrics ?? []) map.set(m.metric_key, m);
  return map;
});

/** 当前勾选列（顺序恒按字典 sort_order，勾选只决定「有哪些列」）。 */
const visibleMetrics = computed<OpsMetric[]>(() => {
  const picked = new Set(selectedKeys.value);
  return (meta.value?.metrics ?? []).filter((m) => picked.has(m.metric_key));
});

const derivedAll = computed(() => (meta.value?.metrics ?? []).filter((m) => m.kind === "derived"));
const maxPage = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));

/**
 * 方案 C 图表几何全部由纯函数算好，模板只渲染（判定层 0 计算、便于 node 断言）。
 * `monthly` 不受分页影响 ⇒ 翻页时值不变，computed 不会重算，图表不闪。
 */
const chart = computed(() => buildMonthlyChart(monthly.value));
const chartHasMoms = computed(() => monthly.value.length >= 2);
const monthXs = computed(() =>
  chart.value.months.map((_, i) =>
    MONTHLY_CHART.left +
    ((MONTHLY_CHART.right - MONTHLY_CHART.left) * i) /
      Math.max(1, chart.value.months.length - 1),
  ),
);
/** 右侧 mini 卡：每条折线的「首月 → 末月」区间涨跌。 */
const typeSpan = computed(() =>
  chart.value.lines.map((line) => ({
    label: line.label,
    mom: spanMom(line.points.map((p) => p.value)),
  })),
);
const totalSpan = computed(() => spanMom(monthly.value.map((m) => m.sales_amount)));
/**
 * 图例条里的徽章样例：取每条线第一个真实环比，⛔ 不造数。
 * 只有 1 个月时 badges 为空 ⇒ 样例自动消失，图例条其余部分仍完整。
 */
const legendDemos = computed<MonthlyMomBadge[]>(() =>
  chart.value.lines
    .slice(0, 2)
    .map((line) => line.badges[0])
    .filter((b): b is MonthlyMomBadge => Boolean(b)),
);

/** 勾选面板分组（分组标题只是展示，不影响字典是唯一列来源）。 */
const metricGroups = computed(() => [
  { title: "原子指标", items: (meta.value?.metrics ?? []).filter((m) => m.kind === "atomic") },
  { title: "派生指标（查询时重算）", items: derivedAll.value },
]);

/**
 * 把接口异常映射到页面状态。
 *
 * @remarks 401 / 403 单独走「无权限」态，避免把权限问题显示成「网络错误」误导用户重试。
 */
function applyError(e: unknown): void {
  const msg = e instanceof Error ? e.message : String(e);
  if (/\b(401|403)\b|无权限|Forbidden|Unauthorized/i.test(msg)) {
    state.value = "denied";
  } else {
    state.value = "error";
    errorText.value = msg;
  }
}

/** 拉取字典并初始化默认区间 / 默认勾选列。 */
async function loadMeta(): Promise<boolean> {
  try {
    const m = await fetchOpsDailyMeta();
    meta.value = m;
    if (!selectedKeys.value.length) {
      // 8 原子 + 2 派生：首屏就能在同一行表头里看到派生重算值。
      selectedKeys.value = defaultSelectedKeys(m.metrics);
    }
    if (m.data_through) {
      const r = defaultRange(m.data_through, 30);
      from.value = r.from;
      to.value = r.to;
    }
    return true;
  } catch (e) {
    applyError(e);
    return false;
  }
}

/**
 * 拉取当前页数据 + 上一期聚合。
 *
 * @remarks
 * 上期请求刻意 `page_size=1`：summary 是整段聚合、与分页无关，
 * 把 rows 压到 1 行可避免为 4 个环比数字白拉 50 行。
 */
async function loadData(): Promise<void> {
  if (!meta.value || !from.value || !to.value) return;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  const mySeq = ++requestSeq;
  state.value = "loading";

  const q = baseQuery.value;
  const prev = previousRange(q.from, q.to);
  try {
    const [cur, prevData] = await Promise.all([
      fetchOpsDailyData({ ...q, page: page.value, page_size: PAGE_SIZE }, signal),
      fetchOpsDailyData(
        { ...prev, shop: q.shop, shop_type: q.shop_type, page: 1, page_size: 1 },
        signal,
      ),
    ]);
    // 旧响应 / 已取消请求一律丢弃，不渲染（防快速改日期时的结果倒灌）。
    if (mySeq !== requestSeq || signal.aborted) return;
    rows.value = cur.rows;
    summary.value = cur.summary;
    prevSummary.value = prevData.summary;
    total.value = cur.total;
    // 月度点只取本期请求的：上期请求带的是上一段区间的 monthly，用它会画错趋势。
    monthly.value = cur.monthly ?? [];
    state.value = cur.rows.length ? "ok" : "empty";
  } catch (e) {
    if (signal.aborted || (e instanceof Error && e.name === "AbortError")) return;
    if (mySeq !== requestSeq) return;
    applyError(e);
  } finally {
    // 路由守卫点亮的全局遮罩，在这里交棒收起（含失败 / 取消场景，不能卡住）。
    ui.setNavigating(false);
  }
}

/** 首次进入 / 重试：meta → data 两段式。 */
async function bootstrap(): Promise<void> {
  const ok = await loadMeta();
  ui.setNavigating(false);
  if (ok && from.value && to.value) await loadData();
  else if (ok) state.value = "empty";
}

/** 筛选变化：250ms 防抖后回到第 1 页重新查询。 */
function onFilterChange(): void {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    page.value = 1;
    void loadData();
  }, FILTER_DEBOUNCE_MS);
}

/** 切到全区间（数据起止日由 /meta 下发，前端不硬编码日期）。 */
function useAllRange(): void {
  if (meta.value?.data_from && meta.value.data_through) {
    from.value = meta.value.data_from;
    to.value = meta.value.data_through;
    onFilterChange();
  }
}

/** 翻页。 */
function goPage(next: number): void {
  if (next < 1 || next > maxPage.value || state.value === "loading") return;
  page.value = next;
  void loadData();
}

/** 勾选 / 取消一列；列序始终按字典，避免勾选顺序造成表格与导出列序抖动。 */
function toggleKey(key: string): void {
  const picked = new Set(selectedKeys.value);
  if (picked.has(key)) picked.delete(key);
  else picked.add(key);
  selectedKeys.value = (meta.value?.metrics ?? [])
    .filter((m) => picked.has(m.metric_key))
    .map((m) => m.metric_key);
}

/**
 * 导出 CSV：分批拉取全量行 → Array.join 拼串 → Blob（带 UTF-8 BOM）下载。
 *
 * @remarks
 * ⛔ 不用 `all=true` 单次拉全量 JSON：数据量增长后会同时打爆后端内存与浏览器主线程。
 * 分批 500 行、并发 4 路，单次响应恒定；导出期间禁用按钮防重复点击。
 * CSV 单元格与表格共用 formatValue，`--` 原样保留。
 */
async function exportCsv(): Promise<void> {
  if (exporting.value || !meta.value || !visibleMetrics.value.length) return;
  exporting.value = true;
  exportNote.value = "";
  exportDoneBatches.value = 0;
  const cols = visibleMetrics.value;
  try {
    const first = await fetchOpsDailyData({
      ...baseQuery.value,
      page: 1,
      page_size: EXPORT_BATCH,
    });
    const pages = exportPages(first.total, EXPORT_BATCH);
    const batches: OpsDailyRow[][] = new Array<OpsDailyRow[]>(pages.length);
    batches[0] = first.rows;
    exportDoneBatches.value = Math.min(1, pages.length);
    exportTotalBatches.value = pages.length;

    let cursor = 1;
    async function worker(): Promise<void> {
      while (cursor < pages.length) {
        const i = cursor++;
        const res = await fetchOpsDailyData({
          ...baseQuery.value,
          page: pages[i],
          page_size: EXPORT_BATCH,
        });
        batches[i] = res.rows;
        exportDoneBatches.value++;
      }
    }
    const workers = Math.min(EXPORT_CONCURRENCY, Math.max(0, pages.length - 1));
    await Promise.all(Array.from({ length: workers }, () => worker()));

    const allRows = batches.flat();
    const csv = buildOpsCsv(cols, allRows);
    // BOM 放在这里而不是后端：中文 Excel 打开不带 BOM 的 UTF-8 CSV 会乱码。
    const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `店铺运营数据_${from.value}_${to.value}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    exportNote.value = `已导出 ${allRows.length} 行 / ${cols.length} 列`;
  } catch (e) {
    exportNote.value = e instanceof Error ? `导出失败：${e.message}` : "导出失败";
  } finally {
    exporting.value = false;
  }
}

onMounted(() => void bootstrap());

onBeforeUnmount(() => {
  controller?.abort();
  if (debounceTimer) clearTimeout(debounceTimer);
  ui.setNavigating(false);
});
</script>

<template>
  <div class="ops-daily" data-testid="ops-daily-view">
    <header class="od-head">
      <div>
        <h1 class="od-title">店铺运营数据</h1>
        <p class="od-sub">
          店铺日粒度指标 · 指标以数据库字典为基准 ·
          数据截至 <strong>{{ meta?.data_through || "--" }}</strong>
        </p>
      </div>
      <button class="od-btn-ghost" type="button" @click="useAllRange">全区间</button>
    </header>

    <!-- 错误 / 无权限：独立状态卡，带明确出口 -->
    <section v-if="state === 'error' || state === 'denied'" class="od-state" data-testid="ops-state-error">
      <div class="od-state-title">
        {{ state === "denied" ? "无权限访问店铺运营数据" : "数据加载失败" }}
      </div>
      <p class="od-state-desc">
        {{ state === "denied"
          ? "请联系管理员开通该页面的访问权限，或稍后重试。"
          : (errorText || "接口暂时不可用，请检查网络或服务状态后重试。") }}
      </p>
      <button class="od-btn-primary" type="button" @click="bootstrap">重试</button>
    </section>

    <template v-else>
      <section class="od-filters" aria-label="筛选条件">
        <label class="od-field">
          <span>起始日期</span>
          <input v-model="from" type="date" :max="to" @change="onFilterChange" />
        </label>
        <label class="od-field">
          <span>结束日期</span>
          <input v-model="to" type="date" :min="from" :max="meta?.data_through || undefined" @change="onFilterChange" />
        </label>
        <label class="od-field">
          <span>店铺</span>
          <select v-model="shop" @change="onFilterChange">
            <option value="">全部店铺</option>
            <option v-for="s in meta?.shops ?? []" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="od-field">
          <span>类型</span>
          <select v-model="shopType" @change="onFilterChange">
            <option value="">全部类型</option>
            <option value="pop">POP</option>
            <option value="self">自营</option>
          </select>
        </label>
      </section>

      <section class="od-kpis" aria-label="核心指标">
        <div v-for="card in KPI_CARDS" :key="card.key" class="od-kpi" :data-testid="`ops-kpi-${card.key}`">
          <div class="od-kpi-label">
            {{ card.label }}
            <span v-if="card.derived" class="od-tag">计算</span>
          </div>
          <div class="od-kpi-value">
            {{ formatValue(summary[card.key], metricsByKey.get(card.key)) }}
          </div>
          <div
            class="od-kpi-trend"
            :class="`trend-${trendClass(summary[card.key], prevSummary[card.key])}`"
          >
            {{ formatTrend(summary[card.key], prevSummary[card.key]) || "暂无上期数据" }}
          </div>
        </div>
      </section>

      <!-- 方案 C：月度销售额趋势（按店铺类型拆解）+ 月份之间的环比徽章。
           数据来自 /data 的 monthly 数组（不新增端点、不受分页影响），
           几何由 opsDaily.ts 的 buildMonthlyChart 算好，这里只渲染。 -->
      <section class="od-chart" aria-label="月度销售额趋势" data-testid="ops-monthly-chart">
        <div class="od-chart-head">
          <div>
            <div class="od-chart-title">月度销售额趋势 · 按店铺类型拆解</div>
            <div class="od-chart-sub">
              纵轴为非零起点放大区间，已标注 · 按当前店铺筛选展示<strong>全部月份</strong>（不随日期区间收窄）；<strong>不受「类型」筛选影响</strong>（结构对比需保留两条线）
            </div>
          </div>
          <div class="od-chart-asof">{{ chart.months.length }} 个月</div>
        </div>

        <!-- 图例上移：读图前的元信息放在标题与图表之间，而不是图后当注释。
             线型样例与徽章样例都取自同一份 chart 数据，⛔ 零造数。 -->
        <div class="od-legend-bar" data-testid="ops-legend-bar">
          <span class="od-legend-title">图例</span>
          <span v-for="line in chart.lines" :key="`lg-${line.key}`" class="od-legend-item">
            <svg class="od-legend-sample" width="26" height="10" viewBox="0 0 26 10" aria-hidden="true">
              <line
                x1="1"
                y1="5"
                x2="25"
                y2="5"
                :stroke="line.color"
                stroke-width="2"
                :stroke-dasharray="line.dash || undefined"
              />
              <circle
                cx="13"
                cy="5"
                r="2.6"
                :fill="line.hollow ? 'var(--color-surface)' : line.color"
                :stroke="line.hollow ? line.color : 'none'"
                stroke-width="1.5"
              />
            </svg>
            {{ line.label }} · {{ line.dash ? "虚线 / 空心点" : "实线 / 实心点" }}
          </span>
          <span class="od-legend-sep"></span>
          <span v-if="legendDemos.length" class="od-legend-item od-legend-demo">
            <span
              v-for="b in legendDemos"
              :key="`demo-${b.title}`"
              :class="`trend-${b.cls}`"
            >{{ b.symbol }} {{ b.text }}</span>
          </span>
          <span class="od-legend-note">徽章 = <strong>相对上一个月的环比</strong>，绿涨红跌</span>
        </div>

        <div v-if="!chartHasMoms" class="od-chart-empty" data-testid="ops-chart-empty">
          数据不足 2 个月，无法计算月份间环比（图表展示全部月份，不随日期区间收窄）。
        </div>
        <div v-else class="od-chart-body">
          <div class="od-chart-split">
            <svg
              class="od-svg"
              :viewBox="`0 0 ${MONTHLY_CHART.width} ${MONTHLY_CHART.height}`"
              role="img"
              aria-label="自营实线与 POP 虚线的月度销售额双折线图，折线上方标注月份之间的环比"
            >
              <!-- 单位统一放这里：刻度改 1 位小数并去掉「万」后缀后不会再顶出画布 -->
              <text x="14" y="18" class="od-axis-title">单位：万元</text>
              <line
                v-for="t in chart.ticks"
                :key="`grid-${t.y}`"
                :x1="MONTHLY_CHART.left"
                :y1="t.y"
                :x2="MONTHLY_CHART.right"
                :y2="t.y"
                class="od-grid"
              />
              <text
                v-for="t in chart.ticks"
                :key="`tick-${t.y}`"
                :x="MONTHLY_CHART.left - 12"
                :y="t.y + 4"
                text-anchor="end"
                class="od-tick"
              >{{ t.label }}</text>

              <g v-for="line in chart.lines" :key="line.key">
                <path
                  :d="line.path"
                  fill="none"
                  :stroke="line.color"
                  stroke-width="2"
                  stroke-linejoin="round"
                  stroke-linecap="round"
                  :stroke-dasharray="line.dash || undefined"
                />
                <template v-for="(p, i) in line.points" :key="`p-${line.key}-${i}`">
                  <circle
                    v-if="p.value !== null"
                    :cx="p.x"
                    :cy="p.y"
                    :r="i === line.points.length - 1 ? 4.2 : 3"
                    :fill="line.hollow ? 'var(--color-surface)' : line.color"
                    :stroke="line.hollow ? line.color : 'none'"
                    stroke-width="1.5"
                  />
                  <text
                    v-if="p.value !== null"
                    :x="p.x"
                    :y="p.y - 10"
                    text-anchor="middle"
                    class="od-point-label"
                  >{{ p.label }}</text>
                </template>
                <!-- 方案 1 环比徽章：纯文字「▲ +5.92%」，⛔ 无胶囊容器。
                     y 由 badgeDy = -13 算好，统一落在折线上方（用户 2026-11-06 追加）；
                     徽章 X 在两月之间的线段中点，与月份点上的数值标签天然错开。 -->
                <g
                  v-for="(b, i) in line.badges"
                  :key="`b-${line.key}-${i}`"
                  class="od-mom"
                  :class="`mom-${b.cls}`"
                  :data-testid="`ops-mom-${line.key}-${i}`"
                >
                  <title>{{ b.title }}</title>
                  <text :x="b.x" :y="b.y + 3.5" text-anchor="middle">{{ b.symbol }} {{ b.text }}</text>
                </g>
              </g>

              <text
                v-for="(m, i) in chart.months"
                :key="`m-${m}`"
                :x="monthXs[i]"
                :y="MONTHLY_CHART.bottom + 30"
                text-anchor="middle"
                class="od-month"
              >{{ m }}</text>
            </svg>

            <div class="od-minis">
              <div v-for="s in typeSpan" :key="s.label" class="od-mini">
                <div class="od-mini-t">
                  {{ s.label }} · {{ chart.months[0] }} → {{ chart.months[chart.months.length - 1] }}
                </div>
                <div class="od-mini-v" :class="`trend-${s.mom.cls}`">{{ s.mom.text }}</div>
              </div>
              <div class="od-mini">
                <div class="od-mini-t">合计销售额 · 逐月环比（相对上月）</div>
                <div class="od-mom-list">
                  <div v-for="(t, i) in chart.totalMoms" :key="t.month">
                    <span>{{ t.month }} 比 {{ chart.months[i].slice(5, 7) }} 月</span>
                    <b :class="`trend-${t.mom.cls}`">{{ t.mom.text }}</b>
                  </div>
                </div>
                <div class="od-mini-d">
                  {{ chart.months[0] }} 是首月，没有上月可比 · 区间涨跌
                  <span :class="`trend-${totalSpan.cls}`">{{ totalSpan.text }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="od-tools">
        <div class="od-panel-wrap">
          <button class="od-btn-ghost" type="button" :aria-expanded="panelOpen" @click="panelOpen = !panelOpen">
            指标勾选（{{ selectedKeys.length }}/{{ meta?.metrics.length ?? 0 }}）
          </button>
          <div v-if="panelOpen" class="od-panel" data-testid="ops-metric-panel">
            <div v-for="g in metricGroups" :key="g.title" class="od-group">
              <div class="od-group-title">{{ g.title }}</div>
              <label v-for="m in g.items" :key="m.metric_key" class="od-check">
                <input
                  type="checkbox"
                  :checked="selectedKeys.includes(m.metric_key)"
                  @change="toggleKey(m.metric_key)"
                />
                <span>{{ m.cn_name }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="od-export">
          <span v-if="exportNote" class="od-note" role="status">{{ exportNote }}</span>
          <button
            class="od-btn-primary"
            type="button"
            :disabled="exporting || !visibleMetrics.length"
            data-testid="ops-export"
            @click="exportCsv"
          >
            {{ exporting ? `导出中 ${exportDoneBatches}/${exportTotalBatches}` : "导出 CSV" }}
          </button>
        </div>
      </section>

      <section class="od-table-wrap" aria-label="指标矩阵">
        <div v-if="state === 'loading'" class="od-loading" data-testid="ops-state-loading">
          正在加载数据…
        </div>
        <template v-else>
          <div v-if="state === 'empty'" class="od-state od-state-inline" data-testid="ops-state-empty">
            <div class="od-state-title">当前筛选条件下没有数据</div>
            <p class="od-state-desc">试试放宽日期区间，或点击右上角「全区间」。</p>
            <button class="od-btn-ghost" type="button" @click="useAllRange">切到全区间</button>
          </div>
          <div v-if="!visibleMetrics.length" class="od-state od-state-inline">
            <div class="od-state-title">未勾选任何指标</div>
            <p class="od-state-desc">在「指标勾选」里至少选择一列后再导出。</p>
          </div>
          <div v-else class="od-scroll">
            <table class="od-table">
              <!-- 表头只保留一行指标名：不再标注「原子 / 派生」分组（用户 2026-11-06 要求）。
                   派生列的公式仍走 title 悬停；勾选面板里的分组文字不是表头，不受影响。 -->
              <thead>
                <tr>
                  <th class="od-sticky">日期</th>
                  <th class="od-sticky od-sticky-2">店铺</th>
                  <th>类型</th>
                  <th
                    v-for="m in visibleMetrics"
                    :key="m.metric_key"
                    :title="m.formula || m.cn_name"
                    :class="{ 'od-th-derived': m.kind === 'derived' }"
                  >
                    {{ m.cn_name }}<small v-if="m.unit" class="od-unit">{{ m.unit }}</small>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in rows"
                  :key="`${row.date}|${row.shop}|${row.shop_type}`"
                  v-memo="[row, visibleMetrics]"
                >
                  <td class="od-sticky">{{ row.date }}</td>
                  <td class="od-sticky od-sticky-2">{{ row.shop }}</td>
                  <td>{{ row.shop_type === "pop" ? "POP" : "自营" }}</td>
                  <td
                    v-for="m in visibleMetrics"
                    :key="m.metric_key"
                    :class="{ 'is-missing': isMissing(row.values[m.metric_key]) }"
                  >
                    {{ formatValue(row.values[m.metric_key], m) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </section>

      <footer class="od-pager">
        <span>共 {{ total }} 行 · 第 {{ page }} / {{ maxPage }} 页 · 每页 {{ PAGE_SIZE }} 行</span>
        <div class="od-pager-btns">
          <button class="od-btn-ghost" type="button" :disabled="page <= 1 || state === 'loading'" @click="goPage(page - 1)">
            上一页
          </button>
          <button class="od-btn-ghost" type="button" :disabled="page >= maxPage || state === 'loading'" @click="goPage(page + 1)">
            下一页
          </button>
        </div>
      </footer>

      <section class="od-formula">
        <button class="od-link" type="button" :aria-expanded="formulaOpen" @click="formulaOpen = !formulaOpen">
          派生口径说明 {{ formulaOpen ? "▲" : "▼" }}
        </button>
        <div v-if="formulaOpen" class="od-formula-body" data-testid="ops-formula">
          <div v-for="m in derivedAll" :key="m.metric_key" class="od-formula-row">
            <strong>{{ m.cn_name }}</strong><span> = {{ m.formula }}</span>
          </div>
          <p class="od-formula-note">
            公式读自指标字典（改字典即改文案）。KPI 卡 = 当前筛选范围整体「先求和再相除」，
            矩阵行 = 单行 (date, shop)；两者粒度不同，验收时禁止互相比对数值。
            真实 0 显示 0，仅缺失或分母为 0 显示 --。
          </p>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
/* 局部令牌覆盖：只在本页根容器作用域生效，⛔ 不改 :root（全站品牌红不变）。
   照抄 InventoryView.vue 的 .inv，保持库存模块「克制蓝」的观感。 */
.ops-daily {
  --color-brand: #2563eb;
  --color-brand-dark: #1d4ed8;
  --color-brand-darker: #1e40af;
  --color-brand-tint: #eff6ff;
  --color-brand-tint-2: #f0f6ff;
  --color-brand-tint-3: #f5f8ff;
  --color-brand-tint-4: #f5f8ff;
  --color-brand-border: #bfdbfe;
  --color-brand-border-2: #dbeafe;
  --color-surface-head: #dbeafe;
  --color-bg: #f6f8fb;
  --color-surface: #ffffff;
  --color-surface-2: #f8fafc;
  --color-surface-3: #fafbfc;
  --color-border: #e5e7eb;
  --color-border-2: #eef2f7;
  --color-border-4: #f1f5f9;
  min-height: 100%;
  padding: 22px 24px 32px;
  background: var(--color-bg);
  color: var(--color-text);
  /* flex item 必须显式 min-width:0，否则宽表会把容器撑破（ai-rules §5） */
  min-width: 0;
}

.od-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.od-title {
  margin: 0;
  font-size: calc(20px * var(--font-scale));
  font-weight: 700;
  color: var(--color-text-strong);
  letter-spacing: 0.5px;
}
.od-sub {
  margin: 5px 0 0;
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-5);
}
.od-sub strong { color: var(--color-text-3); font-weight: 600; }

.od-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 14px;
  margin-bottom: 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.od-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-5);
  min-width: 0;
}
.od-field input,
.od-field select {
  min-width: 140px;
  padding: 6px 9px;
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-3);
  border-radius: var(--radius-sm);
  outline: none;
}
.od-field input:focus-visible,
.od-field select:focus-visible,
.od-btn-ghost:focus-visible,
.od-btn-primary:focus-visible,
.od-link:focus-visible,
.od-check input:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 2px;
}

.od-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.od-kpi {
  /* KPI 卡用中性底：主蓝每页 ≤1 处，留给导出按钮 */
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
}
.od-kpi-label {
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-5);
  display: flex;
  align-items: center;
  gap: 6px;
}
.od-tag {
  font-size: calc(10px * var(--font-scale));
  padding: 1px 5px;
  color: var(--color-brand-dark);
  background: var(--color-brand-tint);
  border: 1px solid var(--color-brand-border-2);
  border-radius: var(--radius-xs);
}
.od-kpi-value {
  margin-top: 8px;
  font-size: calc(24px * var(--font-scale));
  font-weight: 700;
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
}
.od-kpi-trend {
  margin-top: 5px;
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-5);
  font-variant-numeric: tabular-nums;
}
/* D3 绿涨红跌：沿用全局 --color-up / --color-down，零新增令牌 */
.od-kpi-trend.trend-up { color: var(--color-up); }
.od-kpi-trend.trend-down { color: var(--color-down); }
.od-kpi-trend.trend-flat { color: var(--color-text-5); }

/* ===== 方案 C：月度销售额趋势 + 月份之间环比徽章 =====
   颜色只用页面局部已有的蓝令牌与全局 --color-up / --color-down，
   ⛔ 零新增 hex、零改动 :root。
   2026-11-06 视觉优化：图例上移、徽章去胶囊改纯文字并上移到折线上方、
   实线/虚线 + 实心/空心点区分两条线、Y 轴刻度 1 位小数去「万」、
   灰字 --color-text-6 → --color-text-3（白底对比度 4.32:1 → 7.58:1）。 */
.od-chart {
  margin-bottom: 14px;
  padding: 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.od-chart-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.od-chart-title { font-size: calc(14px * var(--font-scale)); font-weight: 600; color: var(--color-text-strong); }
.od-chart-sub { margin-top: 4px; font-size: calc(12.5px * var(--font-scale)); font-weight: 600; color: var(--color-text-3); line-height: 1.7; }
.od-chart-sub strong { color: var(--color-brand-dark); font-weight: 700; }
.od-chart-asof {
  flex: none;
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text-3);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 5px 9px;
}

/* 图例条：读图前的元信息放在标题与图表之间，而不是图后当注释。 */
.od-legend-bar {
  display: flex; align-items: center; flex-wrap: wrap; gap: 12px;
  background: var(--color-bg);
  border: 1px solid var(--color-brand-border);
  border-radius: var(--radius-md);
  padding: 9px 12px; margin-bottom: 12px;
  font-size: calc(12.5px * var(--font-scale)); font-weight: 600;
  color: var(--color-text-3);
}
.od-legend-title {
  flex: none;
  font-size: calc(11px * var(--font-scale)); font-weight: 700; letter-spacing: 1px;
  color: var(--color-brand-darker);
  background: var(--color-brand-tint);
  border-radius: var(--radius-sm); padding: 2px 7px;
}
.od-legend-item { display: inline-flex; align-items: center; gap: 7px; white-space: nowrap; }
.od-legend-sample { flex: none; display: block; }
.od-legend-sep { flex: none; width: 1px; height: 16px; background: var(--color-brand-border); }
.od-legend-demo { gap: 8px; font-weight: 700; }
.od-legend-demo .trend-up { color: var(--color-up); }
.od-legend-demo .trend-down { color: var(--color-down); }
.od-legend-demo .trend-flat { color: var(--color-text-5); }
.od-legend-note { margin-left: auto; font-size: calc(12px * var(--font-scale)); font-weight: 400; color: var(--color-text-5); }
.od-legend-note strong { color: var(--color-text-3); font-weight: 600; }

.od-chart-empty { padding: 28px 12px; text-align: center; font-size: calc(12.5px * var(--font-scale)); color: var(--color-text-3); }
.od-chart-split { display: grid; grid-template-columns: minmax(0, 2.2fr) minmax(0, 1fr); gap: 14px; align-items: start; }
.od-svg { width: 100%; height: auto; display: block; }
.od-grid { stroke: var(--color-border-2); stroke-width: 1; }
/* 2026-11-06 17:37 用户反馈「图表字体和线条都太粗太大」⇒ 统一收细一档。
   只缩字号 / 字重 / 线宽 / 端点，⛔ 不动 --color-text-3 深色（7.58:1 保持不变），
   否则会退回上一轮「浅灰字看不清」的问题。图例条与 mini 卡是 HTML 文本，不在本次范围。 */
.od-axis-title { font-size: calc(10.5px * var(--font-scale)); font-weight: 500; fill: var(--color-text-3); }
.od-tick, .od-month { font-size: calc(10.5px * var(--font-scale)); font-weight: 500; fill: var(--color-text-3); }
.od-point-label { font-size: calc(10.5px * var(--font-scale)); font-weight: 600; fill: var(--color-brand-darker); }

/* 方案 1 徽章：删掉白底描边胶囊，只留彩色「涨跌符号 + 百分比」。
   paint-order 让画布色描边先画，压在折线 / 网格上仍清楚；零遮挡、零新增色值。
   描边宽度随字号一起收细，避免细字挂一圈粗白边。 */
.od-mom text {
  font-size: calc(10.5px * var(--font-scale));
  font-weight: 600;
  paint-order: stroke fill;
  stroke: var(--color-surface);
  stroke-width: 2.5px;
  stroke-linejoin: round;
}
.od-mom.mom-up text { fill: var(--color-up); }
.od-mom.mom-down text { fill: var(--color-down); }
.od-mom.mom-flat text { fill: var(--color-text-5); }

.od-minis { display: flex; flex-direction: column; gap: 9px; }
.od-mini {
  background: var(--color-brand-tint-3);
  border: 1px solid var(--color-brand-border-2);
  border-radius: var(--radius-md);
  padding: 10px 11px;
}
.od-mini-t { font-size: calc(12px * var(--font-scale)); font-weight: 600; color: var(--color-text-3); }
.od-mini-v {
  margin-top: 4px;
  font-size: calc(16px * var(--font-scale));
  font-weight: 600;
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
}
.od-mini-v.trend-up, .od-mini-d .trend-up, .od-mom-list .trend-up { color: var(--color-up); }
.od-mini-v.trend-down, .od-mini-d .trend-down, .od-mom-list .trend-down { color: var(--color-down); }
.od-mini-v.trend-flat, .od-mini-d .trend-flat, .od-mom-list .trend-flat { color: var(--color-text-5); }
/* 合计月环比：按行写清「哪个月比哪个月」，不再把两个数字挤成一团 */
.od-mom-list { margin-top: 5px; display: flex; flex-direction: column; gap: 2px; font-size: calc(12px * var(--font-scale)); }
.od-mom-list div { display: flex; justify-content: space-between; gap: 8px; color: var(--color-text-3); }
.od-mom-list b { font-weight: 700; font-variant-numeric: tabular-nums; }
.od-mini-d { margin-top: 4px; font-size: calc(11.5px * var(--font-scale)); color: var(--color-text-5); }
@media (max-width: 900px) { .od-chart-split { grid-template-columns: 1fr; } }

.od-tools {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.od-panel-wrap { position: relative; }
.od-panel {
  position: absolute;
  z-index: 20;
  top: calc(100% + 6px);
  left: 0;
  width: 420px;
  max-height: 340px;
  overflow: auto;
  padding: 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-brand-border-2);
  border-radius: var(--radius-md);
  box-shadow: 0 10px 28px rgb(15 23 42 / 12%);
}
.od-group + .od-group { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--color-border-2); }
.od-group-title {
  font-size: calc(12px * var(--font-scale));
  font-weight: 600;
  color: var(--color-brand-dark);
  margin-bottom: 6px;
}
.od-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  width: 48%;
  margin-bottom: 5px;
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-2);
  cursor: pointer;
}
.od-check input { accent-color: var(--color-brand); cursor: pointer; }
.od-export { display: flex; align-items: center; gap: 10px; }
.od-note { font-size: calc(12px * var(--font-scale)); color: var(--color-text-5); }

.od-btn-ghost,
.od-btn-primary {
  padding: 7px 13px;
  font-size: calc(12.5px * var(--font-scale));
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
}
.od-btn-ghost {
  color: var(--color-text-2);
  background: var(--color-surface);
  border: 1px solid var(--color-border-3);
}
.od-btn-ghost:hover:not(:disabled) { background: var(--color-surface-2); }
/* 唯一一处主蓝：导出按钮 */
.od-btn-primary {
  color: #fff;
  background: var(--color-brand);
  border: 1px solid var(--color-brand);
}
.od-btn-primary:hover:not(:disabled) { background: var(--color-brand-dark); }
.od-btn-ghost:disabled,
.od-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.od-table-wrap {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  min-width: 0;
}
.od-scroll { overflow-x: auto; overflow-y: auto; max-height: 62vh; }
.od-table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: calc(12.5px * var(--font-scale)); }
.od-table th,
.od-table td {
  padding: 7px 10px;
  text-align: right;
  white-space: nowrap;
  border-bottom: 1px solid var(--color-border-4);
  font-variant-numeric: tabular-nums;
}
.od-table thead th {
  position: sticky;
  top: 0;
  z-index: 3;
  background: var(--color-surface-head);
  color: var(--color-brand-darker);
  font-weight: 600;
  border-bottom: 1px solid var(--color-brand-border);
}
/* 表头已改单行：不再有第二行 sticky 偏移，也不再有分组表头样式 */
.od-th-derived { color: var(--color-brand-dark) !important; }
.od-unit { margin-left: 3px; font-weight: 400; color: var(--color-text-5); }
.od-table td:first-child,
.od-table td:nth-child(2),
.od-table th:first-child,
.od-table th:nth-child(2) { text-align: left; }
.od-sticky { position: sticky; left: 0; z-index: 2; background: var(--color-surface); }
.od-sticky-2 { left: 96px; }
.od-table thead .od-sticky { z-index: 4; background: var(--color-surface-head); }
/* 固定列表头背景与其他 th 一致，单行表头不再需要第二层覆盖 */
.od-table tbody tr:hover td { background: var(--color-surface-2); }
.od-table tbody tr:hover .od-sticky { background: var(--color-surface-2); }
.od-table .is-missing { color: var(--color-text-6); }
.od-loading { padding: 36px; text-align: center; color: var(--color-text-5); }

.od-state {
  padding: 40px 24px;
  text-align: center;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.od-state-inline { margin: 12px; padding: 22px; border: 1px dashed var(--color-brand-border); }
.od-state-title { font-size: calc(15px * var(--font-scale)); font-weight: 600; color: var(--color-text-2); }
.od-state-desc { margin: 8px 0 16px; font-size: calc(12.5px * var(--font-scale)); color: var(--color-text-5); }

.od-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-5);
}
.od-pager-btns { display: flex; gap: 8px; }

.od-formula { margin-top: 16px; }
.od-link {
  padding: 0;
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-brand-dark);
  background: none;
  border: none;
  cursor: pointer;
}
.od-formula-body {
  margin-top: 8px;
  padding: 12px 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-brand-border-2);
  border-radius: var(--radius-md);
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-2);
}
.od-formula-row { padding: 2px 0; }
.od-formula-row strong { color: var(--color-text-3); }
.od-formula-note { margin: 10px 0 0; color: var(--color-text-5); line-height: 1.7; }

@media (prefers-reduced-motion: reduce) {
  .od-btn-ghost, .od-btn-primary, .od-kpi { transition: none; }
}
</style>
