<script setup lang="ts">
// 区间汇总面板（方案 C · 工单 01 骨架 + 工单 02 环比 + 工单 03 结构区）：插在模块页工具条正下方。
// 5 张核心 KPI 卡（成交金额 / 成交单量 / 访客数 / ROI / 退货率），
// 每卡 = 区间总值（CountUp）+ 环比上期小字（方向色，反转类由 compareDelta 折算）
// + 逐日 sparkline（纯 SVG polyline，series 来自后端现算）。
// 面板标题写明统计范围（N 家店铺 / N 个 SPU / N 天），本身就是「汇总跟随筛选」的口径自检器。
// 状态：加载中 = 骨架屏；无命中数据 = 整体隐藏；加载失败 = 面板内占位（不阻塞下方列表）。
import { computed } from "vue";
import { useModuleStore } from "../stores/module";
import CountUp from "./CountUp.vue";
import { compareDelta, getMetric, kpiValueOf, withBarWidth } from "../metrics";
import { fmtBy, thumb } from "../modules";
import type { CompareSpec, MetricFormat } from "../metrics";

const store = useModuleStore();

/** KPI 卡定义：key 对应 total 字段（return_rate 例外，前端由总量相除派生） */
interface KpiSpec {
  key: string;
  label: string;
  format: MetricFormat;
  /** 折线/圆点配色 */
  color: string;
}

/**
 * 5 张核心 KPI 卡（配色 = 方案 C 预览稿定稿）。
 * - label：面板专用短名（可与 METRICS.title 不同）；
 * - color：全部经 token / METRICS SSOT 取值，不写字面 hex——
 *   成交金额是 hero 卡，用品牌红 token 与卡片红底红字强调统一；
 *   成交单量 / 访客数 / ROI 取各自 METRICS 趋势色（与全站图表一致）；
 *   退货率属退款族，沿用退款金额的 METRICS 红。
 */
const KPIS: KpiSpec[] = [
  { key: "amount", label: "成交金额", format: "currency", color: "var(--color-brand)" },
  { key: "orders", label: "成交单量", format: "number", color: getMetric("orders")!.color },
  { key: "visitors", label: "访客数", format: "number", color: getMetric("visitors")!.color },
  { key: "roi", label: "ROI", format: "decimal", color: getMetric("roi")!.color },
  { key: "return_rate", label: "退货率", format: "percent", color: getMetric("refund_amount")!.color },
];

/**
 * 取某 KPI 的区间总值（委托纯函数 kpiValueOf：含退货率口径分支，组件不自己判定）。
 *
 * @param {string} key - KPI key。
 * @returns {number | null} 总值；无数据 / 分母为 0 时为 null（CountUp 显示 "--"）。
 */
function totalOf(key: string): number | null {
  return kpiValueOf(store.aggregate?.total, key);
}

/** 是否单日模式（起止同一天）：环比文案随之切换为「较前一天」 */
const singleDay = computed(() => !!store.start && store.start === store.end);

/** 各 KPI 的环比描述（vs 上一等长区间），由纯函数 compareDelta 折算，模板只渲染 */
const cmpCache = computed(() => {
  const a = store.aggregate;
  const out: Record<string, CompareSpec> = {};
  for (const k of KPIS) {
    out[k.key] = compareDelta(
      k.key,
      k.format,
      kpiValueOf(a?.total, k.key),
      kpiValueOf(a?.prev, k.key),
      singleDay.value,
    );
  }
  return out;
});

/**
 * 某 KPI 的逐日值（sparkline 数据源）。
 *
 * @param {string} key - KPI key（series 点的字段名，5 项与后端契约一一对应）。
 * @returns {Array<number | null>} 与 series 等长的逐日值数组。
 */
function seriesOf(key: string): Array<number | null> {
  return (store.aggregate?.series ?? []).map(
    (p) => (p as unknown as Record<string, number | null>)[key] ?? null,
  );
}

/**
 * 各 KPI 的 sparkline 几何缓存（viewBox 120×30，留 2px 边距防描边裁切）。
 *
 * @remarks
 * null（当日无数据 / 比率分母为 0）直接跳过——折线跨过缺口而不是砸到 0，
 * 避免「缺数据日」被误读成「暴跌」。有效点 < 2 时 points 为空串，模板不渲染折线。
 * 全部值相同时 span 兜底为 1，y 恒为中线，不会除零。
 */
