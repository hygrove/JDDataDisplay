<script setup lang="ts">
// 模块页：保留顶部工具条（日期范围 / 搜索 / 状态 / 指标配置 / 刷新），
// 下方按「一个 SPU 一张单元卡」展示：
//   - 单日模式（start==end，默认=最新一天）：卡片以网格排列，顶部商品信息 +
//     左侧图片 + 右侧勾选指标（一个指标一行）；
//   - 区间模式（start!=end）：每个 SPU 单独占一整行，左侧固定商品信息 + 图片，
//     右侧铺成「指标 × 日期」矩阵，日期逐列标注，缺失数据显示 --，可横向滚动。
// 日期范围选择器内置「当月 / 上月」快捷按钮（与单品分析页行为一致）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useModuleStore } from "../stores/module";
import { useMetricsStore } from "../stores/metrics";
import { getMetric, metricsOf } from "../metrics";
import { fmtBy } from "../modules";
import type { MetricRecord, Row } from "../types";
import MetricConfigPanel from "../components/MetricConfigPanel.vue";
import DateRangePicker from "../components/DateRangePicker.vue";

const props = defineProps<{ moduleId: string }>();
const store = useModuleStore();
const metricsStore = useMetricsStore();
const route = useRoute();
const router = useRouter();

// 诊断开关（仅用于定位「最小化被拉回」问题，正常访问不带这些参数）：
//   ?bare=1      只渲染一个空白诊断页（不初始化 store / 不挂网格）
const bare = computed(() => "bare" in route.query);

onMounted(async () => {
  if (bare.value) return;
  await store.init(props.moduleId);
  // 从单品分析页跳转过来时可能带 query.shop，初始化后应用并清掉 query
  const shopFromQuery = route.query.shop as string;
  if (shopFromQuery && shopFromQuery !== store.shop) {
    store.setShop(shopFromQuery);
    void router.replace({ query: { ...route.query, shop: undefined } });
  }
});
onUnmounted(() => {
  observer?.disconnect();
  store.reset();
});
watch(
  () => props.moduleId,
  (id) => {
    if (bare.value) return;
    void store.init(id);
  }
);

// ---------- 指标配置：勾选的 key <-> store（localStorage 持久化，分析页共用）----------
const selectedKeys = computed({
  get: () => metricsStore.keysFor(props.moduleId),
  set: (v: string[]) => metricsStore.set(props.moduleId, v),
});
// 勾选的指标（按清单顺序），由指标清单唯一事实来源派生
const cardSpecs = computed(() => metricsOf(selectedKeys.value));
const metricSpecs = computed(() => cardSpecs.value);

// ---------- 搜索防抖 ----------
const keywordInput = ref("");
let timer: ReturnType<typeof setTimeout> | null = null;
watch(keywordInput, (kw) => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => store.setKeyword(kw.trim()), 400);
});

// ---------- 日期范围 ----------
function onRangeChange(v: { start: string; end: string }) {
  store.setRange(v.start, v.end);
}

// 区间模式下所有卡片共用的列（日期），取各 SPU 日期并集并排序——保证矩阵列对齐
const dayDates = computed<string[]>(() => {
  const set = new Set<string>();
  for (const r of store.rows) for (const d of r.days) set.add(d.date);
  return [...set].sort();
});
// 单日（列数=1）走网格卡；多日（列数>1）走整行矩阵
const isRange = computed(() => dayDates.value.length > 1);

// 单日模式下取该 SPU 当天指标（兼容 days[0] 或旧字段 metrics）
function dayMetrics(row: Row): MetricRecord {
  return row.days?.[0]?.metrics ?? row.metrics;
}
// 区间模式下取某天指标
function metricsOn(row: Row, date: string): MetricRecord | undefined {
  return row.days.find((d) => d.date === date)?.metrics;
}
// 日期列表头：周末标红，让矩阵更有层次
function isWeekend(date: string): boolean {
  const w = new Date(date + "T00:00:00").getDay();
  return w === 0 || w === 6;
}

