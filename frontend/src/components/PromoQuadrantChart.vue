<script setup lang="ts">
// 四象限散点图：用「花费规模 × 投产比」解释算法为什么这样分配预算。
//
// ⚠️ 口径纪律（本组件最重要的约束）：
//   **分割线位置直接用后端给的 roi_threshold / cost_threshold，⛔ 不在前端重算分位数。**
//   分割线是「分档结论的图示」——如果前端自己算一遍 P50（哪怕公式看起来一样），
//   插值约定或样本筛选一旦有差，就会出现「点被染成高效档却在分割线下方」的
//   自相矛盾画面，而且只在特定数据下偶发，极难排查。
//   与 metrics.ts:aggregateMetrics 同一纪律：口径单一事实来源在后端，前端只渲染。
//
// ⚠️ 术语纪律（见 CONTEXT.md）：一律「投产比」，⛔ 不出现「边际ROI」/「亏损」/「预测」。
//
// ⚠️ 样本不足时（quadrant_reliable = false）**弱化展示而非隐藏数据**：
//   真实 3 家店的 SPU 数是 7 / 2 / 1，两家必然命中 too_few_spus。
//   中位数在样本 < 4 时退化成「自己跟自己比」，但贪心分配和建议仍然有效，
//   把整张图藏起来等于丢掉仍有价值的信息 —— 正确做法是淡化分割线 + 说清不可信。
import { computed } from "vue";
import { useRouter } from "vue-router";
import GenericChart from "./GenericChart.vue";
import { fmtBy } from "../modules";
import { QUAD_META, formatRoi, quadMeta } from "../modules/promoTable";
import { cssVar, withAlpha } from "../utils/cssVar";
import type { EChartsOption } from "echarts";
import type { PromoAnalysis, PromoSpu } from "../types";

const props = defineProps<{ analysis: PromoAnalysis }>();
const router = useRouter();

/** 推广数据所在模块（点击气泡跳转单品分析页时拼路由用）。 */
const PROMO_MODULE = "pop_spu_detail";

/**
 * 可参与分档的 SPU（投产比算得出来的）。
 *
 * ⚠️ roi 为 null 说明区间内推广花费为 0（投产比 = 成交 ÷ 花费是除零），
 *    画不进散点图。这类品在图下方单独列出并说明原因，而不是静默消失——
 *    运营看到「表里有这个品、图上没有」会以为数据丢了。
 */
const plottable = computed(() => props.analysis.spus.filter((s) => s.roi != null));

/** 无法计算投产比、因而被排除在图外的 SPU。 */
const unplottable = computed(() => props.analysis.spus.filter((s) => s.roi == null));

/**
 * 四象限分档的元信息与配色 token。
 *
 * ⚠️ 颜色只写 **token 名**，真实色值在 computed 里用 cssVar() 解析：
 *    echarts 把颜色当普通字符串写进 SVG 的 fill 属性，而 CSS 变量只对
 *    CSS 属性生效，属性值里写 var() 会被静默忽略（点变成黑色/透明）。
 *
 * 配色语义（与后端 promo.py 的 QUADRANT_* 一一对应，⛔ 不在前端另写中文枚举映射）：
 *   core      核心高效 = 高投产比 + 高花费 → 品牌红（要保住、要加投，最需要关注）
 *   potential 潜力待加 = 高投产比 + 低花费 → 蓝（值得加预算）
 *   loser     低效吞金 = 低投产比 + 高花费 → 灰（该削减，但不该报警）
 *   trial     观察新品 = 低投产比 + 低花费 → 琥珀（小额观察）
 */
const QUAD_ORDER = ["core", "potential", "loser", "trial"] as const;

/** 各档真实色值（从 design token 解析，见文件头「口径纪律」）。 */
const quadColor = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {};
  for (const k of QUAD_ORDER) {
    // 兜底色与 token 同值：token 读不到时图表仍可读，不至于全黑
    out[k] = cssVar(QUAD_META[k].token, "#94a3b8");
  }
  return out;
});

