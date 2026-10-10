<script setup lang="ts">
// 推广预算优化页（/promo）：贪心分配方案 + 三数字卡片 + 四象限散点图 + SPU 决策表。
// 数据全部来自 /api/promo/analysis（后端已算清贪心分配、波动标记、四象限分档与建议文案）。
//
// ⚠️ 本页**不调 store.init()**、不写任何 Pinia store：店铺列表与日期边界都按路由自查 manifest。
//    与 SpuAnalysisView 同样的纪律——跨页依赖 store.moduleId，会在模块页 onUnmounted
//    触发 reset() 后凭空消失（表现为日期选择器拿不到 min/max、店铺下拉变空）。
//
// ⚠️ 术语纪律（见 CONTEXT.md）：一律「投产比」，⛔ 不出现「边际ROI」/「亏损」/「预测」。
//    边际 ROI 需要反事实数据（关掉某 SPU 的推广会怎样），平台不提供，算不出来；
//    投产比 = 推广成交金额 ÷ 推广花费，分子是收入不是利润，数据源不含毛利字段。
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { fetchManifest, fetchPromoAnalysis } from "../api";
import { fmtBy } from "../modules";
import { recentDaysRange } from "../utils/month";
import { useModuleStore } from "../stores/module";
import type { PromoAnalysis, PromoEmptyReason } from "../types";
import DateRangePicker from "../components/DateRangePicker.vue";
import PromoQuadrantChart from "../components/PromoQuadrantChart.vue";
import PromoSpuTable from "../components/PromoSpuTable.vue";
import {
  capNotice,
  checkDisclaimers,
} from "../modules/promoTable";

const router = useRouter();
const store = useModuleStore(); // 仅复用 manifest（店铺列表 + 日期边界），不调 init()

/** 推广数据所在的模块标识（后端默认也是它，但显式传更清晰）。 */
const PROMO_MODULE = "pop_spu_detail";

const data = ref<PromoAnalysis | null>(null);
const loading = ref(true);
const error = ref("");

const shop = ref("");
const start = ref("");
const end = ref("");
// true = 用户/默认值已确定范围，之后不再自动套用默认（点「全区间」清空后允许回到默认）
const rangeSet = ref(false);

/** 默认区间天数（Q11 决策：最近 30 天，不是自然月）。 */
const DEFAULT_WINDOW_DAYS = 30;

// 店铺列表与数据边界按路由自查 manifest：本页不调 store.init()，
// 离开模块页后 store.currentModule 会被 reset 掉，用它会恒为 null。
const manifestModule = computed(
  () => store.manifest?.modules.find((m) => m.module_id === PROMO_MODULE),
);
const shops = computed(() => manifestModule.value?.shops ?? []);
const fullRange = computed(() => manifestModule.value?.date_range ?? []);

/**
 * 默认统计区间：数据最新日往前 DEFAULT_WINDOW_DAYS 天（含最新日）。
 *
 * @returns {{start: string; end: string}} 默认区间；无数据边界时返回空区间。
 * @example
 * ```ts
 * defaultRange(); // { start: "2026-09-07", end: "2026-10-06" }
 * ```
 */
function defaultRange(): { start: string; end: string } {
  return recentDaysRange(DEFAULT_WINDOW_DAYS, fullRange.value[0], fullRange.value[1]);
}

/**
 * 拉取推广分析数据（按当前店铺与区间）。
 *
 * @returns {Promise<void>} 完成后 resolve；失败时也 resolve，错误写进 error 由模板展示。
 * @throws 不抛出——所有异常被捕获后转成 error 文本。
 * @remarks
 * ⚠️ 空态（`empty_reason` 非空）**不是错误**：后端仍返回 200，页面照常渲染数字 + 空态提示。
 * @example
 * ```ts
 * await load(); // 切换店铺、区间时调用
 * ```
 */
async function load() {
  loading.value = true;
  error.value = "";
  try {
    const r = rangeSet.value ? { start: start.value, end: end.value } : defaultRange();
    const r2 = await fetchPromoAnalysis({
      module_id: PROMO_MODULE,
      shop: shop.value || undefined,
      start: r.start || undefined,
      end: r.end || undefined,
    });
    data.value = r2;
    // 回填实际命中的区间（可能窄于请求区间，如请求跨到无数据的月份）
    start.value = r2.date_range[0] ?? "";
    end.value = r2.date_range[1] ?? "";
    rangeSet.value = true;
  } catch (e) {
    error.value = String(e);
    data.value = null;
  } finally {
    loading.value = false;
  }
}

