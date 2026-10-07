<script setup lang="ts">
// 多月份对比表：在单品分析页并排对比某 SPU **多个**自然月的各项指标。
//
// 为什么单独抽成组件：
//   单品分析页已有指标卡 + 趋势图 + 综合分析，本块是独立职责；
//   抽出来后自带「取数 / 口径计算 / 格式化」，视图只负责渲染。
//
// 布局（默认态 + 点击展开）：
//   - 默认态：每个指标一行，行内月份按 **左小 → 右大** 横排，相邻月份之间画
//     `→` 箭头，箭头上方浮气泡 = 差额 · 变化（绿涨红跌）。
//   - 点击指标行展开：该行高度扩大，内联展示该指标单独的「时间轴大图」
//     （节点 = 各月值，连线，箭头上方气泡）。两态复用同一份逐月值与相邻 diff，互不重复计算。
//
// 口径（重要，与项目其它位置保持一致）：
//   - 每个月**独立**用 aggregateMetrics 聚合，即比率类仍「先求和再相除」，
//     不是把两月相减或取平均；与指标卡、趋势图、导出表完全一致。
//   - 「差额」列按指标**单位**分两种写法：百分比类（转化率/点击率/推广占比）记**百分点(pt)**
//     —— 1.92% → 1.56% 是「−0.35pt」；其余按各自单位记数值 —— 客单价 ¥85 → ¥88 是「+¥3.00」。
//     ⚠️ 判据是 format（单位）而非 agg.kind（聚合方式）：客单价虽然是 ratio 聚合，
//     单位却是金额，与成交金额完全同理。
//   - 「变化」列**全类型**都给相对变化率 `(新 − 旧) / |旧|`，分母 0 时给 `--`（不给 Infinity）。
//     它与差额列并存不冲突：差额是**绝对差**、变化率是**相对变化**，回答的是两个不同问题
//     （转化率降了 0.35 个百分点 ＝ 相对降了 18.5%）。
//   - ⚠️ 箭头方向固定「低月份 → 高月份」（左 → 右），差额/变化永远是 右 − 左，
//     与页面「左小右大」的排列一致，不会因用户勾选顺序而颠倒。
//
// 天数公平性（重要）：
//   拿 3 天的当月去比 30 天的上月，算出来的涨跌有相当一部分是「天数少」造成的假象。
//   本组件的处理：
//   1. 默认选中**最近的完整月**（自动跳过当月这种只有几天的残缺月），避开这个坑；
//   2. 顶部给出「同天数对比」开关，勾选后**所有月都对齐到全局最短的一方**再比——
//      谁天数少就以谁为基准，截长的那一边，与哪个月最短无关（详见 trimToDays 注释）；
//   3. 表头显示每月**实际天数**，选到不完整月时有明确提醒。
import { computed, onBeforeUnmount, onMounted, ref, watch, type VNodeRef } from "vue";
import { fetchSpuAnalysis } from "../api";
import { aggregateMetrics, METRICS, type MetricFormat } from "../metrics";
import { fmtBy } from "../modules";
import { availableMonths, monthBoundsOf, type MonthCell, type MonthRange } from "../utils/month";
import type { SpuDailyPoint } from "../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 模块 id */
  moduleId: string;
  /** SPU 编号 */
  spu: string;
  /** 店铺名；空串 = 该 SPU 全部店铺合计 */
  shop: string;
  /** 数据可用区间 [最早日, 最晚日]，用于月份边界与天数计算 */
  dataRange: string[];
}>();

/* ---------------- 月份选择（多选） ---------------- */

/** 面板是否展开（默认收起：单品分析页已有指标卡与趋势图，本块不抢视觉） */
const expanded = ref(false);

/* ---------------- 折叠头「可点击」提示 ---------------- */

/**
 * 是否播放折叠头的「呼吸」提示动画（三角轻微上下浮动）。
 *
 * @remarks
 * 为什么要这个：折叠头本身是页面中部一个不起眼的横条，收起态又只有一行
 * 13px 文字 + 9×15px 的淡灰三角，缩略显示时几乎看不出「这里能点」。
 * hover 只能解决「已经注意到之后」的反馈，解决不了「压根没看到」——
 * 所以需要一个不依赖鼠标悬停的静态标识 + 一次性的动态招徕。
 *
 * ⚠️ **只播一次**：常驻循环动画会变成页面噪音，反而让人不再注意它。
 * 用 sessionStorage 记录「本次浏览器会话已提示过」，用户看过一眼就记住，
 * 之后切到别的 SPU 不再打扰（session 级而非 localStorage 永久——
 * 隔天回来重新看一眼提示是合理的）。
 */
const hintPulse = ref(false);

/** sessionStorage key：记录本会话是否已播放过折叠头提示 */
const HINT_KEY = "jd.mc-hint-shown";

onMounted(() => {
  try {
    if (sessionStorage.getItem(HINT_KEY)) return;
    sessionStorage.setItem(HINT_KEY, "1");
  } catch {
    // 隐私模式 / 存储被禁用时降级为「每次都提示」，不阻断功能
  }
  hintPulse.value = true;
  // 动画只跑 2.55s（CSS 里 3 遍 × 0.85s），结束后撤下 class 复位 transform
  window.setTimeout(() => (hintPulse.value = false), 2600);
});

/** 选中的对比月份 "YYYY-MM" 列表（按时间升序：左小 → 右大） */
const selectedMonths = ref<string[]>([]);

/** 是否勾选「同天数对比」（把所有月都对齐到天数较少的一方再比） */
const sameDays = ref(false);

/**
 * 当前展开时间轴大图的指标 key 集合（空Set = 无展开）。
 *
 * @remarks
 * 用 Set 而非单个 key：**允许多个指标同时展开时间轴**（用户需求）。
 * 早前是单值语义，点开新行会自动收起旧行——用户想「保留已展开的以便横向对照」，
 * 于是改为可多开。Set 保证同一行再点一次即取消（toggle 语义不变）。
 * 模板里用 `expandedRowKeys.has(r.key)` 判定，比数组 includes 更直白且不需每次重建。
 */
const expandedRowKeys = ref<Set<string>>(new Set());

/**
 * 切换某指标行的展开状态。
 *
 * @param key - 指标行的唯一 key。
 */
function isRowExpanded(key: string): boolean {
  return expandedRowKeys.value.has(key);
}

/** 箭头固定为方案二（渐长虚线 + 大三角头）；用户确认只保留此方案，已移除方案一及其切换。 */


/**
 * 方案二（虚线）的渐长虚线段几何：线宽从 3px 逐段加宽到 ~14px、间距恒定 4px，
 * 模拟「越靠近箭头越粗」的透视感（用户提供的图片2）；终点 101px 处衔接三角箭头。
 * 与样式无关的静态几何，只需算一次，无需响应式。
 *
 * @returns {{ x: number; w: number }[]} 虚线段列表（x = 起点、w = 段宽，viewBox 坐标）。
 */
const arrowDashes = (() => {
  const out: { x: number; w: number }[] = [];
  let x = 0;
  let w = 3;
  // 逐段推进：段宽递增、留 4px 间隙；超出箭头主体（101px）即停，剩余留给三角头
  while (x + w < 100) {
    out.push({ x, w });
    x += w + 4;
    w += 1.6;
  }
  return out;
})();

/**
 * 某月对应的日期区间（与数据可用区间取交集）。
 *
 * @param {string} monthKey - "YYYY-MM"。
 * @returns {MonthRange} 该月区间；monthKey 为空时返回空区间。
 */