/**
 * 气泡面积 ∝ 推广成交金额。
 *
 * ⚠️ 用**平方根**而不是线性映射：面积正比于数值（sqrt 后取直径）才符合
 *    「面积代表数量」的视觉惯例。直接用金额当直径会让大额品的气泡
 *    视觉上压倒一切（半径翻倍 = 面积 4 倍），误导读者。
 *
 * @param {number} amount - 该 SPU 的推广成交金额（元）。
 * @param {number} maxAmount - 本店最大成交金额（用于归一化）。
 * @returns {number} 气泡直径（px），范围 [12, 46]。
 * @example
 * ```ts
 * bubbleSize(50000, 100000); // 约 37
 * bubbleSize(0, 100000);      // 12（下限）
 * ```
 */
function bubbleSize(amount: number, maxAmount: number): number {
  if (!(maxAmount > 0)) return 12;
  const ratio = Math.sqrt(Math.max(0, amount) / maxAmount);
  return Math.round(12 + ratio * 34);
}

/** 成交金额最大的可分档 SPU（气泡大小的归一化基准）。 */
const maxAmount = computed(() =>
  plottable.value.reduce((m, s) => Math.max(m, s.current_amount), 0),
);

/**
 * 把一个 SPU 变成散点数据项。
 *
 * ⚠️ 携带整个 spu 对象而不是只带 x/y：tooltip 与点击跳转都要用到
 *    名称、建议文案等字段，拆成散落的平行数组反而更容易对不上。
 *
 * @param {PromoSpu} s - 单个 SPU 明细。
 * @returns {{value: [number, number]; spu: PromoSpu}} echarts 散点数据项。
 * @example
 * ```ts
 * toPoint({ spu: "A", roi: 3.5, current_cost: 120, ... }); // { value: [120, 3.5], spu: {...} }
 * ```
 */
function toPoint(s: PromoSpu) {
  return { value: [s.current_cost, s.roi as number], spu: s };
}

/** 按档位分组的散点序列（四档各一个 series，图例与配色才好对应）。 */
const series = computed(() => {
  const color = quadColor.value;
  const rel = props.analysis.quadrant_reliable;
  return QUAD_ORDER.map((k) => {
    const items = plottable.value.filter((s) => s.quadrant === k);
    return {
      name: QUAD_META[k].label,
      type: "scatter" as const,
      data: items.map(toPoint),
      // 样本不足时整体降低不透明度，让「不可靠」在视觉上也说出来
      itemStyle: {
        color: withAlpha(color[k], rel ? 0.72 : 0.34),
        borderColor: color[k],
        borderWidth: 1.5,
      },
      symbolSize: (d: { data?: { spu?: PromoSpu } }) =>
        bubbleSize(d?.data?.spu?.current_amount ?? 0, maxAmount.value),
      // 分割线不在这里挂：四档都挂会画出四组线，由 option 统一挂到 core 档上
      emphasis: { focus: "series" as const },
    };
  });
});

/**
 * 四象限分割线（两条：横线 = 投产比 P50，竖线 = 花费 P50）。
 *
 * ⚠️ 阈值取自后端，**不在前端重算**（见文件头「口径纪律」）。
 * ⚠️ 只挂在第一个 series 上：重复画四条线会加粗成一片糊。
 * ⚠️ silent: true —— 分割线不该响应鼠标悬浮，否则 tooltip 会一直闪。
 * ⚠️ 样本不足时用虚线 + 更淡的颜色：图还留着，但一眼能看出这条线不可信。
 */
