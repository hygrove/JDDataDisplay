<script setup lang="ts">
// 单品分析页：单个 SPU 的指标卡 + 趋势图 + 工作日/节假日对比 + 综合分析。
// 数据全部来自 /api/module/:moduleId/spu/:spu/analysis；
// 时间范围默认「最近一周」（以数据最新日为终点往前 7 天，如最新日 2026-09-17 -> 2026-09-10 ~ 2026-09-17），
// 可通过日期选择器自定义（后端重新聚合）；数据不足一周时被最早日期截断。
//
// 展示哪些指标与模块页共用同一份配置（stores/metrics.ts + 顶部的「指标配置」按钮）：
// 指标卡、趋势图、工作日/节假日对比行三者都按勾选结果动态生成。
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchManifest, fetchSpuAnalysis } from "../api";
import { fmtBy } from "../modules";
import { recentDaysRange } from "../utils/month";
import { useModuleStore } from "../stores/module";
import { useMetricsStore } from "../stores/metrics";
import { useUiStore } from "../stores/ui";
import { aggregateMetrics, sumRawMetrics, metricsOf, METRICS, METRIC_MAP, type MetricFormat } from "../metrics";
import type { MetricSpec } from "../metrics";
import type { CompareGroup, SpuAnalysis } from "../types";
import GenericChart from "../components/GenericChart.vue";
import DateRangePicker from "../components/DateRangePicker.vue";
import MetricConfigPanel from "../components/MetricConfigPanel.vue";
import MetricIcon from "../components/MetricIcon.vue";
import CountUp from "../components/CountUp.vue";
import type { EChartsOption } from "echarts";

const props = defineProps<{ moduleId: string; spu: string }>();
const route = useRoute();
const router = useRouter();
const store = useModuleStore(); // 仅复用 manifest（date_range 做日期边界）
const metricsStore = useMetricsStore();
const ui = useUiStore();

const shop = computed(() => (route.query.shop as string) || "");
const data = ref<SpuAnalysis | null>(null);
const loading = ref(true);
const error = ref("");

// 数据可用区间必须从「路由上的 moduleId」查 manifest：
// 本页不调 store.init()，且离开模块页会 store.reset()（moduleId 清空），
// 用 store.currentModule 会恒为 null → 日期选择器拿不到 min/max、默认「近一周」也算不出来。
const fullRange = computed(
  () => store.manifest?.modules.find((m) => m.module_id === props.moduleId)?.date_range ?? [],
);

// 日期范围：默认「最近一周」（入参由 defaultRange() 计算；用户手动改过之后不再覆盖）
const DEFAULT_WINDOW_DAYS = 7;
const start = ref("");
const end = ref("");
// true = 已由用户/默认值确定范围，不再自动套用默认（点「全区间」清空后允许再次默认为空->全区间）
const rangeSet = ref(false);

/** 默认统计区间：数据最新日往前 DEFAULT_WINDOW_DAYS 天（含最新日）。 */
function defaultRange(): { start: string; end: string } {
  return recentDaysRange(DEFAULT_WINDOW_DAYS, fullRange.value[0], fullRange.value[1]);
}

/**
 * 由原始图片 URL 推导缩略图 URL。
 *
 * @remarks
 * 本页 hero 图只有 96px，直接下原图（PNG 常 0.9~3MB）纯属浪费带宽，
 * 故改走批处理预生成的 AVIF 缩略图（WebP 兜底），见 backend/jobs/make_thumbs.py。
 *
 * @param {string | null | undefined} orig - 原图访问路径，形如 "/images/100123.png"；
 *      为空时返回空串（模板里 <picture> 会走「暂无图片」分支）。
 * @param {number} size - 缩略图尺寸（96 / 120 / 180），需与后端生成的目录一致。
 * @param {"avif" | "webp"} [fmt="avif"] - 缩略图格式；默认 avif（体积最小）。
 * @returns {string} 缩略图访问路径，形如 "/thumbs/96/100123.avif"。
 * @example
 * ```ts
 * thumb("/images/100123.png", 96);       // "/thumbs/96/100123.avif"
 * thumb("/images/100123.png", 96, "webp"); // "/thumbs/96/100123.webp"
 * ```
 */
function thumb(orig: string | null | undefined, size: number, fmt: "avif" | "webp" = "avif"): string {
  if (!orig) return "";
  // 去掉 "/images/" 前缀得到文件名，再去掉扩展名得到 stem（即 SPU 号）
  const base = orig.replace("/images/", "");
  const stem = base.replace(/\.[^.]+$/, "");
  return `/thumbs/${size}/${stem}.${fmt}`;
}