function rangeOf(monthKey: string): MonthRange {
  if (!monthKey) return { start: "", end: "" };
  const [y, m] = monthKey.split("-").map(Number);
  return monthBoundsOf(y, m - 1, props.dataRange[0], props.dataRange[1]);
}

/**
 * 数字补零成两位（9 -> "09"）。
 */
function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * 按给定区间算天数（含首尾）。
 *
 * @remarks
 * 传进来的区间已经与数据边界取过交集，所以算出的就是**实际参与聚合的天数**。
 *
 * @param {MonthRange} r - 日期区间。
 * @returns {number} 实际天数；无效区间返回 0。
 */
function dayCountInRange(r: MonthRange): number {
  if (!r.start || !r.end) return 0;
  const [y1, m1, d1] = r.start.split("-").map(Number);
  const [y2, m2, d2] = r.end.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000) + 1;
}

/**
 * 某自然月的实际天数（用于判断「是否完整月」）。
 *
 * @remarks 用 Date.UTC(y, m, 0) 取「下个月第 0 天」即本月最后一天，自动处理闰年。
 * @param {string} monthKey - "YYYY-MM"（月份为 1–12）。
 * @returns {number} 该月天数；28/29/30/31。
 */
function naturalDays(monthKey: string): number {
  const [y, m] = monthKey.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/**
 * 把区间截断到指定天数（从起始日算起取前 N 天）。
 *
 * @remarks
 * 截断点越界时（目标天数大于原区间天数）原样返回 —— 绝不反向延长，
 * 否则会把「基准月只有 3 天」错误地延长成 30 天，凭空造出没有的数据。
 *
 * @param {MonthRange} base - 原始区间。
 * @param {number} days - 目标天数；<= 0 表示不截断，原样返回。
 * @returns {MonthRange} 截断后的区间。
 */
function trimToDays(base: MonthRange, days: number): MonthRange {
  if (days <= 0 || !base.start || !base.end) return base;
  const [y, m, d] = base.start.split("-").map(Number);
  // Date.UTC 的 getUTCDate 自动处理跨月借位（如 9-28 + 5 天 = 10-03）
  const cut = new Date(Date.UTC(y, m - 1, d + (days - 1)));
  const cutStr = `${cut.getUTCFullYear()}-${pad2(cut.getUTCMonth() + 1)}-${pad2(cut.getUTCDate())}`;
  return { start: base.start, end: cutStr < base.end ? cutStr : base.end };
}

/** 各选中月**未截断**的原始区间 */
const rawRanges = computed<Record<string, MonthRange>>(() => {
  const out: Record<string, MonthRange> = {};
  for (const mo of selectedMonths.value) out[mo] = rangeOf(mo);
  return out;
});

/**
 * 「同天数对比」目标天数：勾选时取**所有选中月**的最小值（全局最短），
 * 否则为 0（表示不截断）。多选语义下「所有月都对齐到最短方」，与哪个月最短无关。
 */
const targetDays = computed(() => {
  if (!sameDays.value) return 0;
  const ds = selectedMonths.value.map((mo) => dayCountInRange(rawRanges.value[mo])).filter((d) => d > 0);
  return ds.length ? Math.min(...ds) : 0;
});

/** 各选中月**应用对齐后**的对比区间 */
const rangeMap = computed<Record<string, MonthRange>>(() => {
  const out: Record<string, MonthRange> = {};
  for (const mo of selectedMonths.value) out[mo] = trimToDays(rawRanges.value[mo], targetDays.value);
  return out;
});

/**
 * 各选中月**实际参与聚合**的天数（表头「N 天」+ 不公平提醒共用）。
 *
 * @remarks
 * 必须用 rangeMap（**已对齐后**的区间）而不是 rawRanges：
 * 勾选「同天数对比」后，数据是按截断后的区间取的，
 * 若表头仍显示原始天数，就会出现「勾了同天数、8 月还写 31 天」的自相矛盾
 * （用户截图反馈）。此处与 rangeMap 保持同一口径 —— 显示即所得。
 */
const daysMap = computed<Record<string, number>>(() => {
  const out: Record<string, number> = {};
  for (const mo of selectedMonths.value) out[mo] = dayCountInRange(rangeMap.value[mo]);
  return out;
});

/**
 * 有数据的月份清单（倒序：最新在前），供多选 chips 渲染。
 *
 * @remarks 直接用 utils/month 的 availableMonths —— 与页面顶部「月份 ▾」按钮同一份来源，
 * 不会出现「顶部能选的月份这里选不到」的不一致。
 */
const monthList = computed<MonthCell[]>(() => availableMonths(props.dataRange[0], props.dataRange[1]));

/**
 * 展开面板：首次进入时初始化为「最近的完整月（最多 3 个）」。
 *
 * @remarks
 * 刻意**不**默认选「当月」：今天是 10 月 5 日时「当月」只有 1~5 号，
 * 涨跌大半是天数造成的假象。默认给已走完的完整月，是不勾任何开关时的「无偏」起点。
 */
function toggle(): void {
  expanded.value = !expanded.value;
  if (expanded.value && selectedMonths.value.length === 0) initDefaults();
}

/**
 * 默认选中「最近的、已走完的完整月」（最多 3 个）。
 *
 * @remarks 完整月 = 实际天数 == 自然天数；自动跳过当月这种只有几天的残缺月。
 * 若完整月不足 2 个（数据区间很短），退而求其次取最近的月份，交给「天数不同」提醒兜底。
 */
function initDefaults(): void {
  const complete = monthList.value.filter((mc) => dayCountInRange(rangeOf(mc.key)) === naturalDays(mc.key));
  const pick = (complete.length >= 2 ? complete : monthList.value).slice(0, 3).map((m) => m.key);
  selectedMonths.value = [...pick].sort();
}

/**
 * 点击月份 chip：在选中列表里增删，并保持升序。
 *
 * @param {string} monthKey - 被点击的 "YYYY-MM"。
 */
function toggleMonth(monthKey: string): void {
  const set = new Set(selectedMonths.value);
  if (set.has(monthKey)) set.delete(monthKey);
  else set.add(monthKey);
  selectedMonths.value = [...set].sort();
  // 若展开的指标行仍在，无需处理；selectedMonths 变化会触发重新取数
}

/**
 * 点击指标行：切换该行的时间轴大图展开状态。
 *
 * @remarks
 * **允许多行同时展开**（用户需求）：早前是「点开新行自动收起旧行」的单值语义，
 * 用户想保留已展开的以便纵向对照，故改为 toggle 进 Set。
 * 用新 Set 替换而非原地 add/delete，确保 Vue 的 ref 变更检测被触发。
 *
 * @param {string} key - 指标 key。
 */
function toggleRow(key: string): void {
  const next = new Set(expandedRowKeys.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedRowKeys.value = next;
}

/* ---------------- 取数 ---------------- */

/** 各月聚合结果；null=该月无数据（如淡季 404） */
const aggMap = ref<Record<string, Record<string, number | null> | null>>({});
/** 某月取数失败时的提示语 */
const emptyMap = ref<Record<string, string>>({});
const loading = ref(false);

/**
 * 把逐日点聚合成单月指标值（比率类走 aggregateMetrics 的「先求和再相除」）。
 *
 * @param {any[]} daily - 该区间的逐日数据。
 * @returns {Record<string, number | null>} 聚合后的指标值。
 */
function monthly(daily: SpuDailyPoint[]): Record<string, number | null> {
  return aggregateMetrics(daily.map((d) => d.metrics));
}

/**
 * 并发拉取所有选中月的汇总数据。
 *
 * @remarks
 * 用 Promise.allSettled 而不是 Promise.all：后端对「该 SPU 在区间内无数据」抛 404，
 * 一个月份没数据是**正常业务情况**（比如淡季），不该把另一个月份的成功结果一起丢掉。
 * allSettled 能在不吞异常的前提下区分各请求成败，逐月落地。
 */
async function load(): Promise<void> {
  const months = selectedMonths.value;
  if (!months.length) return;
  loading.value = true;
  const q = { shop: props.shop };
  const tasks = months.map((mo) => {
    const rg = rangeMap.value[mo];
    return fetchSpuAnalysis(props.moduleId, props.spu, { ...q, start: rg.start, end: rg.end });
  });
  const results = await Promise.allSettled(tasks);
  const newAgg: Record<string, Record<string, number | null> | null> = {};
  const newEmpty: Record<string, string> = {};
  results.forEach((res, i) => {
    const mo = months[i];
    if (res.status === "fulfilled") newAgg[mo] = monthly(res.value.daily);
    else {
      newAgg[mo] = null;
      newEmpty[mo] = res.reason instanceof Error ? res.reason.message : "加载失败";
    }
  });
  aggMap.value = newAgg;
  emptyMap.value = newEmpty;
  loading.value = false;
}

/** 首次展开或选中月份 / 同天数开关变化时重新取数 */
watch([selectedMonths, sameDays], () => {
  if (expanded.value) load();
});

/** 所选月份天数是否不全相等（不等则对比有失真风险） */
const unbalanced = computed(() => {
  if (sameDays.value) return false; // 已对齐，无需提醒
  const ds = Object.values(daysMap.value).filter((d) => d > 0);
  return new Set(ds).size > 1;
});

/* ---------------- 表格数据 ---------------- */

/** 单个指标的逐月值 */
interface MonthVal {
  /** 月份 "YYYY-MM" */
  month: string;
  /** 聚合原始值（null = 该月无此值） */
  raw: number | null;
  /** 格式化后的展示文本 */
  text: string;
  /** 是否有有效值 */
  has: boolean;
}

/** 相邻两月的 diff 段 */
interface Segment {
  /** 起始月（较早） */
  from: string;
  /** 结束月（较晚） */
  to: string;
  /** 差额文本；百分比类记 pt，缺失段为 "—" */
  diffText: string;
  /** 相对变化率文本；分母 0 为 "--"，缺失段为 "—" */
  deltaText: string;
  /** 变化方向：1=上升，-1=下降，0=持平/无数据 */
  dir: number;
}

/**
 * 单个指标的对比行（多月份）。
 */
interface CompareRow {
  /** 指标 key */
  key: string;
  /** 指标中文名 */
  title: string;
  /** 指标分组，用于插入分组标题行 */
  group: string;
  /** 分组中文名 */
  groupTitle: string;
  /**
   * 是否**百分比类**指标（format === "percent"：转化率 / 搜索点击率 / 推广占比）。
   *
   * @remarks 刻意**不用 `agg.kind === "ratio"`** 做判断：客单价是 `金额/客户数` 的 ratio 聚合，
   * 但单位是金额，差额该显示 `¥3.28` 而不是 `0.03pt`。决定「用不用 pt」的是**单位**而非**算法**。
   */
  isPercent: boolean;
  /** 指标单位（currency/percent/decimal/number），用于差额格式化 */
  format: MetricFormat;
  /** 各选中月的逐月值（顺序与 selectedMonths 一致） */
  monthVals: MonthVal[];
  /** 相邻月的 diff 段（长度 = 月份数 − 1，顺序对应原序相邻对） */
  segments: Segment[];
}

/** 分组中文名映射 */
const GROUP_TITLE: Record<string, string> = {
  detail: "商品明细表",
  promo: "推广数据表",
  extra: "退款 / 其他",
};

/**
 * 所有指标逐月对比的计算结果（未分组）。
 *
 * @remarks 关键口径：
 * - 每个月**独立**调 aggregateMetrics（比率类仍是「先求和再相除」），不是两月相减；
 * - **差额列的「百分点 vs 数值」按指标单位（format）判断，不按聚合方式（agg.kind）**：
 *   百分比类差额记 pt（绝对百分点差），客单价虽是 ratio 聚合但单位是**金额**，记 `¥3.28` 而非 `0.03pt`；
 * - **变化列全类型都给相对变化率** `(新 − 旧) / |旧|`，分母为 0 时给 `--`（避免 Infinity）。
 *
 * 相邻 diff 段严格按**原序相邻对**计算：若中间某月缺失，则该段显示 "—"，
 * 但两端仍各自与相邻月成对（即「不跳月」），与表格「每两个相邻月份之间一个箭头」的展示一致。
 */
const rows = computed<CompareRow[]>(() => {
  const months = selectedMonths.value;
  if (!months.length) return [];
  const out: CompareRow[] = [];
  for (const m of METRICS) {
    // ⚠️ 用 format（单位）而非 agg.kind（聚合方式）判断是否走百分点
    const isPercent = m.format === "percent";
    const monthVals: MonthVal[] = months.map((mo) => {
      const agg = aggMap.value[mo];
      const raw = agg ? (agg[m.key] as number | null) : null;
      return { month: mo, raw, text: agg ? fmtBy(m.format, raw) : "—", has: agg != null && raw != null };
    });
    const segments: Segment[] = [];
    for (let i = 0; i < months.length - 1; i++) {
      const a = monthVals[i];
      const b = monthVals[i + 1];
      if (a.has && b.has) {
        const diff = (b.raw as number) - (a.raw as number);
        const dir = diff === 0 ? 0 : diff > 0 ? 1 : -1;
        const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
        const diffText = isPercent
          ? `${sign}${(Math.abs(diff) * 100).toFixed(2)}pt`
          : `${sign}${fmtBy(m.format, Math.abs(diff))}`;
        let deltaText = "--";
        const base = a.raw as number;
        if (base !== 0) {
          const r = diff / Math.abs(base);
          deltaText = `${r > 0 ? "+" : r < 0 ? "−" : ""}${(Math.abs(r) * 100).toFixed(1)}%`;
        }
        segments.push({ from: a.month, to: b.month, diffText, deltaText, dir });
      } else {
        segments.push({ from: a.month, to: b.month, diffText: "—", deltaText: "—", dir: 0 });
      }
    }
    out.push({
      key: m.key,
      title: m.title,
      group: m.group,
      groupTitle: GROUP_TITLE[m.group] ?? m.group,
      isPercent,
      format: m.format,
      monthVals,
      segments,
    });
  }
  return out;
});

/**
 * 表头 / 每行要渲染的「单元格序列」：指标列 + (值列 / 箭头列) 交替。
 *
 * @remarks 表头与数据行**共用同一份 cells**，保证列宽严格对齐。箭头列只在相邻值列之间出现。
 */
const cells = computed(() => {
  const arr: { type: "val" | "arrow"; month?: string; idx?: number; sidx?: number }[] = [];
  const months = selectedMonths.value;
  for (let i = 0; i < months.length; i++) {
    arr.push({ type: "val", month: months[i], idx: i });
    if (i < months.length - 1) arr.push({ type: "arrow", sidx: i });
  }
  return arr;
});

/**
 * 变化方向的 CSS 类（绿涨红跌）。
 *
 * @param {{ dir?: number }} seg - 段（只用到变化方向）。
 * @returns {string} CSS 类名："up" / "down" / "flat"。
 */
function dirClass(seg: { dir?: number }): string {
  const d = seg.dir ?? 0;
  return d > 0 ? "up" : d < 0 ? "down" : "flat";
}

/**
 * 脚注里的 pt 示例：从真实数据里挑一个**百分比类**指标（优先成交转化率），
 * 用它最早两个有值月份拼出一句可读的说明。
 *
 * @remarks 刻意**动态生成**而不是写死「1.92% → 1.56%」：表头月份随用户选择变化，
 * 写死的例子会和用户眼前的实际数字对不上号，反而让人怀疑「pt 是不是算错了」。
 */
const ptExample = computed(() => {
  const candidates = ["conversion_rate", "search_click_rate", "promotion_ratio"];
  for (const key of candidates) {
    const r = rows.value.find((x) => x.key === key);
    if (!r) continue;
    const seg = r.segments[0];
    if (!seg || seg.diffText === "—") continue;
    const num = seg.diffText.replace(/pt$/, "").replace(/[+−]/, "");
    const a = r.monthVals[0];
    const b = r.monthVals[1];
    if (!a || !b || !a.has || !b.has) continue;
    return { left: a.text, right: b.text, diff: num, delta: seg.deltaText.replace(/^[+−]/, "") };
  }
  return null;
});

/**
 * 时间轴所在容器的实际宽度（px）。
 *
 * @remarks
 * ⚠️ 必须用**真实 DOM 宽度**而不是写死viewBox 宽度。
 * 之前 viewBox 固定 620，而容器实际宽 1253，SVG 被浏览器整体横向拉伸约 2 倍，
 * 导致「内容太大、违和」（用户反馈）。这里测量容器宽度后按1:1 出viewBox，
 * 使SVG 内部坐标单位 == CSS 像素，字号/圆点才是所见即所得。
 * SSR/无 DOM 时回退 620。
 */
const timelineBoxW = ref(620);
/**
 * 容器尺寸观察器句柄。
 * @remarks 切换指标行 / 组件卸载时必须 disconnect，否则监听会一直累积（内存泄漏）。
 */
let tlObserver: ResizeObserver | null = null;
/**
 * 已绑定的展开容器集合（用于多行同时展开时统一管理观察目标）。
 *
 * @remarks
 * ⚠️ 允许多行展开后，`.mc-exp` 会有多个；早前实现是「每次绑定先 disconnect 旧的」，
 * 只跟踪单个元素——多行时会导致**只有最后绑定的那个被观察**，
 * 先展开的行容器尺寸变化（如窗口缩放）不会更新 viewBox。
 * 故改为 Set 累积所有容器，尺寸取**最大可用宽**（各容器同列、宽度相同，取最大最稳妥）。
 */
const expBoxes = new Set<HTMLElement>();
/**
 * 绑定展开行 DOM 并测量宽度。
 *
 * @remarks
 * ⚠️ **必须用函数式 ref（v-for 内 ref 会聚成数组）**：
 * `.mc-exp` 位于 `v-for` 之内，若写成 `ref="expBox"`，Vue 收集到的是
 * `HTMLElement[]` 数组而非元素，取 `clientWidth` 得 undefined → viewBox 退回 620，
 * SVG 被拉伸近 2 倍（「时间轴内容太大、违和」反馈的直接原因）。
 *
 * ⚠️ **null（解绑）时不要清空集合**：多行展开时 Vue 会在每次 patch 依次传null/元素，
 * 任何一次 null 都整体清空会把其它已展开行的容器踢出观察范围。
 * 解绑的元素已从 DOM 移除，其 `clientWidth` 为 0，取 max 时自然被忽略；
 * 真正的清理由 `onBeforeUnmount` 兜底。
 *
 * @param el - 元素引用；解绑时 Vue 传入 null。
 * @param refs - Vue 内部传入的 refs 集合（本组件无需使用，仅为满足 VNodeRef 签名）。
 */
const setExpBox: VNodeRef = (el) => {
  const box = (el as HTMLElement | null) ?? null;
  if (box) observeExpBox(box);
};
/**
 * 容器宽度变化时同步 viewBox。
 *
 * @remarks
 * 用 ResizeObserver 而非 window.resize —— 侧栏折叠、窗口缩放都会改容器宽度，
 * 但只关心「这个图表盒子本身多宽」，与窗口事件解耦更精确。
 * 每次绑定重建 Observer（可重复 observe 多个目标），宽度取所有容器的最大值。
 */
function observeExpBox(el: HTMLElement): void {
  expBoxes.add(el);
  tlObserver?.disconnect();
  const apply = () => {
    // 扣掉 .mc-exp 的左右 padding+border，得到 SVG 可用宽度；多行时取最大
    let best = 0;
    for (const box of expBoxes) best = Math.max(best, Math.round(box.clientWidth) - 26);
    if (best > 0) timelineBoxW.value = Math.max(360, best);
  };
  apply();
  tlObserver = new ResizeObserver(apply);
  for (const box of expBoxes) tlObserver.observe(box);
}
onBeforeUnmount(() => { tlObserver?.disconnect(); tlObserver = null; expBoxes.clear(); });

/**
 * 时间轴大图的几何（只取该指标**有值**的月份节点）。
 *
 * @remarks 时间轴与表格不同：表格按「原序相邻对」成对（缺失段显示"—"），
 * 而时间轴是单指标走势，**跳过空月后把有值的月连成线**，更贴近「走势」语义。
 * ⚠️ 连线在**到达下一个圆点之前就停止**（留 10px 间隙），箭头 marker 落在间隙里，
 * 这样箭头不会被圆点遮住（用户明确要求）。
 *
 * ⚠️ **改为按 key 取的普通函数，不是 computed**：
 * 因为允许多行同时展开（`expandedRowKeys` 是 Set），每个展开行都要一份自己的几何，
 * 无法用「单一 computed + 依赖单key」表达。
 * 代价是每次渲染重算，但几何计算只涉及几个月的数据、开销可忽略；
 * 换来的是多行展开互不干扰。
 *
 * @param key - 要展开时间轴的指标行 key。
 * @returns 该行的时间轴几何；未找到行返回 null；有效月份不足 2 个返回 `{ single: true }` 走退化提示。
 */
function tlOf(key: string) {
  const row = rows.value.find((r) => r.key === key);
  if (!row) return null;
  const vals = row.monthVals.filter((v) => v.has).map((v) => ({ month: v.month, raw: v.raw as number, text: v.text }));
  if (vals.length < 2) return { n: vals.length, single: true as const };
  const n = vals.length;
  /**
   * viewBox 宽度：**按月份数动态决定**，并以容器宽度为上限。
   *
   * @remarks
   * 目标：月份少时不该把2 个点拉满 1200px（连线横穿整屏、气泡孤零零居中，很违和），
   * 但也不能写死（月份多了又会被裁切）。故用「每段固定间距」反推所需宽度：
   *   W = 左右留白 + 段数 × 段间距
   * 再夹在 [最小可读宽, 容器实际宽] 之间 —— 月份少则窄、月份多则自然变宽，
   * 超出容器时靠 .mc-tablewrap/外层 overflow 处理，不会压缩节点间距。
   */
  const SEG = 132;         // 相邻节点的目标间距（约等于气泡宽度上限，够放「−46,690.39」）
  const MIN_W = 460;       // 2 个月时的最小可读宽度
  const PAD_X = 62;        // 左右留白：容纳月份标签与数值标签不贴边
  const needW = PAD_X * 2 + (n - 1) * SEG;
  // 多行同时展开时，各容器宽度相同（同一表格列），取已测量到的最大值作上限即可
  const capW = Math.max(360, timelineBoxW.value);
  const W = Math.max(MIN_W, Math.min(needW, Math.max(capW, MIN_W)));
  const H = 200;
  const x0 = PAD_X;
  const x1 = W - PAD_X;
  const xs = vals.map((_, i) => x0 + ((x1 - x0) * i) / (n - 1));
  const nums = vals.map((v) => v.raw);
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const pad = (max - min) * 0.3 || 30; // 上下留白，避免点贴边
  const lo = min - pad;
  const hi = max + pad;
  const yTop = 60;
  const yBot = 150;
  const ys = nums.map((num) => yBot - ((num - lo) / (hi - lo)) * (yBot - yTop));
  // 仅在有值相邻月之间算 diff（跳过空月）
  const segs: { x: number; y: number; diffText: string; deltaText: string; dir: number }[] = [];
  for (let i = 0; i < n - 1; i++) {
    const diff = vals[i + 1].raw - vals[i].raw;
    const dir = diff === 0 ? 0 : diff > 0 ? 1 : -1;
    const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
    const diffText = row.isPercent
      ? `${sign}${(Math.abs(diff) * 100).toFixed(2)}pt`
      : `${sign}${fmtBy(row.format, Math.abs(diff))}`;
    let deltaText = "--";
    const base = vals[i].raw;
    if (base !== 0) {
      const r = diff / Math.abs(base);
      deltaText = `${r > 0 ? "+" : r < 0 ? "−" : ""}${(Math.abs(r) * 100).toFixed(1)}%`;
    }
    // 气泡整体上移：两行文本需要更高的净空，避免压到节点/连线
    segs.push({ x: (xs[i] + xs[i + 1]) / 2, y: Math.min(ys[i], ys[i + 1]) - 26, diffText, deltaText, dir });
  }
  return {
    n,
    single: false as const,
    W,
    H,
    baseX1: x0,
    baseX2: x1,
    baseY: 168,
    monthY: 190,
    yMid: (yTop + yBot) / 2,
    r: 4,
    // 字号：1:1 下即为真实 CSS 像素。上次缩到 11/12 偏小，回调到 12.5/13.5
    fs: 12.5,
    fsBubble: 13.5,
    xs,
    ys,
    vals: vals.map((v, i) => ({ x: xs[i], y: ys[i], text: v.text, month: v.month })),
    segs,
  };
}

/**
 * 表格里真正要渲染的行：把分组标题作为伪行插进指标行之间。
 */
interface TableRow {
  /** 唯一 key：指标行用指标 key，分组行用 "g:" + 分组名 */
  key: string;
  /** 行类型：group = 分组标题伪行，row = 指标数据行 */
  kind: "group" | "row";
  /** 行内主文案（指标名或分组名） */
  title: string;
  /** 指标行携带的对比数据（分组行为 undefined） */
  row?: CompareRow;
}

const tableRows = computed<TableRow[]>(() => {
  const out: TableRow[] = [];
  let lastGroup = "";
  for (const r of rows.value) {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      out.push({ key: `g:${r.group}`, kind: "group", title: r.groupTitle });
    }
    out.push({ key: r.key, kind: "row", title: r.title, row: r });
  }
  return out;
});

/** 是否至少有一个月取到数据（否则整块提示「无数据」） */
const hasAnyData = computed(() => Object.values(aggMap.value).some((v) => v !== null));
</script>

<template>
  <div class="mcompare" data-testid="spu-month-compare">
    <!-- 折叠头：默认收起，点击展开 -->
    <button class="mc-head" :class="{ open: expanded }" type="button" @click="toggle">
      <span class="mc-title">月份对比</span>
      <!-- 动作召唤标签：收起时明示「这里能点」。为什么不用 hover 才出现的提示？
           因为折叠头是页面中部一个不起眼的横条，用户的鼠标未必悬上去；
           hover 只能反馈「已注意到」，静态标识才解决「压根没看到」。 -->
      <span v-if="!expanded" class="mc-cta">点击展开</span>
      <span v-if="expanded && selectedMonths.length" class="mc-hint">{{ selectedMonths[0] }} ~ {{ selectedMonths[selectedMonths.length - 1] }}（{{ selectedMonths.length }} 个月）</span>
      <span v-else class="mc-hint mc-hint-dim">勾选多个月份，横向对比各项指标</span>
      <!-- 收起指示符：素材三角（右箭头.svg）。外层 span 只负责占位，内层 svg 负责旋转——
         三角宽高比非 1:1，若直接旋转 svg 元素会因宽高互换导致展开/收起时抖動 -->
      <span class="mc-caret" :class="{ up: expanded, pulse: hintPulse }">
        <svg viewBox="294 159 428 706" aria-hidden="true">
          <path d="M715.8 493.5L335 165.1c-14.2-12.2-35-1.2-35 18.5v656.8c0 19.7 20.8 30.7 35 18.5l380.8-328.4c10.9-9.4 10.9-27.6 0-37z" />
        </svg>
      </span>
    </button>

    <div v-if="expanded" class="mc-body">
      <!-- 月份多选 chips + 同天数开关 -->
      <div class="mc-pickers">
        <span class="mc-plabel">对比月份</span>
        <button
          v-for="mc in monthList"
          :key="mc.key"
          type="button"
          class="mc-chip"
          :class="{ on: selectedMonths.includes(mc.key) }"
          @click="toggleMonth(mc.key)"
        >
          {{ mc.key }}
        </button>
        <label class="mc-same" title="把所有月份都对齐到天数较少的一方再比较，消除天数差异造成的失真">
          <input v-model="sameDays" type="checkbox" />
          <span>同天数对比</span>
        </label>
      </div>

      <p v-if="selectedMonths.length < 2" class="mc-warn">请至少选择 2 个月份进行对比。</p>
      <p v-else-if="unbalanced" class="mc-warn">
        所选月份天数不同，涨跌可能受天数影响；可勾选「同天数对比」消除影响。
      </p>
      <!-- 已对齐提示：明确告知各月被截到同一天数，避免用户误以为数据异常 -->
      <p v-else-if="sameDays && targetDays > 0" class="mc-warn mc-warn-soft">
        已按<strong>{{ targetDays }} 天</strong>对齐：各月均只取前{{ targetDays }}天数据，消除天数差异影响。
      </p>

      <div v-if="loading" class="mc-loading">对比数据加载中…</div>

      <div v-else-if="selectedMonths.length >= 2 && hasAnyData" class="mc-tablewrap">
        <div class="mc-grid">
          <!-- 表头：指标 + 月份序列（与数据行共用 cells，列宽严格对齐） -->
          <div class="mc-hrow">
            <div class="mc-metric mc-th">指标</div>
            <div class="mc-seq">
              <template v-for="(c, ci) in cells" :key="'h' + ci">
                <div v-if="c.type === 'val'" class="mc-val mc-th">
                  {{ c.month }}<span class="mc-th-sub">{{ daysMap[c.month!] }} 天</span>
                </div>
                <div v-else class="mc-arrow"></div>
              </template>
            </div>
          </div>

          <!-- 分组标题 / 指标行 -->
          <template v-for="r in tableRows" :key="r.key">
            <div v-if="r.kind === 'group'" class="mc-grouprow">{{ r.title }}</div>
            <template v-else>
              <div
                class="mc-rowline"
                :class="{ expanded: isRowExpanded(r.key) }"
                @click="toggleRow(r.key)"
              >
                <div class="mc-metric">
                  <!-- 展开指示符同样用 右箭头.svg 素材：收起朝右、展开旋转朝下。
                       悬停变红（用户要求）：红三角是「可展开」的召唤 -->
                  <span class="mc-caret-row" :class="{ on: isRowExpanded(r.key) }">
                    <svg viewBox="294 159 428 706" aria-hidden="true">
                      <path d="M715.8 493.5L335 165.1c-14.2-12.2-35-1.2-35 18.5v656.8c0 19.7 20.8 30.7 35 18.5l380.8-328.4c10.9-9.4 10.9-27.6 0-37z" />
                    </svg>
                  </span>
                  {{ r.title }}
                </div>
                <div class="mc-seq">
                  <template v-for="(c, ci) in cells" :key="'r' + r.key + ci">
                    <div v-if="c.type === 'val'" class="mc-val">{{ r.row!.monthVals[c.idx!].text }}</div>
                    <div v-else class="mc-arrow">
                      <span class="bubble" :class="dirClass(r.row!.segments[c.sidx!])">
                        <span class="bubble-diff">{{ r.row!.segments[c.sidx!].diffText }}</span>
                        <span class="bubble-delta">{{ r.row!.segments[c.sidx!].deltaText }}</span>
                      </span>
                      <!-- 箭头本体：内联 SVG，渐长虚线段 + 三角头。⚠️ 这里的三角头属于「月份连线箭头」，
                           与指标行的展开三角是两回事，不要混用素材 -->
                      <svg class="line" viewBox="0 0 116 12" aria-hidden="true">
                        <rect v-for="(d, di) in arrowDashes" :key="'d' + di" :x="d.x" y="4.5" :width="d.w" height="3" rx="1" fill="currentColor" />
                        <polygon points="103,1 115,6 103,11" fill="currentColor" />
                      </svg>
                    </div>
                  </template>
                </div>
              </div>
              <!-- 点击展开：该指标的时间轴大图（内联，行高扩大）
                   ⚠️ **允许多行同时展开**：条件是 isRowExpanded(r.key) 而非「等于唯一展开行」。
                   用函数式 ref 而非 ref="expBox"：本元素在 v-for 内，字符串 ref 会聚成数组。
                   ⚠️ 外层 template v-for 把 tlOf(r.key) 的结果绑成局部变量 `tl`：
                   多行展开时每行要各用自己的几何，无法用一个全局 computed 承载；
                   `[tlOf(r.key)]` 是「单元素数组」，仅为在模板里造一个局部别名，
                   与前面 tableRows/cells 的 v-for 别名用法一致。 -->
              <template v-if="isRowExpanded(r.key)" v-for="tl in [tlOf(r.key)]">
              <div v-if="tl" :ref="setExpBox" class="mc-exp">
                <div class="mc-exp-title">
                  <!-- 只有指标名放大变绿（用户要求），后半段说明文字保持灰色小字 -->
                  <span class="mc-exp-metric">{{ r.title }}</span>
                  <span class="mc-exp-meta"> · 时间轴（{{ selectedMonths[0] }} ~ {{ selectedMonths[selectedMonths.length - 1] }}）</span>
                </div>
                <!-- 宽度用 tl.W（按月份数动态算出的目标宽）而非 100%：
                     配合 CSS `max-width:100%` 实现「月份少→窄图靠左、月份多→变宽」，
                     且内部坐标与显示像素 1:1，字号所见即所得。
                     ⚠️ tl 必须是**当前行自己的**几何（多行展开时各行独立），
                     所以用函数式computed 而非全局单值。 -->
                <svg
                  v-if="tl && !tl.single"
                  class="mc-tl"
                  :viewBox="`0 0 ${tl.W} ${tl.H}`"
                  :width="tl.W"
                  :height="tl.H"
                  role="img"
                  :aria-label="r.title + ' 时间轴'"
                >
                  <title>{{ r.title }} 时间轴</title>
                  <desc>各月数值节点，相邻月之间箭头上方气泡为差额与变化，绿涨红跌</desc>
                  <defs>
                    <!-- 箭头用灰色，与表内蓝色虚线箭头区分开：这里是「走势」辅助线，不是主视觉 -->
                    <marker :id="`mcArw-${r.key}`" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                      <path d="M2 1L8 5L2 9" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
                    </marker>
                  </defs>
                  <!-- 基线 -->
                  <line :x1="tl.baseX1" :y1="tl.baseY" :x2="tl.baseX2" :y2="tl.baseY" stroke="#e2e8f0" stroke-width="0.5" />
                  <!-- 连线：在到达下一个圆点前 10px 处停止，箭头落在间隙里，不被圆点遮住 -->
                  <line
                    v-for="(s, i) in tl.segs"
                    :key="'seg' + i"
                    :x1="tl.xs[i] + 10"
                    :y1="tl.ys[i]"
                    :x2="tl.xs[i + 1] - 10"
                    :y2="tl.ys[i + 1]"
                    stroke="#94a3b8"
                    stroke-width="1.5"
                    :marker-end="`url(#mcArw-${r.key})`"
                  />
                  <!-- 节点（画在连线之上，但连线已留间隙，箭头不被遮）：蓝色空心圈 -->
                  <circle v-for="(p, i) in tl.vals" :key="'nd' + i" :cx="p.x" :cy="p.y" :r="tl.r" fill="#fff" stroke="#4a9db8" stroke-width="1.6" />
                  <!-- 数值标签 -->
                  <text v-for="(p, i) in tl.vals" :key="'vt' + i" :x="p.x" :y="p.y > tl.yMid ? p.y + 18 : p.y - 10" text-anchor="middle" :font-size="tl.fs" fill="#334155">{{ p.text }}</text>
                  <!-- 月份标签 -->
                  <text v-for="(p, i) in tl.vals" :key="'mt' + i" :x="p.x" :y="tl.monthY" text-anchor="middle" :font-size="tl.fs" fill="#64748b">{{ p.month }}</text>
                  <!-- 气泡：差额与变化**分两行**（去掉「·」分隔符，避免挤在一行读不清） -->
                  <text
                    v-for="(s, i) in tl.segs"
                    :key="'bb' + i"
                    :x="s.x"
                    :y="s.y"
                    text-anchor="middle"
                    :font-size="tl.fsBubble"
                    :class="dirClass(s)"
                  >{{ s.diffText }}<tspan :x="s.x" :dy="tl.fsBubble * 1.25">{{ s.deltaText }}</tspan></text>
                </svg>
                <p v-else class="mc-exp-empty">该指标所选月份数据不足（需至少 2 个有值月份），无法绘制时间轴。</p>
              </div>
              </template>
            </template>
          </template>
        </div>
        <!-- 脚注：解释 pt（百分点）与两列的口径差异 -->
        <p class="mc-foot">
          相邻两月的差额 = 右月 − 左月的<strong>绝对差</strong>；变化 = 相对变化率 =(右月 − 左月) ÷ |左月|。<br />
          <strong>pt</strong> = <strong>百分点</strong>，用于百分比类指标（成交转化率 / 搜索点击率 / 推广占比）：
          <template v-if="ptExample">
            以成交转化率 {{ ptExample.left }} → {{ ptExample.right }} 为例，差为 <strong>{{ ptExample.diff }} 个百分点</strong>
            （记 −{{ ptExample.diff }}pt），而「相对降了 {{ ptExample.delta }}」是同一件事的另一种说法。
          </template>
          <template v-else>
            例如 1.92% 降到 1.56% 是「少了 0.35 个百分点」（记 −0.35pt），「相对降了 18.5%」是同一件事的另一种说法。
          </template>
          客单价、ROI 等金额或倍数类指标按各自单位记差额（如 +¥21.58、−0.38），不用 pt。
        </p>
      </div>

      <p v-else-if="selectedMonths.length >= 2 && !hasAnyData" class="mc-warn mc-warn-soft">
        所选月份均无该 SPU 的数据。
      </p>

      <!-- 单月无数据的软提示（不阻断其它月） -->
      <p v-for="(msg, mo) in emptyMap" :key="'e' + mo" class="mc-warn mc-warn-soft">
        {{ mo }}：{{ msg }}。
      </p>
    </div>
  </div>
</template>

<style scoped>
.mcompare {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  overflow: hidden;
}
/* 折叠头 */
/* 折叠头：左内边距与表格内文字缩进对齐（见下方 .mc-rowline 的 --mc-inset） */
.mc-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 13px 18px 13px var(--mc-inset);
  /* 折叠头做成「按钮」外观：给底色 + 边框 + 悬停加深。
     为什么必须这样：收起态它就是页面中部一个不起眼的横条，若无边框无 hover，
     视觉上和普通说明文字无异，用户根本不会意识到这里能点。 */
  background: var(--color-surface-2);
  border: none;
  border-bottom: 1px solid var(--color-border-4);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, box-shadow 0.15s;
}
/* 悬停：底色加深 + 左侧浮出一道品牌色竖条，强化「可点」语义。
   竖条用 box-shadow 而非 border-left，避免撑动整行布局（整行 padding 含
   --mc-inset 缩进，加 border 会让标题横向位移1px 与下方表格对不齐）。 */