/**
 * 日期区间变化回调（含「清空 = 全区间」）。
 *
 * @param {{start: string; end: string}} v - 新的起止日期；两者都为空串表示全区间。
 * @returns {void} 无返回值；内部触发重新加载。
 * @example
 * ```ts
 * onRangeChange({ start: "2026-09-01", end: "2026-09-30" });
 * ```
 */
function onRangeChange(v: { start: string; end: string }) {
  start.value = v.start;
  end.value = v.end;
  rangeSet.value = true;
  void load();
}

/**
 * 店铺切换回调。
 *
 * @param {Event} ev - select 的 change 事件。
 * @returns {void} 无返回值；内部触发重新加载。
 * @example
 * ```ts
 * onShopChange(ev); // ev.target.value = "钻芯旗舰店"
 * ```
 */
function onShopChange(ev: Event) {
  shop.value = (ev.target as HTMLSelectElement).value;
  void load();
}

/**
 * 确保 manifest 已加载（店铺下拉与日期边界都依赖它）。
 *
 * @returns {Promise<void>} 已有 manifest 时立即返回；拉取失败也 resolve（静默）。
 * @throws 不抛出——manifest 失败会在后续分析接口里体现，这里不中断流程。
 * @example
 * ```ts
 * await ensureManifest();
 * ```
 */
async function ensureManifest() {
  if (store.manifest) return;
  try {
    store.manifest = await fetchManifest();
  } catch {
    // 忽略：接口自身的错误会在后续请求里体现
  }
}

onMounted(async () => {
  await ensureManifest();
  // 默认落在第一家店：页面首屏不该因为没选店就空白
  if (!shop.value && shops.value.length) shop.value = shops.value[0];
  if (!rangeSet.value) {
    const r = defaultRange();
    if (r.start && r.end) {
      start.value = r.start;
      end.value = r.end;
      rangeSet.value = true;
    }
  }
  void load();
});

// 店铺清单迟到时补选第一家（直接输链接 + manifest 慢的场景）
watch(shops, (list) => {
  if (!shop.value && list.length) {
    shop.value = list[0];
    void load();
  }
});

// ---------- 三数字卡片 ----------
/**
 * 把金额格式化成「万元」口径的大数字，用于三数字卡片。
 *
 * @remarks
 * 理论上限 / 当前成交都在 10 万量级，直接显示 `¥355,673` 反而刺眼且不易比大小，
 * 卡片里统一折成「35.6 万」，把精确值放副标题。
 *
 * @param {number|null|undefined} v - 金额（元）。
 * @returns {string} 形如 "35.6 万"；无值时返回 "--"。
 * @example
 * ```ts
 * fmtWan(355673); // "35.6 万"
 * fmtWan(null);   // "--"
 * ```
 */
function fmtWan(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "--";
  if (Math.abs(v) < 10000) return fmtBy("currency", v);
  return (v / 10000).toFixed(1) + " 万";
}

/**
 * 卡片副标题里的精确金额（带千分位）。
 *
 * @param {number|null|undefined} v - 金额（元）。
 * @returns {string} 形如 "¥355,673"；无值时返回 "--"。
 * @example
 * ```ts
 * fmtExact(41260); // "¥41,261"
 * ```
 */
function fmtExact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "--";
  return fmtBy("currency", v);
}

/**
 * 现状是否已违反单品集中度上限（30%）。
 *
 * ⚠️ 后端给的是**判定结果**（`over_concentrated`），前端只渲染不重新判断 ——
 *    「哪些算超限」是业务口径（`current > cap`），在前端再实现一遍必然与后端漂移。
 *    非 null 时 gap < 0、达成率 > 1 是数学必然，不是 bug：
 *    现状把大部分预算压在少数品上，30% 上限要求把钱挪走，重分配后总成交必然下降。
 */
const overConc = computed(() => data.value?.over_concentrated ?? null);

/** 达成率（0~1）转百分比文本；分母 0 时后端给 null。 */
const rateText = computed(() => {
  const r = data.value?.achievement_rate;
  if (r == null) return "--";
  return (r * 100).toFixed(1) + "%";
});