const sparkCache = computed(() => {
  const out: Record<string, { points: string; last: { x: number; y: number } | null; count: number }> = {};
  for (const k of KPIS) {
    const vals = seriesOf(k.key).filter((v): v is number => v != null);
    if (vals.length < 2) {
      out[k.key] = { points: "", last: null, count: vals.length };
      continue;
    }
    const W = 120;
    const H = 30;
    const PAD = 2;
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1; // 全相同值时 span=1，y 恒为中线，不会除零
    const stepX = (W - PAD * 2) / (vals.length - 1);
    const pts = vals.map((v, i) => {
      const x = PAD + i * stepX;
      // SVG y 轴向下：值越大 y 越小
      const y = H - PAD - ((v - min) / span) * (H - PAD * 2);
      return { x, y };
    });
    out[k.key] = {
      points: pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
      last: pts[pts.length - 1],
      count: vals.length,
    };
  }
  return out;
});

/**
 * TOP 贡献榜（与店铺对比同构）：后端 top_spus 已给金额与 share，这里只经纯函数 withBarWidth
 * 附加条宽百分比，组件里只剩渲染。
 */
const topSpus = computed(() => withBarWidth(store.aggregate?.top_spus ?? []));

/** 店铺间对比：同 withBarWidth 附加条宽。后端在带 shop 过滤时返回空数组，故此卡自动无数据。 */
const shopTotals = computed(() => withBarWidth(store.aggregate?.shop_totals ?? []));

/** 店铺对比卡是否展示：仅「全部店铺」视图（未选店）且有店铺数据时出现 */
const showShopCard = computed(() => !store.shop && shopTotals.value.length > 0);

/** 面板标题的统计范围文案（含当前店铺名，明示汇总口径） */
const scopeText = computed(() => {
  const a = store.aggregate;
  if (!a) return "";
  const shopPart = store.shop ? `「${store.shop}」` : "全部店铺";
  return `${shopPart} · ${a.counts.shops} 家店铺 / ${a.counts.spus} 个 SPU / ${a.days} 天`;
});

/** 是否渲染面板：加载中(骨架) / 有数据 / 出错占位；无命中数据时整体隐藏 */
const visible = computed(
  () => store.loadingAggregate || !!store.aggregateError || (store.aggregate?.counts.spus ?? 0) > 0,
);
</script>