// 区间模式下：在左侧固定区展示的三个日均指标，分母 = 所选日期范围的天数（含无数据日）
function averageMetrics(row: Row) {
  const n = dayDates.value.length;
  if (n === 0) {
    return { amount: "--", promotion_ratio: "--", roi: "--", buyers: "--" };
  }
  const entries = [
    { key: "amount" as keyof MetricRecord, metricKey: "amount" },
    { key: "promotion_ratio" as keyof MetricRecord, metricKey: "promotion_ratio" },
    { key: "roi" as keyof MetricRecord, metricKey: "roi" },
    { key: "buyers" as keyof MetricRecord, metricKey: "buyers" },
  ] as const;
  const out = { amount: "--", promotion_ratio: "--", roi: "--", buyers: "--" };
  for (const { key, metricKey } of entries) {
    let sum = 0;
    let has = false;
    for (const d of row.days) {
      const v = d.metrics[key];
      if (typeof v === "number" && !Number.isNaN(v)) {
        sum += v;
        has = true;
      }
    }
    const spec = getMetric(metricKey);
    const avg = has ? sum / n : null;
    (out as Record<string, string>)[key as string] = fmtBy(spec?.format ?? "number", avg);
  }
  return out;
}

// ---------- 触底加载更多（无限滚动，单页拉满后继续翻页）----------
const sentinelRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;
function setupObserver() {
  if (observer) observer.disconnect();
  if (!sentinelRef.value) return;
  observer = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting && store.hasMore && !store.loadingRows) {
          void store.loadMore();
        }
      }
    },
    { rootMargin: "240px" }
  );
  observer.observe(sentinelRef.value);
}
watch(sentinelRef, () => setupObserver());

// 点击「单品分析」-> 跳转单品分析页（带上店铺，避免跨店铺混淆）
function goAnalysis(row: { spu: string; shop: string }) {
  void router.push({
    name: "spu-analysis",
    params: { moduleId: props.moduleId, spu: row.spu },
    query: { shop: row.shop },
  });
}

// 由原始图片 URL（/images/xxx.png）推导缩略图 URL（/thumbs/{size}/xxx.avif|webp）
// 前端展示框只有 180/120/96px，下原图纯浪费带宽，这里改用对应尺寸缩略图，AVIF 优先 + WebP 兜底。
function thumb(orig: string | null | undefined, size: number, fmt: "avif" | "webp" = "avif"): string {
  if (!orig) return "";
  const base = orig.replace("/images/", "");
  const stem = base.replace(/\.[^.]+$/, "");
  return `/thumbs/${size}/${stem}.${fmt}`;
}

// ---------- 复制 SPU 编号 ----------
const copiedKey = ref(""); // 刚复制的那张卡（spu|shop），用来把图标临时切成对勾
let copyTimer: ReturnType<typeof setTimeout> | null = null;
async function copySpu(row: { spu: string; shop: string }) {
  const text = row.spu;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      // 兜底：非安全上下文 / 老浏览器
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
  } catch {
    // 复制失败也不打断页面
  }
  copiedKey.value = row.spu + "|" + row.shop;
  if (copyTimer) clearTimeout(copyTimer);
  copyTimer = setTimeout(() => (copiedKey.value = ""), 1500);
}
</script>