/**
 * 达成率进度条宽度（0~100）。
 *
 * ⚠️ 现状违反 cap 时达成率 > 100%（可能 300%+），直接当百分比会让进度条撑破卡片，
 *    必须夹紧到 100。此时条是「满」的，但下面的文案会说明真实含义，不会误读。
 */
const ratePct = computed(() => {
  const r = data.value?.achievement_rate;
  if (r == null) return 0;
  return Math.max(0, Math.min(100, r * 100));
});

/**
 * 「可优化空间」卡片的标题与副标题。
 *
 * ⚠️ 现状违反 cap 时 gap 为负，字面说「可优化空间 −1,735 元」会被当成算法出错。
 *    此时改说「现状过度集中」，把负数转成「集中度约束下的成交差额」来讲。
 */
const gapCard = computed(() => {
  const d = data.value;
  if (!d) return { title: "可优化空间", sub: "" };
  if (overConc.value) {
    return {
      title: "重新分配的成交差额",
      sub: "按 30% 上限把预算挪走后，总成交会下降 —— 这是分散风险的代价，不是收益",
    };
  }
  return { title: "可优化空间", sub: "理论上限 − 当前实际（线性假设下的差额，非承诺收益）" };
});

// ---------- 模式切换（工单 08）----------
/**
 * 是否处于「优化模式」。默认false = 看板模式。
 *
 * ⚠️ 优化区用 `v-show` 而非 `v-if`：**决策表组件永不卸载**，
 *    里面的 chip 筛选与排序状态因此天然保留（工单验收项「切换不丢失筛选状态」）。
 *    换成 v-if 就得把picked/sort 提到父级再传下去，代码更绕且更容易漏。
 */
const optimize = ref(false);

/** 切换优化模式。 */
function toggleOptimize() {
  optimize.value = !optimize.value;
}

// ---------- 优化模式的解读文案（工单 08）----------
/**
 * 三处免责的展示视图（逐条 + 每条是否说清了要点）。
 *
 * ⚠️ 后端是唯一事实来源，这里只做**完整性自检**，⛔ 不改写文案。
 *    `allOk` 为 false 时页面显式报警—— spec §8 把三处定为合规硬要求，
 *    悄悄少显示一条比显示一条错文案更危险。
 */
const disclaim = computed(() => checkDisclaimers(data.value?.disclaimers));

/**
 * 单品集中度上限的解读文案（现状超限 / 有余额分不出去 / 仅陈述规则）。
 *
 * ⚠️ 判定依据是后端给的 `over_concentrated` 与 `unallocated`，⛔ 前端不自己算
 *    `current_cost > cap` ——「哪些算超限」是业务口径（cap = 总预算 × 30%），
 *    前端再实现一遍必然与后端漂移（同 compute_metrics 纪律）。
 */
const capRead = computed(() =>
  capNotice({
    overConcentrated: overConc.value,
    unallocated: data.value?.unallocated,
    cap: data.value?.cap,
    capText: fmtExact(data.value?.cap),
    unallocatedText: fmtExact(data.value?.unallocated),
  }),
);

/**
 * 「方案怎么读」的三步说明（看板给数字，优化模式解释数字的含义）。
 *
 * ⚠️ 数字全部取后端已算好的值，⛔ 不在前端做二次计算：
 *    保守估计 = ideal × BACKTEST_DISCOUNT，折扣是后端常量，
 *    前端若也写一遍 0.7，改了常量就会两处不一致（同 metrics.ts 纪律）。
 */
const howToRead = computed(() => {
  const d = data.value;
  if (!d) return [];
  return [
    {
      t: "① 现状",
      b: `当前推广花费 ${fmtExact(d.total_cost)}，参与分配的 SPU 带来推广成交 ${fmtExact(d.current_total_amount)}。投产比因品而异，逐品见下方决策表。`,
    },
    {
      t: "② 建议方案",
      b: `把总预算 ${fmtExact(d.total_cost)} 重新分配给投产比更高的单品，理论总成交可达 ${fmtExact(d.ideal_total_amount)}；按保守折扣估算约 ${fmtExact(d.conservative_total)}。`,
    },
    {
      t: "③ 怎么看差距",
      b: overConc.value
        ? "现状已超出单品集中度上限，重新分配会让总成交下降 —— 这是分散风险的代价，不是收益。"
        : `可优化空间 ${fmtExact(Math.abs(d.gap))}，达成率 ${rateText.value}（当前实际 ÷ 理论上限）。`,
    },
  ];
});