<template>
  <section v-if="visible" class="agg-panel" data-testid="module-aggregate-panel" aria-label="区间汇总">
    <!-- 加载失败：面板内占位，不阻塞下方列表（即便残留旧数据也如实报错，不掩盖失败） -->
    <div v-if="store.aggregateError" class="agg-error">
      汇总加载失败：{{ store.aggregateError }}
    </div>

    <!-- 骨架屏：首次加载（尚无数据）时显示 -->
    <template v-else-if="store.loadingAggregate && !store.aggregate">
      <div class="agg-head">
        <span class="agg-title">区间汇总</span>
        <span class="agg-scope skel-line" style="width: 220px"></span>
      </div>
      <div class="agg-cards">
        <div v-for="k in KPIS" :key="k.key" class="agg-card skel">
          <div class="skel-line" style="width: 56px"></div>
          <div class="skel-line big" style="width: 90px"></div>
          <div class="skel-line" style="width: 100%; height: 30px"></div>
        </div>
      </div>
    </template>

    <template v-else-if="store.aggregate">
      <div class="agg-head">
        <span class="agg-title">区间汇总</span>
        <span class="agg-scope">{{ scopeText }}</span>
      </div>
      <div class="agg-cards">
        <div v-for="k in KPIS" :key="k.key" class="agg-card">
          <div class="k-label">{{ k.label }}</div>
          <div class="k-val">
            <CountUp :value="totalOf(k.key)" :format="k.format" />
          </div>
          <!-- 环比小字：与上一等长区间比较；prev 缺失 / 字段 null / 相对变化分母 0 时整块不渲染 -->
          <div
            v-if="cmpCache[k.key].present"
            class="k-cmp"
            :class="'tone-' + cmpCache[k.key].tone"
          >
            <span class="cmp-arrow">{{ cmpCache[k.key].arrow }}</span>
            <span class="cmp-text">{{ cmpCache[k.key].text }}</span>
            <span class="cmp-label">{{ cmpCache[k.key].label }}</span>
          </div>
          <!-- sparkline：逐日趋势，纯装饰（数值已有文本等价物），null 日跨过不砸 0 -->
          <!-- 描边/填充走 :style 内联而非属性：k.color 可能是 var(--color-brand)，属性形式对 var() 解析不可靠 -->
          <svg
            v-if="sparkCache[k.key].points"
            class="k-spark"
            width="120"
            height="30"
            viewBox="0 0 120 30"
            aria-hidden="true"
          >
            <polyline :points="sparkCache[k.key].points" fill="none" :style="{ stroke: k.color }" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
            <circle
              v-if="sparkCache[k.key].last"
              :cx="sparkCache[k.key].last!.x"
              :cy="sparkCache[k.key].last!.y"
              r="2.5"
              :style="{ fill: k.color }"
            />
          </svg>
        </div>
      </div>

      <!-- 结构区双卡（工单 03）：TOP 5 贡献榜（品牌红条）+ 店铺间对比（紫条，点击下钻单店）。
           指定店铺时后端不返回 shop_totals → 店铺卡隐藏 → TOP 卡自动占满整行。 -->
      <div v-if="topSpus.length" class="agg-structure">
        <div class="st-card">
          <div class="st-title">成交金额 TOP {{ topSpus.length }} 贡献</div>
          <ul class="st-list">
            <li v-for="t in topSpus" :key="t.shop + '|' + t.spu" class="st-row">
              <picture v-if="t.image" class="st-img">
                <source :srcset="thumb(t.image, 96, 'avif')" type="image/avif" />
                <source :srcset="thumb(t.image, 96, 'webp')" type="image/webp" />
                <img :src="t.image" :alt="t.spu_name || t.spu" loading="lazy" />
              </picture>
              <div class="st-body">
                <div class="st-name" :title="t.spu_name || t.spu">{{ t.spu_name || t.spu }}</div>
                <div class="st-bar st-bar-top"><div class="st-bar-fill" :style="{ width: t.width + '%' }"></div></div>
              </div>
              <div class="st-num">
                <span class="st-amount">{{ fmtBy("currency", t.amount) }}</span>
                <span class="st-share">{{ t.share == null ? "--" : fmtBy("percent", t.share) }}</span>
              </div>
            </li>
          </ul>
        </div>

        <div v-if="showShopCard" class="st-card">
          <div class="st-title">店铺间成交对比</div>
          <ul class="st-list">
            <li
              v-for="s in shopTotals"
              :key="s.shop"
              class="st-row st-click"
              role="button"
              tabindex="0"
              :title="'切到「' + s.shop + '」查看该店明细'"
              :aria-label="'查看「' + s.shop + '」区间明细'"
              @click="store.setShop(s.shop)"
              @keydown.enter="store.setShop(s.shop)"
              @keydown.space.prevent="store.setShop(s.shop)"
            >
              <div class="st-body">
                <div class="st-name" :title="s.shop">{{ s.shop }}</div>
                <div class="st-bar st-bar-shop"><div class="st-bar-fill" :style="{ width: s.width + '%' }"></div></div>
              </div>
              <div class="st-num">
                <span class="st-amount">{{ fmtBy("currency", s.amount) }}</span>
                <span class="st-share">{{ s.share == null ? "--" : fmtBy("percent", s.share) }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.agg-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 12px 16px 14px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.agg-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.agg-title {
  font-size: calc(14px * var(--font-scale));
  font-weight: 700;
  color: var(--color-text-strong);
}
.agg-scope {
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-5);
}
.agg-error {
  font-size: calc(13px * var(--font-scale));
  color: var(--color-danger);
  padding: 4px 0;
}
/* KPI 条：auto-fit 换行，窄屏不破版不横向滚动 */
.agg-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px;
}
.agg-card {
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-4);
  border-radius: 10px;
  padding: 10px 12px;
  min-width: 0; /* 允许作为 grid 项收缩，环比小字再长也不撑破列宽 */
}
.k-label {
  font-size: calc(11.5px * var(--font-scale));
  color: var(--color-text-5);
  margin-bottom: 3px;
}
.k-val {
  font-size: calc(19px * var(--font-scale));
  font-weight: 700;
  color: var(--color-text-strong);
  line-height: 1.2;
}
/* 环比小字：箭头+差值染方向色，标签恒灰；tabular-nums 防数字抖动 */
.k-cmp {
  margin-top: 3px;
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-size: calc(11px * var(--font-scale));
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}
.cmp-arrow {
  font-size: calc(10px * var(--font-scale));
}
.cmp-label {
  color: var(--color-text-5);
  font-size: calc(10.5px * var(--font-scale));
}
/* 方向色 good=绿 / bad=红 / flat=灰；反转类(退货率等)的升降好坏已由 compareDelta 折算进 tone */
.k-cmp.tone-good {
  color: var(--color-up);
}
.k-cmp.tone-bad {
  color: var(--color-down);
}
.k-cmp.tone-flat {
  color: var(--color-text-5);
}
/* ---------- 结构区双卡（工单 03）：TOP 贡献榜 + 店铺间对比 ---------- */
.agg-structure {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.st-card {
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-4);
  border-radius: 10px;
  padding: 10px 12px;
  min-width: 0;
}
.st-title {
  font-size: calc(12.5px * var(--font-scale));
  font-weight: 700;
  color: var(--color-text-strong);
  margin-bottom: 8px;
}
.st-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.st-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.st-img {
  width: 34px;
  height: 34px;
  border-radius: 6px;
  flex: 0 0 auto;
  overflow: hidden;
  background: var(--color-border-4);
}
.st-img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.st-body {
  flex: 1 1 auto;
  min-width: 0;
}
/* 商品名过长省略号截断，全名走 title 提示 */
.st-name {
  font-size: calc(12px * var(--font-scale));
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}
.st-bar {
  height: 7px;
  border-radius: 4px;
  background: var(--color-border-4);
  overflow: hidden;
}
.st-bar-fill {
  height: 100%;
  border-radius: 4px;
}
.st-bar-top .st-bar-fill {
  background: linear-gradient(90deg, var(--color-brand), var(--color-brand-dark));
}
.st-bar-shop .st-bar-fill {
  background: linear-gradient(90deg, var(--color-violet), var(--color-violet-dark));
}
.st-num {
  flex: 0 0 auto;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.st-amount {
  display: block;
  font-size: calc(12.5px * var(--font-scale));
  font-weight: 600;
  color: var(--color-text-strong);
}
.st-share {
  display: block;
  font-size: calc(11px * var(--font-scale));
  color: var(--color-text-5);
}
/* 店铺条：可点击下钻单店，鼠标指针 + 悬停/键盘聚焦反馈 */
.st-click {
  cursor: pointer;
  border-radius: 6px;
  padding: 2px 4px;
  margin: 0 -4px;
  transition: background-color 0.15s ease;
}
.st-click:hover {
  background: var(--color-surface); /* 白底在 surface-2 灰卡上「浮起」，比 border-4 对比明显 */
}
.st-click:focus-visible {
  outline: 2px solid var(--color-violet);
  outline-offset: 1px;
}
/* 成交金额卡：品牌红强调（与预览稿一致，主指标突出） */
.agg-card:first-child .k-val {
  color: var(--color-brand-darker);
}
.agg-card:first-child {
  background: var(--color-brand-tint);
  border-color: var(--color-brand-border);
}
.k-spark {
  display: block;
  margin-top: 6px;
  width: 100%;
  max-width: 140px;
  height: 30px;
}
/* 骨架屏 shimmer */
.skel .skel-line,
.skel-line {
  height: 10px;
  border-radius: 4px;
  background: var(--color-border-4);
  display: inline-block;
}
.skel-line.big {
  height: 20px;
  margin: 6px 0;
}
.skel {
  min-height: 96px;
}
.skel .skel-line {
  animation: agg-shimmer 1.4s ease-in-out infinite;
}
@keyframes agg-shimmer {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}
/* 系统开启「减少动态效果」时骨架屏不闪动、店铺条悬停过渡静止 */
@media (prefers-reduced-motion: reduce) {
  .skel .skel-line {
    animation: none;
  }
  .st-click {
    transition: none;
  }
}
</style>