/**
 * 拉取单品分析数据（按当前路由、店铺、区间）。
 *
 * @remarks
 * 后端是「读已缓存扁平 JSON + 按需聚合」，不是整库重算，所以切区间很快。
 *
 * @returns {Promise<void>} 完成后 resolve；失败时也 resolve，错误写进 error 由模板展示。
 * @throws 不抛出——所有异常被捕获后转成 error 文本。
 * @example
 * ```ts
 * await load(); // 切换 SPU、店铺、区间时调用
 * ```
 */
async function load() {
  loading.value = true;
  error.value = "";
  try {
    // 用户没手动选过区间时用默认「近一周」，选过就用用户的选择
    const r = rangeSet.value ? { start: start.value, end: end.value } : defaultRange();
    data.value = await fetchSpuAnalysis(props.moduleId, props.spu, {
      shop: shop.value || undefined,
      start: r.start || undefined,
      end: r.end || undefined,
    });
    // 回填显示用日期（不影响下次请求逻辑）：接口返回的区间即实际统计区间
    start.value = data.value.date_range[0] ?? "";
    end.value = data.value.date_range[1] ?? "";
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
 * onRangeChange({ start: "2026-09-18", end: "2026-09-24" });
 * ```
 */
function onRangeChange(v: { start: string; end: string }) {
  start.value = v.start;
  end.value = v.end;
  rangeSet.value = true; // 用户显式选过（含清空=全区间），后续不再自动套默认
  void load();
}

// 直接进入本页（刷新/分享链接）时 store 里可能还没有 manifest，先补拉一次，
// 否则拿不到数据最新日、默认「近一周」就退化成全区间。
/**
 * 确保 manifest 已加载（拿不到数据最新日就无法算「近一周」）。
 *
 * @returns {Promise<void>} 已有 manifest 时立即返回；拉取失败也 resolve（静默）。
 * @throws 不抛出——manifest 失败会在后续分析接口里体现，这里刻意不中断流程。
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
  // 组件已挂载：路由级遮罩（覆盖 chunk 下载+解析）交棒给本页自身的「数据加载中…」遮罩，
  // 后者覆盖后端分析接口的请求阶段，二者无缝衔接、不闪空。
  ui.setNavigating(false);
  await ensureManifest();
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
watch(() => [props.spu, shop.value], () => void load());

// ---------- 指标配置（与模块页同源）----------
const selectedKeys = computed({
  get: () => metricsStore.keysFor(props.moduleId),
  set: (v: string[]) => metricsStore.set(props.moduleId, v),
});
const specs = computed(() => metricsOf(selectedKeys.value));

// ---------- 指标卡：单日=单值；范围模式=「求和 + 平均值」两块 ----------
// 求和类：求和块=区间累计，平均值块=日均（累计÷天数）；
// 比率类（转化率/ROI/客单价/推广占比/搜索点击率）无「求和」意义，
//   求和块展示分子分母的区间累计，平均值块展示区间整体比率（先求和再相除，与后端口径一致）。
const GROUP_LABEL: Record<string, string> = {
  detail: "明细",
  promo: "推广",
  extra: "退款/其他",
};
// 部分比率指标的分子/分母本身不是「展示指标」（如 search_click_rate 的分子 search_clicks），
// 不在 METRICS 清单里，这里补一份中文名兜底，避免求和块显示原始 key。
const EXTRA_LABEL: Record<string, string> = {
  search_clicks: "搜索点击次数",
};
/**
 * 取指标的中文名（用于比率类卡片展示「分子 / 分母」）。
 *
 * @param {string} key - 指标 key，可能是「隐藏分子」（如 search_clicks）。
 * @returns {string} 中文名；清单里没有时查 EXTRA_LABEL 兜底，再没有就原样返回 key。
 * @example
 * ```ts
 * labelOf("buyers");        // "成交客户数"
 * labelOf("search_clicks"); // "搜索点击次数"（来自 EXTRA_LABEL）
 * ```
 */
function labelOf(key: string): string {
  return METRIC_MAP[key]?.title ?? EXTRA_LABEL[key] ?? key;
}

/**
 * 取分子/分母的展示格式：清单里没有的 key（如 search_clicks）按整数处理。
 *
 * @param {string} key - 指标 key。
 * @returns {MetricFormat} 展示格式；未登记时返回 "number"。
 * @example
 * ```ts
 * fmtOf("search_clicks"); // "number"
 * ```
 */
function fmtOf(key: string): MetricFormat {
  return METRIC_MAP[key]?.format ?? "number";
}
/** 日均/平均值专用格式：整数型指标（退款单量/成交单量/客户数/访客数等）的日均需保留小数，
 *  否则 fmtBy("number") 会把 15÷22=0.68 四舍五入成 1。最多 2 位小数、无多余尾零
 *  （0.68→"0.68"、5→"5"、1234.5→"1,234.5"）。其余格式沿用自身。 */
function fmtAvg(format: MetricFormat, v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "0";
  if (format === "number") {
    return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }
  return fmtBy(format, v);
}
interface CardVM {
  key: string;
  title: string;
  groupTag: string;
  single: boolean;
  missing: boolean;
  kind?: "sum" | "ratio";
  text?: string;
  sumMain?: string;
  sumSub?: string;
  sumPairs?: { label: string; value: string }[];
  avgLabel?: string;
  avgMain?: string;
  avgSub?: string;
}
const cards = computed<CardVM[]>(() => {
  const daily = data.value?.daily ?? [];
  const days = daily.length;
  const metrics = daily.map((d) => d.metrics);
  const sum = aggregateMetrics(metrics); // 各指标自身口径（sum 累加 / ratio 先求和再相除）
  const raw = sumRawMetrics(metrics); // 原始字段区间累计（含 search_clicks 等「隐藏分子」）
  return specs.value.map((s) => {
    const val = sum[s.key as keyof typeof sum];
    const missing = val == null;
    const groupTag = GROUP_LABEL[s.group] ?? "";
    // 单日模式：维持原单值卡片
    if (days <= 1) {
      return { key: s.key as string, title: s.title, groupTag, single: true, missing, text: fmtBy(s.format, val) };
    }
    // 范围模式：求和块 + 平均值块
    if (s.agg.kind === "ratio") {
      const numKey = s.agg.num as string;
      const denKey = s.agg.den as string;
      const numLabel = labelOf(numKey);
      const denLabel = labelOf(denKey);
      return {
        key: s.key as string,
        title: s.title,
        groupTag,
        single: false,
        kind: "ratio",
        missing,
        // 比率类无「求和」意义：求和块展示分子/分母的区间累计（原始值，非比率本身）
        sumPairs: [
          { label: numLabel, value: fmtBy(fmtOf(numKey), raw[numKey as keyof typeof raw]) },
          { label: denLabel, value: fmtBy(fmtOf(denKey), raw[denKey as keyof typeof raw]) },
        ],
        avgLabel: "平均值（区间整体）",
        avgMain: fmtBy(s.format, val),
        avgSub: `= ${numLabel} ÷ ${denLabel}`,
      };
    }
    // 求和类：平均值 = 日均
    const avg = typeof val === "number" ? val / days : null;
    return {
      key: s.key as string,
      title: s.title,
      groupTag,
      single: false,
      kind: "sum",
      missing,
      sumMain: fmtBy(s.format, val),
      sumSub: `日均 ${fmtAvg(s.format, avg)} · ${days} 天`,
      avgLabel: "平均值（日均）",
      avgMain: fmtAvg(s.format, avg),
      avgSub: "= 区间累计 ÷ 天数",
    };
  });
});

// ---------- 趋势图：3 张（2 张双轴合并 + 1 张推广占比）----------
// 合并规则：成交转化率(右%轴) + 商品访客数(左数轴)；成交金额(左金额轴) + 搜索点击率(右%轴)。
// 百分比率指标的 y 轴刻度标签统一用「百分格式」展示。
const dates = computed(() => (data.value?.daily ?? []).map((d) => d.date));

interface TrendAxis { name: string; format: MetricFormat }
interface TrendSeries { key: string; title: string; format: MetricFormat; color: string; axisIndex: 0 | 1 }
interface TrendPanel {
  key: string;
  title: string;
  axes: TrendAxis[]; // 长度 1 或 2（双轴）
  series: TrendSeries[];
  threshold?: boolean; // 推广占比：显示可调警戒线
}

/** 从指标清单取标题/配色/格式，保持单一事实来源。 */
function specOf(key: string): MetricSpec {
  const m = METRIC_MAP[key];
  if (!m) throw new Error(`未知指标：${key}`);
  return m;
}
function mkSeries(key: string, axisIndex: 0 | 1): TrendSeries {
  const m = specOf(key);
  return { key, title: m.title, format: m.format, color: m.color, axisIndex };
}

const TREND_PANELS: TrendPanel[] = [
  {
    key: "conv_click",
    title: "成交转化率 & 搜索点击率",
    axes: [{ name: "成交转化率", format: "percent" }, { name: "搜索点击率", format: "percent" }],
    series: [mkSeries("conversion_rate", 0), mkSeries("search_click_rate", 1)],
  },
  {
    key: "amount_cost",
    title: "成交金额 & 推广花费金额",
    axes: [{ name: "成交金额", format: "currency" }, { name: "推广花费", format: "currency" }],
    series: [mkSeries("amount", 0), mkSeries("promotion_cost", 1)],
  },
  {
    key: "promotion_ratio",
    title: "推广占比",
    axes: [{ name: "推广占比", format: "percent" }],
    series: [mkSeries("promotion_ratio", 0)],
    threshold: true,
  },
];

// 推广占比趋势图的警戒线阈值（单位 %），可在页面上手动修改并实时生效
const thresholdPct = ref(20);

/**
 * 生成 y 轴刻度标签的格式化函数：百分比 -> "12.3%"；金额 -> 紧凑 "¥1.2万"；其余 -> 千分位。
 *
 * @remarks
 * 金额超过 1 万时压缩成「万」，否则轴标签会变成 "¥123456" 这种又长又难读的字符串，
 * 挤占绘图区宽度。
 *
 * @param {MetricFormat} format - 该轴的展示格式。
 * @returns {(v: number) => string} 接收轴刻度值、返回标签文本的函数。
 * @example
 * ```ts
 * axisTickFormatter("percent")(0.123);  // "12.3%"
 * axisTickFormatter("currency")(12345); // "¥1.2万"
 * ```
 */
function axisTickFormatter(format: MetricFormat): (v: number) => string {
  if (format === "percent") return (v: number) => (v * 100).toFixed(1) + "%";
  if (format === "currency") return (v: number) =>
    v >= 10000 ? "¥" + (v / 10000).toFixed(1) + "万" : "¥" + Math.round(v).toLocaleString("zh-CN");
  return (v: number) => v.toLocaleString("zh-CN");
}

/**
 * 生成 tooltip 的 HTML 格式化函数（按各序列自身的格式展示）。
 *
 * @remarks
 * 必须按序列自己的 format 渲染：双轴面板里左轴是金额、右轴是百分比，
 * 若统一按一种格式渲染就会把金额显示成百分比（或反之）。
 *
 * @param {TrendPanel} panel - 趋势面板配置，用于按 seriesIndex 找到对应序列。
 * @returns {(params: unknown) => string} ECharts tooltip 的 formatter 函数，
 *      返回一段含日期表头与各序列数值的 HTML。
 * @example
 * ```ts
 * // 由 buildTrendOption 装配进 option.tooltip.formatter
 * ```
 */
function makeTooltip(panel: TrendPanel) {
  return (params: unknown) => {
    const arr = Array.isArray(params) ? params : [params];
    const rows = (arr as Array<{ seriesIndex: number; value: unknown; marker: string; axisValue: string }>).map((p) => {
      const se = panel.series[p.seriesIndex];
      const val = p.value;
      const txt = fmtBy(se.format, typeof val === "number" ? val : 0);
      return `${p.marker}${se.title}: ${txt}`;
    });
    const head = (arr as Array<{ axisValue?: string }>)[0]?.axisValue ?? "";
    return head + "<br/>" + rows.join("<br/>");
  };
}

/**
 * 取某面板所有序列在逐日数据里的最大值（用于需要对齐双轴刻度时统一量程）。
 *
 * @param {TrendPanel} panel - 趋势面板配置。
 * @returns {number} 该面板所有序列、所有日期中的最大数值；无有效数据时返回 0。
 * @example
 * ```ts
 * panelDataMax(TREND_PANELS[0]); // 如 0.086（8.6%）
 * ```
 */
function panelDataMax(panel: TrendPanel): number {
  const daily = data.value?.daily ?? [];
  let max = 0;
  for (const d of daily) {
    for (const s of panel.series) {
      const v = (d.metrics as Record<string, number | null>)[s.key];
      // 跳过 null（缺失日）与 NaN，避免 Math.max 被污染成 NaN
      if (typeof v === "number" && !Number.isNaN(v)) max = Math.max(max, v);
    }
  }
  return max;
}

/**
 * 把最大值向上取到「整齐」的刻度上限（1% / 2% / 5% / 10% / 20% / 50% / 100%…）。
 *
 * @remarks
 * 直接把最大值当轴上限会得到 8.6% 这种零碎刻度，左右轴虽然数值相同但格线很难读；
 * 取到整档后刻度落在 10%、20% 这类整齐值上，观感更好。
 *
 * @param {number} v - 数据最大值（小数形式的比率）。
 * @returns {number} 向上取整后的刻度上限；v <= 0 时返回 0.01（1%）。
 * @example
 * ```ts
 * niceCeil(0.086); // 0.1  (10%)
 * niceCeil(0.3);   // 0.5  (50%)
 * niceCeil(0);     // 0.01 (1%)
 * ```
 */
function niceCeil(v: number): number {
  if (!(v > 0)) return 0.01;
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1];
  for (const s of steps) if (v <= s) return s;
  return Math.ceil(v);
}

/**
 * 按面板配置生成 ECharts 配置项。
 *
 * @remarks
 * 关键设计——双轴刻度对齐：
 * 「成交转化率 & 搜索点击率」两条序列都是百分比，若让 ECharts 各自独立定刻度，
 * 左右轴的刻度数字会不一样（如左 0~10%、右 0~8%），两条线的高度就不可比。
 * 所以对这个面板强制左右轴共用同一量程（0 ~ niceCeil(数据最大值)）。
 * 其余面板（如成交金额 vs 推广花费，量级差很大）保持各自独立刻度，避免小的那条被压平。
 *
 * @param {TrendPanel} panel - 趋势面板配置（轴、序列、是否带警戒线）。
 * @returns {EChartsOption} ECharts 配置项。
 * @example
 * ```ts
 * const opt = buildTrendOption(TREND_PANELS[0]);
 * // opt.yAxis[0].max === opt.yAxis[1].max（该面板双轴对齐）
 * ```
 */
function buildTrendOption(panel: TrendPanel): EChartsOption {
  // 成交转化率 & 搜索点击率 两张都是百分比，强制左右轴共用同一量程（0 ~ 同一上限），
  // 使两侧刻度数字完全一致、两条线可直接比较；其余面板保持各自独立刻度。
  const alignScale = panel.key === "conv_click";
  const sharedMax = alignScale ? niceCeil(panelDataMax(panel)) : 0;
  const yAxis = panel.axes.map((ax, i) => ({
    type: "value" as const,
    name: ax.name,
    position: (i === 0 ? "left" : "right") as "left" | "right",
    axisLabel: { formatter: axisTickFormatter(ax.format) },
    splitLine: i === 0 ? { lineStyle: { color: "#f1f5f9" } } : { show: false },
    ...(alignScale ? { min: 0, max: sharedMax } : {}),
  }));
  const series = panel.series.map((se) => {
    const base: Record<string, unknown> = {
      name: se.title,
      type: "line",
      smooth: true,
      yAxisIndex: se.axisIndex,
      data: (data.value?.daily ?? []).map((d) => (d.metrics as Record<string, number | null>)[se.key] ?? null),
      connectNulls: true,
      itemStyle: { color: se.color },
      areaStyle: { opacity: 0.06 },
      showSymbol: false,
    };
    // 推广占比叠加可手动调整的警戒线（阈值以百分比输入，按小数落到数值轴）
    if (panel.threshold && se.key === "promotion_ratio" && thresholdPct.value != null) {
      base.markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dashed", color: "#ef4444", width: 1.5 },
        label: { formatter: `警戒线 ${thresholdPct.value}%`, position: "insideEndTop", color: "#ef4444", fontSize: 11 },
        data: [{ yAxis: thresholdPct.value / 100 }],
      };
    }
    return base;
  });
  return {
    tooltip: { trigger: "axis", formatter: makeTooltip(panel) },
    legend: { data: panel.series.map((s2) => s2.title), top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 64, right: panel.axes.length > 1 ? 64 : 28, top: 40, bottom: 30 },
    xAxis: { type: "category", data: dates.value, axisLabel: { formatter: (v: string) => v.slice(5) } },
    yAxis,
    series,
  };
}

const trendCharts = computed(() =>
  TREND_PANELS.map((panel) => ({
    key: panel.key,
    title: panel.title,
    option: buildTrendOption(panel),
    threshold: !!panel.threshold,
  })),
);

// ---------- 工作日 vs 节假日 对比（固定 4 项：日均成交金额 / 成交转化率 / 推广占比 / ROI）----------
interface CompareRow {
  key: string;
  label: string;
  workday: string;
  holiday: string;
  w: number; // 原始值（算条高比例）
  h: number;
}
/**
 * 从「工作日 / 节假日」分组里安全取一个数值字段。
 *
 * @param {CompareGroup} g - 分组统计对象。
 * @param {keyof CompareGroup} field - 要取的字段名。
 * @returns {number | null} 字段值；字段缺失或不是数字时返回 null（调用方按 0 兜底）。
 * @example
 * ```ts
 * groupValue(workday, "amount_avg"); // 1234.5 或 null
 * ```
 */
function groupValue(g: CompareGroup, field: keyof CompareGroup): number | null {
  const v = g[field];
  return typeof v === "number" ? v : null;
}

/** 「工作日 vs 节假日」固定展示这 4 项（不随指标勾选变化：对比图是固定分析视角） */
const COMPARE_KEYS = ["amount", "conversion_rate", "promotion_ratio", "roi"];

/**
 * 按给定顺序取 spec 列表（保留调用方指定的排列）。
 *
 * @remarks
 * 与 metricsOf 的区别：metricsOf 按**清单顺序**返回（用于卡片/列，保证稳定），
 * 这里按**传入顺序**返回（用于对比图，让「日均成交金额」排首位符合阅读习惯）。
 *
 * @param {string[]} keys - 指标 key 数组（顺序即返回顺序）。
 * @returns {MetricSpec[]} 对应的指标定义；未命中的 key 被过滤掉。
 * @example
 * ```ts
 * orderedSpecs(["roi", "amount"]).map((m) => m.title); // ["ROI", "成交金额"]
 * ```
 */
function orderedSpecs(keys: string[]): MetricSpec[] {
  const map = new Map(METRICS.map((m) => [m.key as string, m]));
  return keys.map((k) => map.get(k)).filter((m): m is MetricSpec => !!m);
}
const compareSpecs = computed(() => orderedSpecs(COMPARE_KEYS));

const compareRows = computed<CompareRow[]>(() => {
  const a = data.value;
  if (!a) return [];
  const rows: CompareRow[] = [];
  for (const s of compareSpecs.value) {
    if (!s.compare) continue;
    const field = s.compare;
    const w = groupValue(a.workday, field) ?? 0;
    const h = groupValue(a.holiday, field) ?? 0;
    rows.push({
      key: s.key as string,
      label: s.compareLabel ?? s.title,
      workday: fmtBy(s.format, w),
      holiday: fmtBy(s.format, h),
      w,
      h,
    });
  }
  return rows;
});

/**
 * 计算对比条形图里某根柱子的高度百分比。
 *
 * @remarks
 * 按行内两个值的较大者再留 40% 余量作为满高基准：
 * 直接以最大值为满高会让最高的柱子顶到轨道顶端、视觉上很挤，留余量更好看；
 * 同时用 Math.max(..., 3) 保证值极小的柱子也有 3% 的可见高度，不会「消失」。
 *
 * @param {number} v - 该柱子的数值（工作日或节假日的值）。
 * @param {CompareRow} row - 所在行（含 w / h 两个原始值，用于确定基准）。
 * @returns {string} 高度百分比字符串，形如 "71.4%"。
 * @example
 * ```ts
 * barPct(100, { w: 100, h: 50 } as CompareRow); // "71.4%"
 * ```
 */
function barPct(v: number, row: CompareRow): string {
  // 按行内两个值的最大者留出 40% 余量，算柱子高度百分比
  // 1e-9 兜底：两个值都为 0 时避免除零（此时 max 极小，结果被下面的 3% 兜住）
  const max = Math.max(row.w, row.h, 1e-9) * 1.4;
  return Math.max((v / max) * 100, 3).toFixed(1) + "%";
}

/**
 * 返回模块列表页。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * goBack(); // 点「← 返回列表」
 * ```
 */
function goBack() {
  void router.push({ name: "module", params: { moduleId: props.moduleId } });
}
</script>

<template>
  <div class="page">
    <!-- 顶部：返回 + 日期范围 + 指标配置 -->
    <div class="topbar">
      <button class="back-btn" @click="goBack">← 返回列表</button>
      <div class="topbar-right">
        <div class="range">
          <span class="range-label">统计区间</span>
          <DateRangePicker
            :start="start"
            :end="end"
            :min="fullRange[0]"
            :max="fullRange[1]"
            show-last-week
            @change="onRangeChange"
          />
        </div>
        <span v-if="loading && data" class="reload-hint"><span class="mini-spin"></span>刷新中…</span>
        <MetricConfigPanel v-model:keys="selectedKeys" />
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <!-- 初始加载遮罩：点击「单品分析」导航过来时整屏变浅 + 旋转圈，给出明确反馈 -->
    <transition name="pl-fade">
      <div v-if="loading && !data" class="page-loading">
        <div class="pl-spinner"></div>
        <div class="pl-text">数据加载中…</div>
      </div>
    </transition>

    <template v-if="data">
      <!-- 商品信息头 -->
      <div class="hero">
        <picture v-if="data.image">
          <source :srcset="thumb(data.image, 96, 'avif')" type="image/avif" />
          <source :srcset="thumb(data.image, 96, 'webp')" type="image/webp" />
          <img :src="data.image" :alt="data.spu" class="hero-img" />
        </picture>
        <div v-else class="hero-img no-img">暂无图片</div>
        <div class="hero-info">
          <div class="hero-name">{{ data.spu_name ?? "--" }}</div>
          <div class="hero-meta">
            <span class="m-spu">SPU: {{ data.spu }}</span>
            <span>{{ data.shops.join(" / ") }}</span>
            <span>{{ data.category ?? "--" }}</span>
            <span>{{ data.date_range[0] }} ~ {{ data.date_range[1] }}</span>
          </div>
        </div>
      </div>

      <!-- 指标卡（与明细页同一份勾选）：单日=单值，范围=求和+平均值两块 -->
      <div class="cards">
        <template v-if="cards.length">
          <div v-for="c in cards" :key="c.key" class="card">
            <div class="ctitle"><span>{{ c.title }}</span><span v-if="c.groupTag" class="tag">{{ c.groupTag }}</span></div>

            <template v-if="c.single">
              <div class="card-value" :class="{ missing: c.missing }">{{ c.text }}</div>
            </template>
            <template v-else>
              <div class="part sum">
                <div class="plabel"><span class="dot"></span>求和 · 区间累计</div>
                <template v-if="c.sumPairs">
                  <div v-for="(p, i) in c.sumPairs" :key="i" class="prow">
                    <span class="plab">{{ p.label }}</span>
                    <span class="pnum">{{ p.value }}</span>
                  </div>
                </template>
                <template v-else>
                  <div class="pval" :class="{ missing: c.missing }">{{ c.sumMain }}</div>
                  <div class="psub">{{ c.sumSub }}</div>
                </template>
              </div>
              <div class="pdiv"></div>
              <div class="part avg">
                <div class="plabel"><span class="dot"></span>{{ c.avgLabel }}</div>
                <div class="pval avgv" :class="{ missing: c.missing }">{{ c.avgMain }}</div>
                <div class="psub">{{ c.avgSub }}</div>
              </div>
            </template>
          </div>
        </template>
        <div v-else class="empty-cards">未勾选任何指标，点击右上角「指标配置」选择要展示的指标</div>
      </div>

      <!-- 综合分析（上移至卡片下方，先给结论再给趋势） -->
      <div class="insight">
        <h3>综合分析</h3>
        <p>{{ data.insight }}</p>
      </div>

      <!-- 趋势图：3 张（成交转化率&访客数 双轴 / 成交金额&搜索点击率 双轴 / 推广占比） -->
      <div class="charts" v-if="trendCharts.length">
        <div v-for="ch in trendCharts" :key="ch.key" class="chart-box">
          <h3>
            <span>{{ ch.title }}</span>
            <label v-if="ch.threshold" class="threshold-ctl">
              警戒线
              <input
                type="number"
                min="0"
                step="0.5"
                v-model.number="thresholdPct"
                class="threshold-input"
              />%
            </label>
          </h3>
          <GenericChart :option="ch.option" height="280px" />
        </div>
      </div>

      <!-- 工作日 vs 节假日（纵向条形图） -->
      <div class="compare-box" v-if="compareRows.length">
        <h3>
          工作日 vs 节假日
          <span class="hint">
            工作日 {{ data.workday.days }} 天 · 节假日 {{ data.holiday.days }} 天（周末 + 法定节假日）
          </span>
        </h3>
        <div class="cmp-legend">
          <span class="lg w">工作日</span>
          <span class="lg h">节假日</span>
        </div>
        <div class="cmp-grid">
          <div v-for="row in compareRows" :key="row.key" class="cmp-card">
            <div class="cmp-label">{{ row.label }}</div>
            <div class="cmp-bars">
              <div class="vbar">
                <span class="bar-val">{{ row.workday }}</span>
                <div class="vbar-track">
                  <div class="vbar-fill w" :style="{ height: barPct(row.w, row) }"></div>
                </div>
                <span class="bar-cap">工作日</span>
              </div>
              <div class="vbar">
                <span class="bar-val">{{ row.holiday }}</span>
                <div class="vbar-track">
                  <div class="vbar-fill h" :style="{ height: barPct(row.h, row) }"></div>
                </div>
                <span class="bar-cap">节假日</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* ---------- 顶部条 ---------- */
.topbar {
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
.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.back-btn {
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 7px 14px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.back-btn:hover {
  border-color: #e1251b;
  color: #e1251b;
}
.range {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #475569;
}
.error {
  background: #fef2f2;
  color: #b91c1c;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 13px;
}
.loading {
  color: #64748b;
  font-size: 14px;
  padding: 40px;
  text-align: center;
}
/* ---------- 商品信息头 ---------- */
.hero {
  display: flex;
  gap: 18px;
  align-items: center;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.hero-img {
  width: 96px;
  height: 96px;
  object-fit: contain;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  flex: none;
}
.no-img {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #cbd5e1;
  font-size: 12px;
}
.hero-name {
  font-size: 17px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 8px;
}
.hero-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: #475569;
}
.m-spu {
  color: #2563eb;
}
/* ---------- 指标卡 ---------- */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
}
.card {
  position: relative;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px 16px;
  overflow: hidden;
  transition: box-shadow 0.18s, transform 0.18s;
}
.card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, #e1251b, rgba(225, 37, 27, 0.15));
  opacity: 0;
  transition: opacity 0.18s;
}
.card:hover {
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.08);
  transform: translateY(-2px);
}
.card:hover::before {
  opacity: 1;
}
.card-title {
  font-size: 12px;
  color: #475569;
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-value {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}
.card-value.missing {
  color: #94a3b8;
}
/* ---------- 指标卡：范围模式 求和/平均值 两块 ---------- */
.ctitle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 10px;
}
.ctitle .tag {
  font-size: 10.5px;
  font-weight: 500;
  color: #475569;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 1px 6px;
}
/* 指标卡标题前的小图标，hover 卡片时放大变红 */
.ctitle-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.m-ico {
  flex: none;
  width: 16px;
  height: 16px;
  color: #64748b;
  display: inline-flex;
  align-items: center;
  transition: transform 0.15s, color 0.15s;
}
.card:hover .m-ico {
  color: #e1251b;
  transform: scale(1.15);
}
.part {
  border-radius: 8px;
  padding: 9px 11px;
}
.part.sum {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}
.part.avg {
  background: #fff7f6;
  border: 1px solid #fecaca;
}
.plabel {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #475569;
  margin-bottom: 6px;
}
.plabel .dot {
  width: 7px;
  height: 7px;
  border-radius: 2px;
  background: #475569;
}
.part.avg .plabel .dot {
  background: #e1251b;
}
.pval {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
}
.pval.missing {
  color: #94a3b8;
}
.pval.avgv {
  color: #e1251b;
}
/* 比率类求和块：小灰标签 + 数据 并排；右对齐，空间不足时整行换行，避免溢出卡片 */
.prow {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 3px 6px;
  margin-top: 3px;
}
.plab {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}
.pnum {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.2px;
  margin-left: auto;
  overflow-wrap: anywhere;
}
.psub {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.pdiv {
  height: 8px;
}
.empty-cards {
  grid-column: 1 / -1;
  padding: 22px;
  text-align: center;
  font-size: 13px;
  color: #64748b;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
}
/* ---------- 趋势图 ---------- */
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 12px;
}
.chart-box {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 12px 14px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.chart-box h3 {
  margin: 0 0 6px;
  font-size: 14px;
  color: #334155;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.hint {
  font-size: 11px;
  font-weight: 400;
  color: #64748b;
}
.threshold-ctl {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 400;
  color: #ef4444;
}
.threshold-input {
  width: 52px;
  padding: 2px 4px;
  font-size: 12px;
  color: #334155;
  border: 1px solid #fecaca;
  border-radius: 4px;
  text-align: right;
  outline: none;
}
.threshold-input:focus {
  border-color: #ef4444;
  box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.15);
}
/* ---------- 工作日 vs 节假日（纵向条形图） ---------- */
.compare-box {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.compare-box h3 {
  margin: 0 0 14px;
  font-size: 14px;
  color: #334155;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
}
.cmp-legend {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-bottom: 12px;
  font-size: 12px;
  color: #475569;
}
.lg::before {
  content: "";
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 5px;
  vertical-align: -1px;
}
.lg.w::before {
  background: #e1251b;
}
.lg.h::before {
  background: #2563eb;
}
.cmp-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
}
.cmp-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  min-width: 130px;
}
.cmp-label {
  font-size: 12.5px;
  color: #475569;
  text-align: center;
}
.cmp-bars {
  display: flex;
  align-items: flex-end;
  gap: 18px;
  height: 180px;
}
.vbar {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  width: 52px;
}
.vbar-track {
  width: 30px;
  height: 140px;
  background: #f1f5f9;
  border-radius: 4px;
  display: flex;
  align-items: flex-end;
  overflow: hidden;
}
.vbar-fill {
  width: 100%;
  border-radius: 4px 4px 0 0;
  transition: height 0.4s ease;
  min-height: 2px;
}
.vbar-fill.w {
  background: linear-gradient(180deg, #f87171, #e1251b);
}
.vbar-fill.h {
  background: linear-gradient(180deg, #60a5fa, #2563eb);
}
.bar-val {
  font-size: 12px;
  color: #334155;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.bar-cap {
  font-size: 11px;
  color: #64748b;
}
/* ---------- 综合分析 ---------- */
.insight {
  background: linear-gradient(135deg, #fff7f6, #fff);
  border: 1px solid #fecaca;
  border-left: 4px solid #e1251b;
  border-radius: 10px;
  padding: 16px 20px;
}
.insight h3 {
  margin: 0 0 10px;
  font-size: 16px;
  color: #e1251b;
}
.insight p {
  margin: 0;
  font-size: 15px;
  line-height: 2;
  color: #334155;
}
@media (max-width: 900px) {
  .charts {
    grid-template-columns: 1fr;
  }
}
/* ---------- 初始加载遮罩（点击「单品分析」导航过来时的明确反馈）---------- */
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
  border: 4px solid #f6cfcc;
  border-top-color: #e1251b;
  border-radius: 50%;
  animation: pl-spin 0.8s linear infinite;
}
@keyframes pl-spin {
  to { transform: rotate(360deg); }
}
.pl-text {
  font-size: 14px;
  color: #475569;
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
/* 区间重算时的轻量提示（不遮挡已有内容） */
.reload-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
}
.mini-spin {
  width: 13px;
  height: 13px;
  border: 2px solid #e2e8f0;
  border-top-color: #e1251b;
  border-radius: 50%;
  animation: pl-spin 0.8s linear infinite;
}
</style>