// ---------- 空态 ----------
/**
 * 空态提示文案表。
 *
 * @remarks
 * ⚠️ `too_few_spus` **不是报错**，是「部分可用」：贪心分配照常工作，
 *    只是四象限分位数不可信。把三态渲染成同一个「暂无数据」会丢掉这个区别，
 *    运营会以为算法坏了。
 */
const EMPTY_TEXT: Record<PromoEmptyReason, { title: string; hint: string; level: "info" | "warn" }> = {
  no_data: {
    title: "该店铺在所选区间内没有推广数据",
    hint: "请调整日期区间，或切换到其他店铺。",
    level: "info",
  },
  too_few_spus: {
    title: "有推广数据的 SPU 不足 4 个",
    hint: "贪心分配与建议仍然可用；四象限分档样本太少，分位数退化，参考价值有限。",
    level: "warn",
  },
  all_zero: {
    title: "该店铺在所选区间内推广花费全为 0",
    hint: "投产比无法计算，请调整日期区间。",
    level: "warn",
  },
};

/** 当前空态（数据完整时为 null）。 */
const empty = computed(() => {
  const r = data.value?.empty_reason;
  return r ? EMPTY_TEXT[r] : null;
});

/**
 * 返回模块页（工具条左侧入口）。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * goBack(); // 点「← 返回明细」
 * ```
 */
function goBack() {
  void router.push({ name: "module", params: { moduleId: PROMO_MODULE } });
}
</script>