/**
 * 悬浮提示内容。
 *
 * ⚠️ 自己拼 HTML 而不用默认模板：默认模板只给 series 名 + x + y，
 *    运营需要的是「这个 SPU 叫什么、花了多少、投产比多少、算法怎么建议」。
 *
 * @param {unknown} p - echarts 悬浮事件参数（类型随配置变化，这里只取所需字段）。
 * @returns {string} HTML 片段；数据项缺 spu 时返回空串（不弹空框）。
 * @example
 * ```ts
 * tooltipFormatter({ data: { spu } }); // "<b>名称</b><br/>花费 …"
 * ```
 */
function tooltipFormatter(p: unknown): string {
  const spu = (p as { data?: { spu?: PromoSpu } })?.data?.spu;
  if (!spu) return "";
  const meta = spu.quadrant ? quadMeta(spu.quadrant) : null;
  const rows = [
    ["推广花费", fmtBy("currency", spu.current_cost)],
    ["投产比", formatRoi(spu.roi)],
    ["推广成交", fmtBy("currency", spu.current_amount)],
    ["建议分配", fmtBy("currency", spu.suggested_cost)],
  ];
  const line = (k: string, v: string) =>
    `<div style="display:flex;justify-content:space-between;gap:14px;line-height:1.8">
       <span style="color:${cssVar("--color-text-4", "#64748b")}">${k}</span>
       <span style="font-variant-numeric:tabular-nums">${v}</span>
     </div>`;
  return [
    `<div style="font-weight:600;margin-bottom:4px">${spu.spu_name || spu.spu}</div>`,
    spu.spu_name ? `<div style="color:${cssVar("--color-text-4", "#64748b")};font-size:11px;margin-bottom:5px">${spu.spu}</div>` : "",
    meta
      ? `<div style="margin-bottom:5px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${quadColor.value[spu.quadrant as string]};margin-right:5px"></span>${meta.label}</div>`
      : "",
    ...rows.map(([k, v]) => line(k, v)),
    spu.unstable
      ? `<div style="margin-top:4px;color:${cssVar("--color-amber", "#f59e0b")}">⚠ 投产比波动大</div>`
      : "",
    spu.advice
      ? `<div style="margin-top:5px;padding-top:5px;border-top:1px dashed ${cssVar("--color-border-2", "#e2e8f0")};color:${cssVar("--color-text-2", "#334155")}">${spu.advice}</div>`
      : "",
  ].join("");
}

/**
 * 点击气泡 → 跳该 SPU 的单品分析页。
 *
 * @param {unknown} p - echarts 点击事件参数。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * onChartClick({ data: { spu } }); // 路由跳到 /module/pop_spu_detail/spu/100273219974
 * ```
 */
function onChartClick(p: unknown) {
  const spu = (p as { data?: { spu?: PromoSpu } })?.data?.spu;
  if (!spu) return;
  void router.push({
    name: "spu-analysis",
    params: { moduleId: PROMO_MODULE, spu: spu.spu },
  });
}

/**
 * 坐标轴上界：留出余量，避免最大点贴在图框上被切掉一半。
 *
 * ⚠️ 无数据时返回 `undefined` 而不是 `null`：echarts 的 `max` 类型是
 *    `ScaleDataValue | undefined`，传 null 会让 vue-tsc 报类型错。
 *    `undefined` 的语义也正好是「交给echarts 自己定范围」。
 *
 * @param {number[]} vals - 数据值数组。
 * @param {number} ratio - 上界余量倍数（如 1.18 表示留 18%）。
 * @returns {number|undefined} 上界值；全为 0 或空数组时返回 undefined。
 * @example
 * ```ts
 * axisMax([100, 200], 1.18); // 236
 * axisMax([], 1.18);        // undefined
 * ```
 */
function axisMax(vals: number[], ratio: number): number | undefined {
  const max = Math.max(...vals, 0);
  if (max <= 0) return undefined;
  return max * ratio;
}