.mc-head:hover {
  background: var(--color-brand-tint-3);
  box-shadow: inset 3px 0 0 var(--color-brand);
}
/* 展开态：还原为白底、去掉底部描边与左侧红条。
   否则收起/展开两态除三角朝向与文案外毫无差别，用户看不出「现在能不能点」；
   展开后这一块已是内容区，不该继续挂「可展开」的视觉暗示。 */
.mc-head.open,
.mc-head.open:hover {
  background: var(--color-surface);
  box-shadow: none;
  border-bottom-color: var(--color-border);
}
.mc-head:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: -2px;
}
.mc-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-strong);
}
/* 动作召唤标签：收起时明示可点，展开后自动消失（内容已展开，再提示就冗余）。 */
.mc-cta {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--color-brand);
  background: var(--color-brand-tint);
  border: 1px solid var(--color-brand-border);
  border-radius: var(--radius-pill);
  padding: 2px 9px;
  line-height: 1.4;
}
.mc-hint {
  font-size: 12px;
  color: var(--color-text-4);
}
.mc-hint-dim {
  color: var(--color-text-4);
}
/* 折叠头三角指示符：素材 右箭头.svg（实心右指三角）
   素材本体 bbox x∈[300,715.8]、y∈[165.1,858.9]，故viewBox 裁到"294 159 428 706" 只留三角、
   去掉四周留白；内层 svg 按该 viewBox 比例给尺寸，保证不变形。
   ⚠️ 外层 span 尺寸要略大于旋转后的三角（9x15 转 90° 后占15x9），否则展开时会被挤压抖动。
   素材本体朝右：收起=朝下（90deg）、展开=朝上（-90deg）。 */