<template>
  <div class="page" data-testid="promo-page">
    <!-- 工具条：返回 + 日期区间 + 店铺 + 手动刷新（对齐 ModuleView 的工具条形态） -->
    <div class="toolbar" data-testid="promo-toolbar">
      <div class="tool-left">
        <button class="btn btn-back" data-testid="promo-back-btn" @click="goBack">← 返回明细</button>
        <div class="tool-item">
          <span>统计区间</span>
          <DateRangePicker
            :start="start"
            :end="end"
            :min="fullRange[0]"
            :max="fullRange[1]"
            show-last-week
            @change="onRangeChange"
          />
        </div>
        <div class="tool-item" v-if="shops.length">
          <span>店铺</span>
          <select
            class="shop-select"
            data-testid="promo-shop-select"
            :value="shop"
            @change="onShopChange"
          >
            <option v-for="s in shops" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>
      <div class="tool-right">
        <button
          class="btn btn-mode"
          :class="{on: optimize}"
          data-testid="promo-mode-toggle"
          :aria-expanded="optimize"
          @click="toggleOptimize"
        >
          {{ optimize ? "▼ 回看板模式" : "▶ 优化模式" }}
        </button>
        <span v-if="data" class="status" data-testid="promo-range-status">
          数据区间：{{ data.date_range[0] || "--" }} ~ {{ data.date_range[1] || "--" }}
        </span>
        <span v-if="loading && data" class="reload-hint"><span class="mini-spin"></span>刷新中…</span>
      </div>
    </div>

    <!-- 优化模式（展开区）登场前就可见，不放在小模式里、不藏在折叠区里 -->
    <div
      v-show="optimize"
      class="opt-zone"
      data-testid="promo-optimize-zone"
    >
      <div class="oz-head">
        <span class="oz-title">优化模式</span>
        <span class="oz-sub">看板给数字，这里解释数字怎么读</span>
      </div>

      <!-- ① 方案怎么读：三步说清现状/建议/差距 -->
      <div class="steps" data-testid="promo-how-to-read">
        <div v-for="st in howToRead" :key="st.t" class="step">
          <div class="st-t">{{ st.t }}</div>
          <div class="st-b">{{ st.b }}</div>
        </div>
      </div>

      <!-- ② 集中度约束解读：分三态，诊断全部来自后端标志 -->
      <div class="cap-read" :class="capRead.level" data-testid="promo-cap-read">
        <div class="cr-t">{{ capRead.title }}</div>
        <div class="cr-b">{{ capRead.detail }}</div>
      </div>

      <!-- ③ 三处免责（spec §8 硬要求，缺一不可）常驻可见 -->
      <div class="disclaimers" data-testid="promo-disclaimers">
        <div class="dc-title">使用前必读</div>
        <div
          v-for="(d, i) in disclaim.views"
          :key="d.meta?.kind ?? i"
          class="dc-item"
          :class="[d.meta?.level, {bad: !d.ok}]"
          :data-testid="'promo-disclaimer-' + (d.meta?.kind ?? ('extra' + i))"
        >
          <span class="dc-tag">{{ d.meta?.title ?? "额外条目" }}</span>
          <span class="dc-text">{{ d.text || "（未获取到文案）" }}</span>
        </div>
        <!-- 完整性自检失败时显式报警：殉默少显一条比显示一条错文案更危险 -->
        <div v-if="!disclaim.allOk" class="dc-bad" data-testid="promo-disclaimer-alert">
          ⚠️ 免责说明不完整（应为 {{ disclaim.views.length }} 条）——
          报账方式已变更，请确认上线前三处说明均在页面上常驻展示。
        </div>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <!-- 初始加载遮罩 -->
    <transition name="pl-fade">
      <div v-if="loading && !data" class="page-loading">
        <div class="pl-spinner"></div>
        <div class="pl-text">分析计算中…</div>
      </div>
    </transition>

    <template v-if="data">
      <!-- 空态提示：三种 empty_reason 各自措辞。too_few_spus 是「部分可用」，不是报错 -->
      <div v-if="empty" class="empty-bar" :class="empty.level" data-testid="promo-empty-bar">
        <span class="eb-title">{{ empty.title }}</span>
        <span class="eb-hint">{{ empty.hint }}</span>
      </div>

      <!-- 三数字卡片（工单 05 的核心交付） -->
      <div class="cards" data-testid="promo-cards">
        <div class="card card-ideal">
          <div class="ctitle">理论上限总成交</div>
          <div class="card-value">{{ fmtWan(data.ideal_total_amount) }}</div>
          <div class="csub">
            {{ fmtExact(data.ideal_total_amount) }} · 按本店最高投产比重分配预算的数学最优
          </div>
        </div>
        <div class="card card-current">
          <div class="ctitle">当前实际总成交</div>
          <div class="card-value">{{ fmtWan(data.current_total_amount) }}</div>
          <div class="csub">
            {{ fmtExact(data.current_total_amount) }} · 推广花费 {{ fmtExact(data.total_cost) }}
          </div>
        </div>
        <div class="card card-gap" :class="{ reversed: overConc }">
          <div class="ctitle">{{ gapCard.title }}</div>
          <div class="card-value">{{ fmtWan(Math.abs(data.gap)) }}</div>
          <div class="csub">{{ gapCard.sub }}</div>
        </div>
        <div class="card card-rate">
          <div class="ctitle">达成率</div>
          <div class="card-value">{{ rateText }}</div>
          <div class="rate-track" role="presentation">
            <div class="rate-fill" :style="{ width: ratePct + '%' }"></div>
          </div>
          <div class="csub">
            <template v-if="overConc">
              现状超出 30% 上限，本就不在贪心的可行域内，此值 &gt; 100%
            </template>
            <template v-else>当前实际 ÷ 理论上限</template>
            · 保守估计 {{ fmtExact(data.conservative_total) }}
          </div>
        </div>
      </div>

      <!-- 预算封顶提示：SPU 数不足时必然剩钱分不出去，是数学必然而非 bug -->
      <div v-if="data.unallocated > 0.005" class="cap-note" data-testid="promo-cap-note">
        有 <b>{{ fmtExact(data.unallocated) }}</b> 元预算无法分配 —— 单品预算上限为总预算的 30%
       （{{ fmtExact(data.cap) }}），而可分配的 SPU 太少，剩余预算无处可去。
      </div>

      <!-- 超集中度提示：现状不在可行域内，是「约束的成本」而非算法出错 -->
      <div v-if="overConc" class="over-note" data-testid="promo-over-note">
        <b>{{ overConc }}</b> 的推广花费已超过单品上限（{{ fmtExact(data.cap) }} =
        总预算 × 30%）。<b>现状本身不满足 30% 集中度约束</b>，因此贪心方案必须把钱挪走 ——
        重新分配后总成交会从 {{ fmtExact(data.current_total_amount) }} 降到
        {{ fmtExact(data.ideal_total_amount) }}。这是分散风险的代价。
      </div>

      <!-- 四象限散点图（工单 06 实现内容，本工单只留容器防布局塌） -->
      <PromoQuadrantChart :analysis="data" />

      <!-- SPU 决策表（工单 07 实现内容，本工单只留容器防布局塌） -->
      <PromoSpuTable :analysis="data" />
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* ---------- 工具条（形态对齐 ModuleView，类名沿用同一套）---------- */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 10px 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.tool-left,
.tool-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-3);
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  padding: 7px 14px;
  font-size: 13px;
  color: var(--color-text-2);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn:hover {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.shop-select {
  padding: 6px 10px;
  font-size: 13px;
  color: var(--color-text-2);
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  outline: none;
  cursor: pointer;
}
.shop-select:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 2px var(--color-brand-tint-2);
}
.status {
  font-size: 12px;
  color: var(--color-text-4);
}
.error {
  background: var(--color-brand-tint);
  color: var(--color-brand-darker);
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 13px;
}
/* ---------- 空态提示条 ---------- */
/* info = 只是没数据；warn = 部分不可用（too_few_spus / all_zero），
   两者视觉上必须能一眼分开，否则运营会把「数据不足」误读成「系统报错」 */