/**
 * 取两个候选上界里的较大者，容忍其中之一为 undefined。
 *
 * ⚠️ 分割线可能被坐标轴上界截断（样本极少时 P50 恰好等于 max），
 *    此时线要画在 max 上，所以轴上界必须「至少等于阈值」——
 *    否则线落到图外，运营看到的是「没有分割线」，而图例里还写着「中位 xx」。
 *
 * @param {number|undefined} axisMaxValue - 数据推出的上界。
 * @param {number|undefined} line - 分割线阈值。
 * @returns {number|undefined} 两者较大值；都无值时返回 undefined。
 * @example
 * ```ts
 * safeMax(200, 236); // 236（阈值在数据上界之外，取阈值）
 * safeMax(200, undefined); // 200
 * ```
 */
function safeMax(
  axisMaxValue: number | undefined,
  line: number | undefined,
): number | undefined {
  if (axisMaxValue == null) return line;
  if (line == null) return axisMaxValue;
  return Math.max(axisMaxValue, line);
}

const option = computed<EChartsOption>(() => {
  const d = props.analysis;
  const rt = d.roi_threshold;
  const ct = d.cost_threshold;
  const costMax = axisMax(plottable.value.map((s) => s.current_cost), 1.18);
  const roiMax = axisMax(plottable.value.map((s) => s.roi as number), 1.18);
  // 阈值线要落在坐标轴范围内才能看见；成本阈值在样本极少时可能等于 max
  const lineX = ct != null && costMax != null ? Math.min(ct, costMax) : undefined;
  const lineY = rt != null && roiMax != null ? Math.min(rt, roiMax) : undefined;

  return {
    // 分割线 label 走同一套 formatter，避免每条线各写一遍
    // 底部留 52px：x 轴名（nameGap 30）落在轴标签下方，容器要放得下
    grid: { left: 66, right: 30, top: 20, bottom: 56 },
    tooltip: {
      trigger: "item",
      formatter: tooltipFormatter,
      backgroundColor: cssVar("--color-surface", "#fff"),
      borderColor: cssVar("--color-border-2", "#e2e8f0"),
      textStyle: { color: cssVar("--color-text", "#1e293b"), fontSize: 12.5 },
      extraCssText: "box-shadow:0 4px 16px rgba(15,23,42,.12);border-radius:8px;",
    },
    xAxis: {
      type: "value",
      name: "推广花费（元）",
      // ⚠️ 不用 nameLocation:"end"：轴末端贴着 grid 右缘，文字会被容器裁掉
      //（实测只剩「推厂」两个字的残影）。居中放最稳。
      nameLocation: "middle",
      nameGap: 30,
      nameTextStyle: { color: cssVar("--color-text-4", "#64748b"), fontSize: 11 },
      max: safeMax(costMax, lineX),
      min: 0,
      axisLabel: {
        color: cssVar("--color-text-4", "#64748b"),
        fontSize: 11,
        formatter: (v: number) =>
          Math.abs(v) >= 10000 ? (v / 10000).toFixed(1) + " 万" : String(Math.round(v)),
      },
      axisLine: { lineStyle: { color: cssVar("--color-border-2", "#e2e8f0") } },
      splitLine: { lineStyle: { color: cssVar("--color-border-4", "#eef2f7") } },
    },
    yAxis: {
      type: "value",
      name: "投产比",
      nameLocation: "middle",
      nameGap: 38,
      nameTextStyle: { color: cssVar("--color-text-4", "#64748b"), fontSize: 11 },
      max: safeMax(roiMax, lineY),
      min: 0,
      axisLabel: {
        color: cssVar("--color-text-4", "#64748b"),
        fontSize: 11,
        formatter: (v: number) => v.toFixed(1),
      },
      axisLine: { lineStyle: { color: cssVar("--color-border-2", "#e2e8f0") } },
      splitLine: { lineStyle: { color: cssVar("--color-border-4", "#eef2f7") } },
    },
    series: series.value.map((s) =>
      s.name === QUAD_META.core.label
        ? {
            ...s,
            markLine: lineX == null && lineY == null ? undefined : buildMarkLine(lineX, lineY),
          }
        : s,
    ),
  };
});