.mc-caret {
  width: 15px;
  height: 15px;
  margin-left: auto;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mc-caret svg {
  width: 9px;
  height: 15px;
  /* 收起态用品牌红：淡灰三角在缩略显示时几乎隐形，看不出「这是展开控件」。
     展开态改回中性色（见 .mc-head.open 下）——红三角是「可展开」的召唤，
     内容已展开后继续用红会让人误以为还有别的可展开内容。 */
  fill: var(--color-brand);
  transform: rotate(90deg);
  transition: transform 0.18s, fill 0.15s;
}
.mc-head.open .mc-caret svg {
  fill: var(--color-text-5);
}
.mc-caret.up svg {
  transform: rotate(-90deg);
}
/* 悬停时三角加深：收起/展开两态都给出「点了有反应」的反馈 */
.mc-head:hover .mc-caret svg {
  fill: var(--color-brand-dark);
}
/* 首次进入的「呼吸」提示：三角轻微上下浮动 3 遍后停。
   ⚠️ 只在 hintPulse 为 true 时播（session 内一次），并加 reduced-motion 降级——
   常驻循环动画会变成页面噪音，反而让人不再注意它；一次性招徕才有效。 */
@media (prefers-reduced-motion: no-preference) {
  .mc-caret.pulse svg {
    animation: mc-hint-pulse 0.85s ease-in-out 3;
  }
}
@keyframes mc-hint-pulse {
  0%,
  100% {
    translate: 0 0;
  }
  50% {
    translate: 0 3px;
  }
}
/* 行内文字的统一左缩进：所有「有底色/ 有分割线」的行都用它，
   这样斑马纹能铺满整个框宽，而文字又与框左边缘保持距离。 */
.mcompare {
  --mc-inset: 48px;
}
/* .mc-body 不再带左padding！
   原因：若在容器层加 padding-left，表格画布(.mc-grid)会整体右移，
   导致 .mc-hrow/.mc-grouprow/.mc-rowline 的「底色 + 分割线」只画到内缩处，
   两侧露出白边 —— 视觉上像「表格被套进了另一个独立容器」（用户截图反馈）。
   缩进改由各行自身的 padding-left 完成：底色铺满、文字缩进。 */
.mc-body {
  padding: 0 0 16px 0;
}
/* 月份多选 chips + 同天数开关（无底色，用左右 padding 保持与表格文字的缩进一致） */
.mc-pickers {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 0 18px 12px var(--mc-inset);
  border-bottom: 1px solid var(--color-bg);
}
.mc-plabel {
  font-size: 12px;
  color: var(--color-text-4);
}
.mc-chip {
  font-size: 12px;
  color: var(--color-text-3);
  background: var(--color-surface);
  border: 1px solid var(--color-text-6);
  border-radius: var(--radius-pill);
  padding: 3px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.mc-chip:hover {
  border-color: var(--color-text-5);
}
.mc-chip.on {
  color: var(--color-surface);
  background: var(--color-brand); /* 项目主色，与导航/品牌一致 */
  border-color: var(--color-brand);
  font-weight: 500;
}
.mc-same {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--color-text-3);
  margin-left: auto;
  cursor: pointer;
}
/* 黄色提示条：贴在框内、缩进与表格文字一致（左右用 margin 留出，
   这样它的黄色底也不会显得像另一个独立容器） */
.mc-warn {
  margin: 10px 18px 0 var(--mc-inset);
  font-size: 12px;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--radius-md);
  padding: 6px 10px;
}
/* 「某月无数据」是数据缺失而非用法提醒，用中性色 */
.mc-warn-soft {
  color: var(--color-text-4);
  background: var(--color-surface-2);
  border-color: var(--color-border-2);
}
.mc-loading {
  padding: 24px 0;
  text-align: center;
  font-size: 12px;
  color: var(--color-text-4);
}
/* 表格脚注：解释 pt（百分点）与两列口径。
   刻意加大字号（11→12.5px）并用更深的色（--color-text-5 → --color-text-3）：
   原样式太小太淡、贴在灰底上几乎读不清（用户截图反馈） */