.empty-bar {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  padding: 11px 16px;
  border-radius: var(--radius-xl);
  font-size: 13px;
}
.empty-bar.info {
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  color: var(--color-text-3);
}
.empty-bar.warn {
  background: var(--color-brand-tint-3);
  border: 1px solid var(--color-brand-border-2);
  color: var(--color-text-2);
}
.eb-title {
  font-weight: 600;
}
.empty-bar.info .eb-title {
  color: var(--color-text-2);
}
.empty-bar.warn .eb-title {
  color: var(--color-brand-darker);
}
.eb-hint {
  color: var(--color-text-4);
}
/* ---------- 三数字卡片 ---------- */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
}
.card {
  position: relative;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 14px 16px;
  overflow: hidden;
}
.card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  opacity: 0.85;
}
/* 左侧色条区分三个数字的性质：上限=品牌红（目标）、当前=中性（现状）、空间=绿（增量） */
.card-ideal::before {
  background: linear-gradient(180deg, var(--color-brand), rgba(225, 37, 27, 0.15));
}
.card-current::before {
  background: linear-gradient(180deg, var(--color-text-5), var(--color-border-3));
}
.card-gap::before {
  background: linear-gradient(180deg, var(--color-up), rgba(22, 163, 74, 0.15));
}
.card-rate::before {
  background: linear-gradient(180deg, var(--color-link), rgba(37, 99, 235, 0.15));
}
.ctitle {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-2);
  margin-bottom: 8px;
}
.card-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
}
.card-gap .card-value {
  color: var(--color-up);
}
/* 现状违反 cap 时 gap 为负：数字取绝对值展示，配色转为「警示」而非「收益绿」，
   否则一屏绿色 + 负数会互相打架 */