<template>
  <div class="page" v-if="!bare">
    <!-- 工具条（保持不变：日期范围 → 搜索 → 状态 → 指标配置 → 手动刷新） -->
    <div class="toolbar">
      <div class="tool-left">
        <div class="tool-item">
          <span>统计区间</span>
          <DateRangePicker
            :start="store.start"
            :end="store.end"
            :min="store.currentModule?.date_range?.[0]"
            :max="store.currentModule?.date_range?.[1]"
            show-last-week
            @change="onRangeChange"
          />
        </div>
        <input
          v-model="keywordInput"
          class="search"
          type="search"
          placeholder="搜索 SPU 号 / 商品名称…"
        />
      </div>
      <div class="tool-right">
        <span v-if="store.currentModule?.date_range?.length === 2" class="status">
          数据区间：{{ store.currentModule.date_range[0] }} ~ {{ store.currentModule.date_range[1] }}
        </span>
        <span v-if="store.status" class="status" :class="{ ok: store.status.success }">
          上次更新：{{ store.status.last_run_at ? new Date(store.status.last_run_at).toLocaleString("zh-CN") : "--" }}
          · {{ store.status.message }}
        </span>
        <MetricConfigPanel v-model:keys="selectedKeys" />
        <button class="btn" :disabled="store.refreshing" @click="store.refresh()">
          {{ store.refreshing ? "刷新中…" : "手动刷新" }}
        </button>
      </div>
    </div>

    <div v-if="store.error" class="error">{{ store.error }}</div>

    <!-- 单日模式：SPU 单元网格（一个 SPU 一张卡） -->
    <div class="spu-grid" v-if="store.rows.length && !isRange">
      <div
        v-for="row in store.rows"
        :key="row.spu + '|' + row.shop"
        class="spu-card"
      >
        <div class="spu-head">
          <div class="spu-info">
            <div class="spu-name" :title="row.spu_name ?? ''">{{ row.spu_name ?? "--" }}</div>
            <div class="spu-meta">
              <span class="m-spu">SPU: {{ row.spu }}<button
    class="copy-btn"
    :class="{ copied: copiedKey === row.spu + '|' + row.shop }"
    :title="copiedKey === row.spu + '|' + row.shop ? '已复制' : '复制 SPU'"
    @click.stop="copySpu(row)"
  ><svg v-if="copiedKey !== row.spu + '|' + row.shop" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><svg v-else viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></button></span>
              <span v-if="row.category">类目: {{ row.category }}</span>
              <span class="m-full">店铺: {{ row.shop }}</span>
            </div>
          </div>
          <a class="spu-analysis" @click="goAnalysis(row)">单品分析 →</a>
        </div>

        <div class="spu-body">
          <div class="spu-img">
            <picture v-if="row.image">
              <source :srcset="thumb(row.image, 180, 'avif')" type="image/avif" />
              <source :srcset="thumb(row.image, 180, 'webp')" type="image/webp" />
              <img :src="row.image" :alt="row.spu" width="180" height="180" loading="lazy" />
            </picture>
            <div v-else class="no-img">暂无图片</div>
          </div>

          <div class="spu-metrics">
            <template v-if="metricSpecs.length">
              <div v-for="s in metricSpecs" :key="s.key as string" class="metric-row">
                <span class="m-label">{{ s.title }}</span>
                <span
                  class="m-value"
                  :class="{ missing: dayMetrics(row)[s.key as keyof MetricRecord] == null }"
                  >{{ fmtBy(s.format, dayMetrics(row)[s.key as keyof MetricRecord]) }}</span
                >
              </div>
            </template>
            <div v-else class="metrics-empty">未勾选指标，点击右上角「指标配置」选择要展示的指标</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 区间模式：每个 SPU 占一整行，右侧铺「指标 × 日期」矩阵 -->
    <div class="spu-rows" v-else-if="store.rows.length && isRange">
      <div
        v-for="row in store.rows"
        :key="row.spu + '|' + row.shop"
        class="spu-row"
      >
        <div class="spu-row-head">
          <div class="spu-name" :title="row.spu_name ?? ''">{{ row.spu_name ?? "--" }}</div>
          <div class="spu-img">
            <picture v-if="row.image">
              <source :srcset="thumb(row.image, 120, 'avif')" type="image/avif" />
              <source :srcset="thumb(row.image, 120, 'webp')" type="image/webp" />
              <img :src="row.image" :alt="row.spu" width="120" height="120" loading="lazy" />
            </picture>
            <div v-else class="no-img">暂无图片</div>
          </div>
          <div class="spu-meta">
            <span class="m-spu">SPU: {{ row.spu }}<button
    class="copy-btn"
    :class="{ copied: copiedKey === row.spu + '|' + row.shop }"
    :title="copiedKey === row.spu + '|' + row.shop ? '已复制' : '复制 SPU'"
    @click.stop="copySpu(row)"
  ><svg v-if="copiedKey !== row.spu + '|' + row.shop" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><svg v-else viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></button></span>
            <span v-if="row.category">类目: {{ row.category }}</span>
            <a class="spu-analysis" @click="goAnalysis(row)">单品分析 →</a>
            <span class="m-full">店铺: {{ row.shop }}</span>
          </div>
          <div class="spu-avg">
            <div class="avg-row">
              <span class="avg-label">平均成交金额</span>
              <span class="avg-value">{{ averageMetrics(row).amount }}</span>
            </div>
            <div class="avg-row">
              <span class="avg-label">平均推广占比</span>
              <span class="avg-value">{{ averageMetrics(row).promotion_ratio }}</span>
            </div>
            <div class="avg-row">
              <span class="avg-label">平均 ROI</span>
              <span class="avg-value">{{ averageMetrics(row).roi }}</span>
            </div>
            <div class="avg-row">
              <span class="avg-label">平均成交客户数</span>
              <span class="avg-value">{{ averageMetrics(row).buyers }}</span>
            </div>
          </div>
        </div>

        <div class="spu-matrix-wrap">
          <table class="spu-matrix" v-if="metricSpecs.length">
            <thead>
              <tr>
                <th class="corner">指标 \ 日期</th>
                <th v-for="d in dayDates" :key="d" :title="d" :class="{ weekend: isWeekend(d) }">{{ d.slice(5) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in metricSpecs" :key="s.key as string">
                <td class="m-label">
                  <span class="m-ico sm"><MetricIcon :name="s.icon" /></span>{{ s.title }}
                </td>
                <td
                  v-for="d in dayDates"
                  :key="d"
                  :class="{ missing: metricsOn(row, d)?.[s.key as keyof MetricRecord] == null }"
                >
                  {{ fmtBy(s.format, metricsOn(row, d)?.[s.key as keyof MetricRecord]) }}
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="metrics-empty">未勾选指标，点击右上角「指标配置」选择要展示的指标</div>
        </div>
      </div>
    </div>

    <!-- 空态 / 加载态 -->
    <div v-if="!store.rows.length && store.loadingRows" class="empty-state">加载中…</div>
    <div v-else-if="!store.rows.length" class="empty-state">该日期范围内暂无数据（可尝试切换日期或清除搜索）</div>

    <!-- 触底加载更多哨兵 -->
    <div v-if="store.hasMore" ref="sentinelRef" class="sentinel"></div>
    <div v-if="store.loadingRows && store.rows.length" class="loading-more">加载更多…</div>
  </div>
  <div v-else class="page">
    <p>诊断页（bare）：若这里能正常最小化，说明问题出在网格 / store，而不是整体布局。</p>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* ---------- 工具条 ---------- */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 10px 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.tool-left {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #475569;
}
.search {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 12px;
  width: 240px;
  font-size: 14px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.search:focus {
  outline: none;
  border-color: #e1251b;
  box-shadow: 0 0 0 3px rgba(225, 37, 27, 0.08);
}
.tool-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.status {
  font-size: 13px;
  color: #94a3b8;
}
.status.ok {
  color: #10b981;
}
.btn {
  background: linear-gradient(135deg, #e1251b, #c81e14);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 8px 16px;
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(225, 37, 27, 0.25);
  transition: box-shadow 0.15s, transform 0.15s;
}
.btn:hover:not(:disabled) {
  box-shadow: 0 4px 12px rgba(225, 37, 27, 0.35);
  transform: translateY(-1px);
}
.btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.error {
  background: #fef2f2;
  color: #b91c1c;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 13px;
}
/* ---------- SPU 单元网格（单日模式） ---------- */
.spu-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 14px;
}
.spu-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  transition: box-shadow 0.18s, transform 0.18s;
}
.spu-card:hover {
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08);
  transform: translateY(-2px);
}
.spu-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px dashed #cbd5e1;
}
.spu-name {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 6px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.spu-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 13px;
  color: #64748b;
}
.spu-meta .m-spu {
  color: #2563eb;
}
.copy-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-left: 4px;
  padding: 0;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  border-radius: 4px;
  vertical-align: -3px;
  transition: color 0.15s, background 0.15s;
}
.copy-btn:hover {
  color: #e1251b;
  background: #fff1f0;
}
.copy-btn.copied {
  color: #16a34a;
}
/* 在 flex 换行容器里「独占一行」：flex-basis:100% 才会把它挤到下一行。
   注意 flex 子项上的 display:block 无效（会被 blockify 成 block 但不影响排布），
   必须在容器这一层用 flex-basis / width 控制。 */