.mc-foot {
  margin: 8px 18px 0 var(--mc-inset);
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--color-text-3);
}
.mc-foot strong {
  font-weight: 600;
  color: var(--color-text-2);
}
/* 表格容器：横向滚动（月份多选可能溢出）；不在此处加横向 margin/padding，
   否则行的底色与分割线会被内缩、右侧提前「断掉」 */
.mc-tablewrap {
  overflow-x: auto;
  margin-top: 4px;
}
/* min-width 只是「列不被压扁」的兜底；宽度不足时靠 .mc-tablewrap 横向滚动，
   不再让它决定视觉边界（此前 min-width:560px 配合容器 padding 导致右侧留白） */
.mc-grid {
  min-width: 560px;
  margin: 0;
}
/* 表头行 / 数据行：指标列固定宽度 + 右侧序列 flex */
.mc-hrow,
.mc-rowline {
  display: flex;
  align-items: stretch;
}
/* 关键：底色/分割线要铺满整个框宽（不带横向 margin），
   缩进只作用于文字 —— 故 padding 用「左缩进 + 右18px」的写法，
   右侧留白仅是为了最后一个月份列不贴框右边，底色仍铺满。 */
.mc-hrow {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
  background: var(--color-surface-2);
  padding: 8px 18px 8px var(--mc-inset);
  border-bottom: 1px solid var(--color-text-6);
}
.mc-rowline {
  padding: 7px 18px 7px var(--mc-inset);
  border-bottom: 1px solid var(--color-border-2);
  cursor: pointer;
  transition: background 0.15s;
}
.mc-rowline:hover {
  background: var(--color-surface-2);
}
.mc-rowline.expanded {
  background: var(--color-bg);
}
.mc-metric {
  flex: 0 0 160px;
  width: 160px;
  padding-left: 0;
  text-align: left;
  font-size: 12.5px;
  color: var(--color-text-2);
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 4px;
}
.mc-th {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
}
/* 指标行展开三角：同为素材 右箭头.svg，收起朝右（0deg）、展开朝下（90deg）。
   外层 span 尺寸 13x13 容纳旋转后的 8x13，避免抖动。 */