.card-gap.reversed::before {
  background: linear-gradient(180deg, var(--color-danger), rgba(239, 68, 68, 0.15));
}
.card-gap.reversed .card-value {
  color: var(--color-down);
}
.csub {
  font-size: 11.5px;
  color: var(--color-text-4);
  margin-top: 6px;
  line-height: 1.7;
}
/* 达成率进度条 */
.rate-track {
  height: 5px;
  background: var(--color-bg);
  border-radius: var(--radius-pill);
  margin: 9px 0 2px;
  overflow: hidden;
}
.rate-fill {
  height: 100%;
  background: var(--color-link);
  border-radius: var(--radius-pill);
  transition: width 0.4s ease;
}
/* ---------- 封顶提示 / 免责 ---------- */
.cap-note {
  padding: 10px 16px;
  font-size: 12.5px;
  color: var(--color-text-2);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-left: 3px solid var(--color-text-5);
  border-radius: var(--radius-md);
  line-height: 1.8;
}
.cap-note b {
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
}
.over-note {
  padding: 11px 16px;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--color-text-2);
  background: var(--color-brand-tint-3);
  border: 1px solid var(--color-brand-border-2);
  border-left: 3px solid var(--color-brand);
  border-radius: var(--radius-md);
}
.over-note b {
  color: var(--color-brand-darker);
}
/* ---------- 优化模式展开区（工单 08）---------- */
/* ⚠️ 本区用 v-show 控制展开，下面的决策表组件不会被卸载 */
.opt-zone {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.oz-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.oz-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
.oz-sub {
  font-size: 12.5px;
  color: var(--color-text-4);
}
/* ① 三步读法：左边编号标题，右边正文 */
.steps {
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.step {
  display: flex;
  gap: 10px;
  align-items: baseline;
}
.st-t {
  flex: none;
  width: 68px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-brand);
}
.st-b {
  flex: 1;
  /* 长文案不能撑宽标题列：长文本来就可以换行 */
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--color-text-2);
}
/* ② 集中度约束：warn 与 info 必须一眼可分，否则运营会当成普通提示 */
.cap-read {
  padding: 11px 14px;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  line-height: 1.85;
}
.cap-read.info {
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  color: var(--color-text-3);
}
.cap-read.warn {
  background: var(--color-brand-tint-3);
  border: 1px solid var(--color-brand-border-2);
  color: var(--color-text-2);
}
.cr-t {
  font-weight: 600;
  margin-bottom: 3px;
}
/* ③ 三处免责：只有大促那条是 warn，口径说明不与警示同权 */
.disclaimers {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 12px 14px;
  background: var(--color-surface-2);
  border: 1px dashed var(--color-border-3);
  border-radius: var(--radius-md);
}
.dc-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text-2);
  margin-bottom: 1px;
}
.dc-item {
  display: flex;
  gap: 9px;
  align-items: baseline;
  font-size: 12px;
  line-height: 1.9;
  color: var(--color-text-3);
}
.dc-tag {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11.5px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  color: var(--color-text-4);
}
.dc-item.warn .dc-tag {
  background: var(--color-brand-tint-2);
  border-color: var(--color-brand-border-2);
  color: var(--color-brand-darker);
  font-weight: 600;
}
.dc-text {
  flex: 1;
  /* 同理：文案长时换行而不撑容器 */
  min-width: 0;
}
/* 自检失败（文案缺要点）：必须看得见，不能混在正文里 */
.dc-item.bad .dc-text {
  color: var(--color-danger, #b91c1c);
  font-weight: 600;
}
.dc-bad {
  margin-top: 3px;
  padding: 8px 10px;
  border-radius: var(--radius-sm, 6px);
  background: var(--color-danger-tint, #fef2f2);
  border: 1px solid var(--color-danger, #b91c1c);
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-danger, #b91c1c);
}
/* 模式切换按钮的按下态 */
.btn-mode {
  color: var(--color-text-2);
}
.btn-mode.on {
  background: var(--color-brand);
  border-color: var(--color-brand);
  color: #fff;
}
/* ---------- 加载遮罩（与 SpuAnalysisView 同一套）---------- */
.page-loading {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(1.5px);
  -webkit-backdrop-filter: blur(1.5px);
}
.pl-spinner {
  width: 44px;
  height: 44px;
  border: 4px solid var(--color-brand-border-2);
  border-top-color: var(--color-brand);
  border-radius: var(--radius-circle);
  animation: pl-spin 0.8s linear infinite;
}
@keyframes pl-spin {
  to {
    transform: rotate(360deg);
  }
}
.pl-text {
  font-size: 14px;
  color: var(--color-text-3);
  letter-spacing: 0.5px;
}
.pl-fade-enter-active,
.pl-fade-leave-active {
  transition: opacity 0.25s ease;
}
.pl-fade-enter-from,
.pl-fade-leave-to {
  opacity: 0;
}
.reload-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-4);
}
.mini-spin {
  width: 13px;
  height: 13px;
  border: 2px solid var(--color-border-2);
  border-top-color: var(--color-brand);
  border-radius: var(--radius-circle);
  animation: pl-spin 0.8s linear infinite;
}
/* ---------- 尊重系统「减少动效」偏好（工单 08 验收项）----------
   不只关新增动效，而是把本页面已有的一并收口：
   旋转器（无限循环，永远不停）、进度条宽度过渡、遮罩淡入淡出、按钮与 chip 的颜色过渡。
   无限循环对前廊敏感用户是真正的问题（会引起眨晕），故必须停。*/
@media (prefers-reduced-motion: reduce) {
  /* 无限循环：两个转圈（大遮罩 + 小刷新提示）一律停 */
  .pl-spinner,
  .mini-spin {
    animation: none;
  }
  /* 遮罩淡入淡出（<transition name="pl-fade">）*/
  .pl-fade-enter-active,
  .pl-fade-leave-active {
    transition: none;
  }
  /* 达成率进度条宽度过渡 */
  .rate-fill {
    transition: none;
  }
  /* 按钮/字体颜色与背景过渡（按下变色不会让人眼疼，但一并关掉更干净） */
  .btn,
  .btn-mode,
  .chip {
    transition: none;
  }
}
</style>