/**
 * 组装分割线配置。
 *
 * 抽成函数是因为阈值可能被坐标轴上界截断（样本极少时 P50 = max），
 * 这种情况必须换成上界而不是画到图外 —— 画到图外的线用户根本看不到，
 * 却会以为「没有分割线」。
 *
 * @param {number|undefined} lineX - 横轴（花费）阈值，undefined 表示不画竖线。
 * @param {number|undefined} lineY - 纵轴（投产比）阈值，undefined 表示不画横线。
 * @returns {object|undefined} echarts markLine 配置；两条都不画时返回 undefined。
 * @example
 * ```ts
 * buildMarkLine(10029, 3.46); // { silent: true, data: [ ...2 条... ] }
 * buildMarkLine(undefined, undefined); // undefined
 * ```
 */
function buildMarkLine(
  lineX: number | undefined,
  lineY: number | undefined,
): Record<string, unknown> | undefined {
  if (lineX == null && lineY == null) return undefined;
  const rel = props.analysis.quadrant_reliable;
  return {
    // silent: 分割线不该响应悬浮，否则鼠标划过时 tooltip 反复闪
    silent: true,
    symbol: "none",
    lineStyle: {
      // ⚠️ 用 --color-text-5 而不是 --color-border-3：后者在白底上太淡
      //（实测几乎看不见，用户根本不知道有分割线），而分割线是这张图的核心语义。
      color: cssVar("--color-text-5", "#94a3b8"),
      // 样本不足时改虚线 + 变淡：图还留着，但一眼能看出这条线不可信
      type: rel ? ("solid" as const) : ("dashed" as const),
      width: rel ? 1.5 : 1.2,
      opacity: rel ? 0.9 : 0.6,
    },
    // ⚠️ 刻意**不挂 label**（实测踩过：两条线的 label 会同时挤到左上角、
    //    文字被旋转竖排、互相压在一起根本读不出来）。阈值数字改放在图下方的
    //    说明行里 —— 那里有完整横向空间，也不会被数据点遮挡。
    label: { show: false },
    data: [
      ...(lineX != null ? [{ xAxis: lineX }] : []),
      ...(lineY != null ? [{ yAxis: lineY }] : []),
    ],
  };
}

/**
 * 分割线阈值说明文案（图下方常驻一行）。
 *
 * ⚠️ 写在 DOM 里而不是画在图上：markLine 的 label 只有线的两端可放，
 *    一横一竖两条线的标签必然打架（实测会旋转竖排叠在左上角）。
 *
 * @returns {string} 形如「虚线为分割线：花费中位 ¥4,024 · 投产比中位 3.11」；
 *   无阈值时返回空串（此时图上也没有线）。
 * @example
 * ```ts
 * thresholdNote.value; // "虚线为分割线：花费中位 ¥4,024 · 投产比中位 3.11"
 * ```
 */
const thresholdNote = computed(() => {
  const d = props.analysis;
  const parts: string[] = [];
  if (d.cost_threshold != null) parts.push(`花费中位 ${fmtBy("currency", d.cost_threshold)}`);
  if (d.roi_threshold != null) parts.push(`投产比中位 ${fmtBy("decimal", d.roi_threshold)}`);
  if (!parts.length) return "";
  return `${d.quadrant_reliable ? "实线" : "虚线"}为分割线（本店 P50）：${parts.join(" · ")}`;
});
</script>