.spu-meta .m-full {
  flex: 0 0 100%;
  width: 100%;
}
.spu-analysis {
  flex: none;
  color: #e1251b;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s, opacity 0.15s;
}
.spu-analysis:hover {
  border-bottom-color: #e1251b;
}
.spu-body {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.spu-img {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  border-radius: 8px;
  overflow: hidden;
}
.spu-img img {
  object-fit: contain;
}
.no-img {
  color: #cbd5e1;
  font-size: 14px;
}
/* 网格卡图片 180×180 */
.spu-card .spu-img {
  width: 180px;
  height: 180px;
}
.spu-card .spu-img img {
  width: 180px;
  height: 180px;
}
.spu-metrics {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  padding: 5px 0;
  border-bottom: 1px solid #cbd5e1;
  font-size: 14px;
}
.metric-row:last-child {
  border-bottom: none;
}
.m-label {
  color: #64748b;
  white-space: nowrap;
}
.m-value {
  color: #0f172a;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.m-value.missing {
  color: #64748b;
  font-weight: 400;
}
/* 指标行左侧：图标 + 名称 成组，hover 时图标放大变红 */
.m-left {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.m-ico {
  flex: none;
  width: 16px;
  height: 16px;
  color: #94a3b8;
  display: inline-flex;
  align-items: center;
  transition: transform 0.15s, color 0.15s;
}
.metric-row:hover .m-ico {
  color: #e1251b;
  transform: scale(1.18);
}
/* 矩阵表头（指标名）前的小图标 */
.m-ico.sm {
  width: 14px;
  height: 14px;
  margin-right: 6px;
  vertical-align: -2px;
  color: #94a3b8;
}
.metrics-empty {
  font-size: 13px;
  color: #94a3b8;
  padding: 8px 0;
}
/* ---------- SPU 整行矩阵（区间模式） ---------- */
.spu-rows {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.spu-row {
  display: flex;
  gap: 16px;
  align-items: stretch;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  transition: box-shadow 0.18s;
}
.spu-row:hover {
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08);
}
.spu-row-head {
  flex: none;
  width: 230px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: stretch;
  padding-right: 16px;
  border-right: 1px dashed #f1f5f9;
  box-sizing: border-box;
}
.spu-row-head .spu-name {
  margin-bottom: 0;
  -webkit-line-clamp: 3;
}
.spu-row-head .spu-img {
  width: 120px;
  height: 120px;
  align-self: center;
}
.spu-row-head .spu-img img {
  width: 120px;
  height: 120px;
}
.spu-meta .spu-analysis {
  margin-left: auto;
}
.spu-row-head .spu-avg {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed #f1f5f9;
  font-size: 13px;
}
.spu-row-head .spu-avg .avg-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.spu-row-head .spu-avg .avg-label {
  color: #64748b;
  white-space: nowrap;
}
.spu-row-head .spu-avg .avg-value {
  color: #0f172a;
  font-weight: 600;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.spu-matrix-wrap {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 6px 16px rgba(15, 23, 42, 0.05);
}
.spu-matrix {
  border-collapse: collapse;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.spu-matrix th,
.spu-matrix td {
  padding: 8px 12px;
  text-align: right;
  background: #fff;
  border-bottom: 1px solid #e6edf3;
  border-right: 1px solid #edf1f6;
}
.spu-matrix th:last-child,
.spu-matrix td:last-child {
  border-right: none;
}
.spu-matrix tbody tr:last-child td {
  border-bottom: none;
}
/* 表头（日期行）：更醒目 + 周末标红 */
.spu-matrix thead th {
  position: sticky;
  top: 0;
  background: #f1f5f9;
  color: #334155;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.2px;
  border-bottom: 2px solid #cbd5e1;
  z-index: 1;
}
.spu-matrix thead th.weekend {
  color: #e1251b;
}
.spu-matrix th.corner {
  text-align: left;
  left: 0;
  z-index: 2;
  background: #eef2f6;
  color: #e1251b;
  border-right: 2px solid #cbd5e1;
}
/* 斑马纹：隔行浅灰，阅读更省力 */
.spu-matrix tbody tr:nth-child(even) td {
  background: #f8fafc;
}
/* 首列（指标名，横向滚动时固定） */
.spu-matrix td.m-label {
  position: sticky;
  left: 0;
  background: #fff;
  text-align: left;
  color: #1e293b;
  font-weight: 600;
  border-right: 2px solid #cbd5e1;
  z-index: 1;
}
.spu-matrix tbody tr:nth-child(even) td.m-label {
  background: #f8fafc;
}
/* 悬停高亮：品牌红浅色 */
.spu-matrix tbody tr:hover td {
  background: #fdf1f0;
}
.spu-matrix tbody tr:hover td.m-label {
  background: #fdf1f0;
}
.spu-matrix td.missing {
  color: #94a3b8;
}
.empty-state {
  padding: 40px;
  text-align: center;
  font-size: 15px;
  color: #94a3b8;
}
.sentinel {
  height: 1px;
}
.loading-more {
  text-align: center;
  font-size: 14px;
  color: #94a3b8;
  padding: 12px;
}
@media (max-width: 640px) {
  .spu-row {
    flex-direction: column;
    align-items: stretch;
  }
  .spu-row-head {
    width: auto;
    border-right: none;
    border-bottom: 1px dashed #f1f5f9;
    padding-right: 0;
    padding-bottom: 12px;
  }
  .spu-body {
    flex-direction: column;
    align-items: stretch;
  }
  .spu-card .spu-img {
    align-self: center;
  }
}
</style>