.mc-caret-row {
  width: 13px;
  height: 13px;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mc-caret-row svg {
  width: 8px;
  height: 13px;
  fill: var(--color-text-5);
  /* transform 用于旋转、fill 用于变色，分开写避免 hover 时把 90deg 旋转覆盖掉 */
  transition: transform 0.15s, fill 0.15s;
}
/* 悬停变红（用户要求）：灰三角太弱，「这里能点」不可见。
   收起与展开态都变—— 展开态的三角是「点此收起」，同样需要点击暗示。 */
.mc-rowline:hover .mc-caret-row svg {
  fill: var(--color-brand);
}
.mc-caret-row.on svg {
  transform: rotate(90deg);
  fill: var(--color-brand);
}
/* 右侧序列：月份值(固定宽) 与箭头列(固定宽) 交替；
   不再用 flex:1 平铺，避免列少时强行平均、间距过大（需求④） */
.mc-seq {
  flex: 1;
  display: flex;
  align-items: stretch;
}
.mc-val {
  flex: 0 0 120px;
  width: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  font-size: 14px;
  color: var(--color-text-2);
  font-variant-numeric: tabular-nums;
}
/* 表头月份列：月份名 + 天数，纵向堆叠并居中（跟随数据列居中；保持标签字号不跟着放大） */
.mc-th.mc-val {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}
.mc-th-sub {
  display: block;
  font-size: 11px;
  color: var(--color-text-4);
  font-weight: 400;
  margin-top: 2px;
}
/* 箭头列：相对定位承载「底部连线(带箭头) + 居中气泡」；
   列宽必须能容纳最长差额文本（分两行后单行更短，如 −46,690.39 ≈ 80px）并留边距，
   否则气泡溢出会压住两侧的数据文字（用户截图反馈：左侧数据被遮、贴太近有干扰） */
.mc-arrow {
  flex: 0 0 132px;
  width: 132px;
  position: relative;
  /* 气泡为「差额 + 变化」两行、12.5px字号，需约 40px 净空 + 箭头区，故行高 68px */
  min-height: 68px;
}
/* 表头里的箭头列是占位空格，不需要撑行高 */
.mc-hrow .mc-arrow {
  min-height: 0;
}
/* 气泡：绝对定位在本列内、数据底部上方，水平居中；
   差额与变化**分两行**显示（不再用「·」挤在一行 —— 用户反馈字太小且一行读不清）；
   不设 nowrap，超长时在列内换行，保证永不溢出压到两侧数据；
   bottom 需给大三角箭头（高 11px）留出纵向空间，不与之重叠 */
.mc-arrow .bubble {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 26px;
  max-width: calc(100% - 12px);
  text-align: center;
  font-size: 12.5px;
  line-height: 1.3;
  font-variant-numeric: tabular-nums;
  z-index: 2;
}
/* 上下两行的分块：block 让两者各占一行，各自居中 */
.mc-arrow .bubble-diff,
.mc-arrow .bubble-delta {
  display: block;
  white-space: nowrap;
}
/* 箭头本体：内联 SVG 容器（实线 / 渐长虚线两种方案由模板切换），
   延长至接近列两端、纵向贴数据底部；颜色统一由 color 控制，SVG 用 currentColor */
.mc-arrow .line {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 12px;
  height: 12px;
  display: block;
  color: #4a9db8; /* 箭头主色（取自用户提供的示意图），改这里即可整体换色 */
}
/* 绿涨红跌（国内习惯）：气泡必须带 .mc-arrow .bubble 前缀，
   否则会被通用文本色覆盖（特异性坑）。值列本身不着色，只有气泡着色。 */
.mc-arrow .bubble.up {
  color: var(--color-up);
}
.mc-arrow .bubble.down {
  color: var(--color-down);
}
.mc-arrow .bubble.flat {
  color: var(--color-text-4);
}
/* 分组标题行（「商品明细表」等）：同样底色铺满、文字缩进 */
.mc-grouprow {
  font-size: 11px;
  color: var(--color-text-3);
  background: var(--color-bg);
  font-weight: 500;
  padding: 5px 18px 5px var(--mc-inset);
  border-bottom: 1px solid var(--color-text-6);
}
/* 展开的时间轴大图：外缩进与表格文字对齐，右侧留18px。
   overflow-x:auto 让「月份很多、图超宽」时可横向滚动，而不是压扁节点。 */
.mc-exp {
  padding: 10px 12px 12px;
  margin: 0 18px 8px var(--mc-inset);
  border: 1px dashed var(--color-text-6);
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
  overflow-x: auto;
}
/* 时间轴 SVG：靠左对齐不居中，宽度由 JS 按月份数给出（timeline.W）。
   max-width:100% 保证不撑破容器；超宽时由 .mc-exp 横向滚动兜底，
   绝不压缩节点间距导致文字重叠。 */
.mc-tl {
  display: block;
  max-width: 100%;
  height: auto;
}
/* 时间轴标题：指标名放大变绿 + 后半段说明保持灰色小字（用户要求）。
   之所以拆成两个 span 而不是整行统一字号：整行一起放大后，
   「· 时间轴（2026-08 ~ 2026-10）」这类辅助信息会喧宾夺主。 */
.mc-exp-title {
  margin-bottom: 4px;
  line-height: 1.5;
}
.mc-exp-metric {
  font-size: 16px;
  font-weight: 600;
  /* 用 --color-up（涨的语义绿）而非 --color-success：后者 #10b981 偏青绿，
     与页面里表示「涨」的绿不是同一个色，同一屏出现两种绿会像两套语义。 */
  color: var(--color-up);
}
.mc-exp-meta {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--color-text-3);
}
.mc-exp-empty {
  font-size: 12px;
  color: var(--color-text-5);
  margin: 0;
}
/* 时间轴 SVG 内文本绿涨红跌（SVG 用 fill 而非 color） */
.mc-exp text.up {
  fill: var(--color-up);
}
.mc-exp text.down {
  fill: var(--color-down);
}
.mc-exp text.flat {
  fill: var(--color-text-4);
}
</style>