<template>
  <div class="chart-box" data-testid="promo-quadrant-box">
    <h3>
      <span>四象限分布</span>
      <span class="hint">横轴 = 推广花费 · 纵轴 = 投产比 · 气泡大小 = 推广成交金额</span>
    </h3>

    <!-- 样本不足时先说清不可信：退化成「自己跟自己比」，但图仍保留（贪心结果有效） -->
    <div v-if="!analysis.quadrant_reliable" class="weak-bar" data-testid="promo-quadrant-weak">
      有推广数据的 SPU 仅 {{ analysis.spus.length }} 个（少于 4 个），四象限分档的阈值退化为
      「自己跟自己比」，下方散点位置与分割线仅供参考，不宜据此下结论。
    </div>

    <div class="legend">
      <span v-for="k in QUAD_ORDER" :key="k" class="lg-item" :class="'q-' + k">
        <span class="dot"></span>{{ QUAD_META[k].label }}
        <span class="lg-hint">{{ QUAD_META[k].hint }}</span>
      </span>
    </div>

    <!-- 无可分档样本：整图不画，但要说清原因（而不是留一块空白） -->
    <div v-if="!plottable.length" class="empty" data-testid="promo-quadrant-empty">
      该店铺在所选区间内没有可计算投产比的 SPU（推广花费为 0 时投产比是除零），
      无法绘制四象限图。
    </div>

    <GenericChart
      v-else
      class="plot"
      :option="option"
      height="340px"
      data-testid="promo-quadrant-plot"
      @click="onChartClick"
    />

    <!-- 被排除在图外的品要列出来：表里有、图上没有，不说明会被当成数据丢了 -->
    <div v-if="unplottable.length" class="excluded" data-testid="promo-quadrant-excluded">
      以下 {{ unplottable.length }} 个 SPU 未画入图中（投产比无法计算）：
      <b v-for="s in unplottable" :key="s.spu" class="ex-item">{{ s.spu_name || s.spu }}</b>
    </div>

    <!-- 分割线阈值：写在图下而不是画在线上（markLine label 两条线会打架） -->
    <div v-if="thresholdNote" class="thr-note" data-testid="promo-quadrant-thresholds">
      {{ thresholdNote }}
    </div>

    <!-- 免责标注：常驻小字。四象限只解释算法分配，不是决策依据 -->
    <div class="foot" data-testid="promo-quadrant-foot">
      分档仅用于解释算法分配，非决策依据 —— 阈值取本店分位数（P50），跨店不可直接比较。
      点击气泡可查看该 SPU 的单品分析。
    </div>
  </div>
</template>

<style scoped>
.chart-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 14px 18px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.chart-box h3 {
  margin: 0 0 4px;
  font-size: 14px;
  color: var(--color-text-2);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
}
.hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-4);
}
.weak-bar {
  margin: 10px 0 2px;
  padding: 9px 13px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--color-text-2);
  background: var(--color-brand-tint-3);
  border: 1px solid var(--color-brand-border-2);
  border-left: 3px solid var(--color-brand);
  border-radius: var(--radius-md);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin: 12px 0 4px;
  font-size: 12px;
  color: var(--color-text-2);
}
.lg-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: var(--radius-circle);
  flex: none;
}
.lg-hint {
  color: var(--color-text-4);
  font-size: 11px;
}
.q-core .dot {
  background: var(--color-brand);
}
.q-potential .dot {
  background: var(--color-link);
}
.q-loser .dot {
  background: var(--color-text-5);
}
.q-trial .dot {
  background: var(--color-amber);
}
.plot {
  margin-top: 6px;
  cursor: pointer;
}
.empty {
  margin-top: 10px;
  padding: 26px 16px;
  text-align: center;
  font-size: 13px;
  line-height: 1.9;
  color: var(--color-text-4);
  background: var(--color-surface-3);
  border: 1px dashed var(--color-border-3);
  border-radius: var(--radius-lg);
}
.excluded {
  margin-top: 8px;
  font-size: 11.5px;
  line-height: 1.9;
  color: var(--color-text-4);
}
.ex-item {
  display: inline-block;
  margin: 0 6px 0 2px;
  padding: 1px 7px;
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-3);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-pill);
}
.thr-note {
  margin-top: 6px;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-3);
  font-variant-numeric: tabular-nums;
}
.foot {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.8;
  color: var(--color-text-4);
}
</style>
