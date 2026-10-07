<script setup lang="ts">
// 合并的日期范围选择器：点开日历后，连续点击「起点 → 终点」即可选中并自动应用。
// 带两个月视图，支持跨月选择；范围外日期禁用；提供「全区间」一键还原。
// 额外提供快捷按钮：当月 / 上月（与数据可用区间取交集）；
// showLastWeek=true 时再追加「近一周」——以数据最新日为终点的 7 天（默认区间，单品分析页用）。
import { computed, ref, nextTick } from "vue";
import { monthBounds, monthBoundsOf, availableMonths, recentDaysRange, type MonthCell } from "../utils/month";
import { claimOverlay, useOverlayLock, acquireOverlay } from "../utils/overlay";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 当前区间起点 "YYYY-MM-DD"；空串表示不限（配合 end 为空 = 全区间） */
  start: string;
  /** 当前区间终点 "YYYY-MM-DD"；空串表示不限 */
  end: string;
  /** 可选：数据可用区间的最早日期，早于它的日期在日历上禁用 */
  min?: string;
  /** 可选：数据可用区间的最晚日期，晚于它的日期在日历上禁用 */
  max?: string;
  /** 是否显示「近一周」快捷按钮（单品分析页用 true） */
  showLastWeek?: boolean;
}>();

/**
 * 组件事件：`change` —— 区间确定后触发（选完终点 / 点快捷按钮 / 清空都会触发）。
 */
const emit = defineEmits<{
  (e: "change", v: { start: string; end: string }): void;
}>();

/**
 * 数字补零（9 -> "09"）。
 *
 * @param {number} n - 待补零的数字。
 * @returns {string} 两位字符串。
 * @example
 * ```ts
 * pad(9); // "09"
 * ```
 */
const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 今天的日期字符串（本地时区，刻意不用 toISOString 以免 UTC 差一天）。
 *
 * @returns {string} 形如 "2026-09-24"。
 * @example
 * ```ts
 * todayStr(); // "2026-09-24"
 * ```
 */
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/**
 * 触发按钮上的显示文案。
 *
 * @returns {string} 有区间时显示 "起 ~ 止"，只有起点时显示起点，都没有时显示 "全区间"。
 * @example
 * ```ts
 * label.value; // "2026-09-18 ~ 2026-09-24"
 * ```
 */
const label = computed(() => {
  if (props.start && props.end) return `${props.start} ~ ${props.end}`;
  if (props.start) return props.start;
  return "全区间";
});

/** 日历面板是否展开 */
const open = ref(false);
/** 面板是否向右展开（true=靠左对齐向右伸展） */
const openRight = ref(true); // 默认向右展开（避开左侧导航栏）
/** 触发按钮元素（用于计算弹层可用空间） */
const triggerRef = ref<HTMLElement | null>(null);
/** 弹层元素（用于测量宽度） */
const popRef = ref<HTMLElement | null>(null);
/** 左月视图年份 */
const viewYear = ref(0);
/** 左月视图月份（0-indexed：0=1月） */
const viewMonth = ref(0);
/** 面板内「正在选择」的临时区间，确定后才 emit 给父组件 */
const pending = ref<{ start: string; end: string }>({ start: "", end: "" });

/** 星期表头（周一起始，符合国内习惯） */
const weekdays = ["一", "二", "三", "四", "五", "六", "日"];

/**
 * 打开日历时默认定位到哪个月：优先当前起点，其次数据最早日，最后今天。
 *
 * @returns {string} "YYYY-MM-DD"。
 * @example
 * ```ts
 * baseDate(); // "2026-09-24"
 * ```
 */
function baseDate(): string {
  return props.start || props.min || todayStr();
}

/**
 * 本实例的浮层唯一标识 + 全局互斥绑定。
 *
 * @remarks
 * 单品分析页「月份对比」模块里并排放着两个 DateRangePicker，它们各自的
 * 面板互不知情；同时展开会互相压盖。这里领一个全局唯一 id，
 * 任一实例打开面板都会把其它实例的面板挤掉（见 utils/overlay.ts）。
 */
const overlayId = claimOverlay("drp");

useOverlayLock(overlayId, () => {
  open.value = false;
  monthOpen.value = false;
});

/**
 * 把视图年月初始化到 baseDate() 所在的月份。
 *
 * @returns {void} 无返回值，副作用是设置 viewYear / viewMonth。
 * @example
 * ```ts
 * initView(); // 视图跳到当前起点所在月
 * ```
 */
function initView() {
  const [y, m] = baseDate().split("-").map(Number);
  viewYear.value = y;
  // 内部月份是 0-indexed，字符串月份是 1-indexed，故减 1
  viewMonth.value = m - 1;
}

/**
 * 打开面板：定位月份、把已选区间载入临时状态、校正展开方向。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * openPanel(); // 点击触发按钮时调用
 * ```
 */
function openPanel() {
  initView();
  // 载入当前已选区间，让用户在此基础上改，而不是每次从空白开始
  pending.value = { start: props.start, end: props.end };
  open.value = true;
  // 登记自己，顺手把同页其它选择器的面板挤掉（月份面板与日历面板共用这一个 id）
  acquireOverlay(overlayId);
  // 与月份面板互斥（见 toggleMonthPanel 的同名注释）
  monthOpen.value = false;
  openRight.value = true; // 先默认向右，positionPop 按可用空间校正
  // nextTick 等弹层真正渲染出来后再测量宽度，否则 offsetWidth 为 0
  nextTick(positionPop);
}

/**
 * 关闭面板（不改变已选区间）。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * closePanel(); // 点遮罩 / Esc / 关闭按钮
 * ```
 */
function closePanel() {
  open.value = false;
}

/**
 * 按可用空间决定弹层向左还是向右展开。
 *
 * @remarks
 * 展开方向：右侧有足够空间就向右（远离左侧导航栏），否则向左
 * （单品分析页的触发按钮靠右，向右会溢出屏幕）。
 *
 * @returns {void} 无返回值，副作用是设置 openRight。
 * @example
 * ```ts
 * // 由 openPanel 在 nextTick 后调用
 * ```
 */
function positionPop() {
  const t = triggerRef.value;
  const p = popRef.value;
  if (!t || !p) return;
  const tr = t.getBoundingClientRect();
  const pw = p.offsetWidth;
  const vw = window.innerWidth;
  const margin = 12;
  // 触发按钮右缘 + 弹层宽 + 边距 不超过视口宽度 -> 向右展开
  openRight.value = tr.right + pw + margin <= vw;
}

/**
 * 生成某个月的日历格子（周一起始的 7 列网格）。
 *
 * @param {number} year - 年份，如 2026。
 * @param {number} month0 - 月份（0-indexed：0=1月）。
 * @returns {Array<string | null>} 日期字符串数组；月初/月末的补位格子为 null，
 *      长度一定是 7 的倍数（保证网格完整对齐周几）。
 * @throws 无。
 * @example
 * ```ts
 * gridFor(2026, 8)[0]; // "2026-09-01" 或 null（取决于 9/1 是周几）
 * ```
 */
function gridFor(year: number, month0: number): (string | null)[] {
  const first = new Date(year, month0, 1);
  // new Date(y, m+1, 0) 的「第 0 天」= 本月最后一天，取 getDate() 即当月天数
  const daysInMonth = new Date(year, month0 + 1, 0).getDate();
  // getDay() 是周日=0；+6 再 %7 转成周一=0，符合国内日历习惯
  const startWeekday = (first.getDay() + 6) % 7;
  const cells: (string | null)[] = [];
  // 月初补空位，让 1 号落在正确的星期列上
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(`${year}-${pad(month0 + 1)}-${pad(d)}`);
  }
  // 末尾补空位到整周，避免最后一行残缺
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

/**
 * 取下一个月的 [年, 月]（用于双月视图的右侧那个月）。
 *
 * @param {number} y - 当前年份。
 * @param {number} m0 - 当前月份（0-indexed）。
 * @returns {[number, number]} [下一年或同年的年份, 下一月]；12 月进位到下一年 1 月。
 * @example
 * ```ts
 * nextYM(2026, 11); // [2027, 0]
 * nextYM(2026, 8);  // [2026, 9]
 * ```
 */
function nextYM(y: number, m0: number): [number, number] {
  return m0 === 11 ? [y + 1, 0] : [y, m0 + 1];
}

const monthLabel1 = computed(() => `${viewYear.value}年${viewMonth.value + 1}月`);
const second = computed(() => nextYM(viewYear.value, viewMonth.value));
const monthLabel2 = computed(() => `${second.value[0]}年${second.value[1] + 1}月`);
const grid1 = computed(() => gridFor(viewYear.value, viewMonth.value));
const grid2 = computed(() => gridFor(second.value[0], second.value[1]));

function prevMonth() {
  if (viewMonth.value === 0) {
    viewMonth.value = 11;
    viewYear.value--;
  } else {
    viewMonth.value--;
  }
}
function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0;
    viewYear.value++;
  } else {
    viewMonth.value++;
  }
}

/**
 * 某日期是否不可选（落在数据可用区间之外）。
 *
 * @param {string} date - 日期 "YYYY-MM-DD"。
 * @returns {boolean} true 表示该日期应禁用。
 * @example
 * ```ts
 * disabled("2020-01-01"); // true（早于数据最早日）
 * ```
 */
function disabled(date: string): boolean {
  // 日期是 YYYY-MM-DD 定长字符串，字典序即时间序，可直接比较
  if (props.min && date < props.min) return true;
  if (props.max && date > props.max) return true;
  return false;
}

/**
 * 是否为当前选择的起点。
 *
 * @param {string} date - 日期 "YYYY-MM-DD"。
 * @returns {boolean} 是起点返回 true。
 * @example
 * ```ts
 * isStart("2026-09-18"); // true / false
 * ```
 */
function isStart(date: string): boolean {
  return pending.value.start === date;
}

/**
 * 是否为当前选择的终点。
 *
 * @param {string} date - 日期 "YYYY-MM-DD"。
 * @returns {boolean} 是终点返回 true。
 * @example
 * ```ts
 * isEnd("2026-09-24"); // true / false
 * ```
 */
function isEnd(date: string): boolean {
  return pending.value.end === date;
}

/**
 * 是否落在起点与终点之间（用于高亮区间中段）。
 *
 * @param {string} date - 日期 "YYYY-MM-DD"。
 * @returns {boolean} 在区间内（不含端点）返回 true；起点或终点未选时返回 false。
 * @example
 * ```ts
 * inRange("2026-09-20"); // 若 09-18 ~ 09-24 已选 -> true
 * ```
 */
function inRange(date: string): boolean {
  const { start, end } = pending.value;
  if (!start || !end) return false;
  return date > start && date < end;
}

/**
 * 点击某个日期：连续两次点击构成「起点 → 终点」，选满即应用。
 *
 * @remarks
 * 三种情况：
 * - 还没选起点，或已选满一对 -> 以本次点击作为**新起点**；
 * - 已选起点、本次点击更早 -> 也当作新起点（否则会出现「终点早于起点」的倒序区间）；
 * - 已选起点、本次更晚 -> 作为终点，凑齐后立即 emit 并关闭面板。
 *
 * @param {string | null} date - 被点击的日期；null（占位格）或禁用日期直接忽略。
 * @returns {void} 无返回值；凑齐区间时 emit("change") 并关闭面板。
 * @example
 * ```ts
 * pick("2026-09-18"); // 设为起点
 * pick("2026-09-24"); // 设为终点 -> emit change 并关闭
 * ```
 */
function pick(date: string | null) {
  // 空格子 / 禁用日期点了没反应
  if (!date || disabled(date)) return;
  const p = { ...pending.value };
  if (!p.start || (p.start && p.end)) {
    // 开始新一轮选择
    p.start = date;
    p.end = "";
  } else if (date < p.start) {
    // 比起点还早 → 当作新的起点
    p.start = date;
    p.end = "";
  } else {
    p.end = date;
  }
  pending.value = p;
  // 起点终点都齐了才算选完，立即应用（父组件会重新拉数据）
  if (p.start && p.end) {
    emit("change", { start: p.start, end: p.end });
    closePanel();
  }
}

/**
 * 清空区间（「全区间」），立即应用并关闭面板。
 *
 * @returns {void} 无返回值；emit 出 start/end 均为空串的区间。
 * @example
 * ```ts
 * clearRange(); // 父组件收到 { start: "", end: "" } -> 展示全部日期
 * ```
 */
function clearRange() {
  emit("change", { start: "", end: "" });
  closePanel();
}

/**
 * 快捷按钮：把区间设为某个月（offset=0 当月、-1 上月），并与数据可用区间取交集。
 *
 * @param {number} offset - 相对当月的偏移：0=当月，-1=上月。
 * @returns {void} 无返回值；emit 出该月区间后关闭面板。
 * @example
 * ```ts
 * applyMonth(0);  // 当月
 * applyMonth(-1); // 上月
 * ```
 */
function applyMonth(offset: number) {
  const { start, end } = monthBounds(offset, props.min, props.max);
  emit("change", { start, end });
  closePanel();
  closeMonthPanel();
}

/* ---------------- 月份选择器（可翻年 + 网格） ---------------- */

/** 月份下拉面板是否展开 */
const monthOpen = ref(false);
/** 月份面板当前显示的年份（翻页用；默认定位到当前选中月所在年） */
const monthPanelYear = ref(0);
/** 月份面板的定位样式（由 positionMonthPop 按月份按钮实际位置算出的像素偏移） */
const monthPopStyle = ref<{ left: string; right: string }>({ left: "0", right: "auto" });
/** 月份面板元素（用于测量宽度决定展开方向） */
const monthPopRef = ref<HTMLElement | null>(null);

/** 由 min/max 反推的有数据月份清单（最新月在前） */
const monthCells = computed<MonthCell[]>(() => availableMonths(props.min, props.max));

/** 数据覆盖的年份区间（含首尾），供翻页按钮判断能否再翻 */
const yearSpan = computed(() => {
  const cells = monthCells.value;
  if (cells.length === 0) return null;
  // cells 已按倒序排列：末位是最早月，首位是最新月
  return { newest: cells[0].year, oldest: cells[cells.length - 1].year };
});

/** 月份面板标题文案（如 "2026 年"） */
const monthPanelTitle = computed(() => `${monthPanelYear.value} 年`);

/**
 * 12 个月格子：1-12月按 3 行 x 4 列排布，无数据的置灰。
 *
 * @remarks
 * 这里才是「无数据月份置灰」真正生效的地方：当用户把面板翻到数据范围之外的年份，
 * 该年 12 个月都不在 [min, max] 内，清单里查不到 -> 兜底成 hasData=false -> 置灰禁用。
 * 翻页按钮已在年份边界禁用，所以正常操作路径下只会看到当前年 + 有数据年份。
 */
const monthGrid = computed<MonthCell[]>(() => {
  const cells = monthCells.value;
  const y = monthPanelYear.value;
  // 把该年 12 个月映射到清单里（可能全年都没数据 -> 全部 hasData=false）
  const byKey = new Map(cells.map((c) => [c.key, c]));
  return Array.from({ length: 12 }, (_, m) => {
    const key = `${y}-${pad(m + 1)}`;
    return byKey.get(key) ?? { year: y, month0: m, key, hasData: false };
  });
});

/**
 * 当前**已生效**区间是否恰好等于某个整月（用于高亮该月 + 按钮文案）。
 *
 * @remarks
 * 刻意读 props.start/end 而不是 pending：pending 是日历面板的临时草稿，
 * 页面刚加载时还没开过面板就是空串，用它会导致按钮一直显示「月份」而非选中月。
 * 按钮反映的是"已生效的区间"，与 pending 无关。
 */
const activeMonthKey = computed(() => {
  const { start, end } = props;
  if (!start || !end) return "";
  // 起止同月且端点分别为 1 号与该月最后一天，才算「选中整月」
  if (start.slice(8) !== "01") return "";
  const m0 = Number(start.slice(5, 7)) - 1;
  const lastDay = new Date(Number(start.slice(0, 4)), m0 + 1, 0).getDate();
  if (end !== `${start.slice(0, 7)}-${pad(lastDay)}`) return "";
  return start.slice(0, 7);
});

/**
 * 月份按钮上显示的文案：命中整月时显示 "2026-09"，否则显示静态 "月份"。
 *
 * @returns {string} 按钮文案。
 * @example
 * ```ts
 * monthBtnLabel.value; // "2026-09"
 * ```
 */
const monthBtnLabel = computed(() => activeMonthKey.value || "月份");

/**
 * 展开/收起月份面板。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * toggleMonthPanel(); // 点「月份 ▾」按钮
 * ```
 */
function toggleMonthPanel() {
  monthOpen.value = !monthOpen.value;
  if (!monthOpen.value) return;
  // 登记自己，顺手把同页其它选择器的面板挤掉（含它们的日历面板）
  acquireOverlay(overlayId);
  // 与日历面板互斥：两个浮层同时打开会互相遮挡、且 Esc/遮罩行为混乱
  open.value = false;
  // 展开时把年份定位到「当前选中月」或「数据最新月」，避免默认停在无关年份
  const anchor = activeMonthKey.value || monthCells.value[0]?.key;
  if (anchor) monthPanelYear.value = Number(anchor.slice(0, 4));
  // nextTick 等面板渲染出来后再量宽度与位置，否则 offsetWidth 为 0、getBoundingClientRect 全是 0
  nextTick(positionMonthPop);
}

/**
 * 关闭月份面板（不改变已选区间）。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * closeMonthPanel(); // 选中月份后关闭
 * ```
 */
function closeMonthPanel() {
  monthOpen.value = false;
}

/**
 * 按「月份按钮」的实际位置对齐月份面板。
 *
 * @remarks
 * 不能像日历面板那样简单用 `left: 0` / `right: 0`：
 * 月份面板挂在 `.drp`（整行工具条，含「统计区间」触发按钮 + 快捷按钮组）下，
 * `left: 0` 对齐的是**工具条左边缘**，而不是月份按钮 —— 视觉上会明显偏左。
 * 因此这里量出月份按钮相对 `.drp` 的像素偏移，把面板精确挪到按钮下方。
 *
 * 默认**右对齐按钮右缘**（面板向左展开）：月份按钮位于工具条最右端，
 * 向左展开天然不会顶出视口右缘，是最常见的形态。
 * 只有当向左展开会让面板左缘跑出视口左缘（按钮贴着屏幕左边）时，才改为左对齐。
 *
 * ⚠️ 这里判断的是「面板**左缘**是否出视口」，**不是**「按钮右缘 + 面板宽是否出视口」——
 *    向左展开时右侧本就不需要空间，用后者判断会把绝大多数正常情况误判成放不下。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * positionMonthPop(); // 由 toggleMonthPanel 在 nextTick 后调用
 * ```
 */
function positionMonthPop() {
  const t = monthTriggerRef.value;
  const p = monthPopRef.value;
  const root = drpRootRef.value;
  if (!t || !p || !root) return;
  const tr = t.getBoundingClientRect();
  const rr = root.getBoundingClientRect();
  const pw = p.offsetWidth;
  const margin = 12;
  // 向左展开时面板左缘的视口坐标：按钮右缘 - 面板宽
  const leftEdgeIfRightAligned = tr.right - pw;
  // 放得下 = 面板左缘不越出视口左缘
  const fitsRightAligned = leftEdgeIfRightAligned >= margin;
  monthPopStyle.value = fitsRightAligned
    // right 是「面板右缘距 .drp 右缘的距离」= .drp右缘 - 按钮右缘
    ? { left: "auto", right: `${Math.max(0, rr.right - tr.right)}px` }
    // left 是「面板左缘距 .drp 左缘的距离」= 按钮左缘 - .drp左缘
    : { left: `${tr.left - rr.left}px`, right: "auto" };
}

/** 月份按钮元素（用于计算面板可用空间） */
const monthTriggerRef = ref<HTMLElement | null>(null);
/** 组件根元素（.drp 整行工具条；月份面板挂在它下面，按对齐需要量它的边界） */
const drpRootRef = ref<HTMLElement | null>(null);

/**
 * 月份面板年份上/下翻页。
 *
 * @param {number} delta - 年份偏移，-1 上一年，+1 下一年。
 * @returns {void} 无返回值；被数据边界挡住时忽略。
 * @example
 * ```ts
 * shiftYear(-1); // 上一年
 * shiftYear(1);  // 下一年
 * ```
 */
function shiftYear(delta: number) {
  const span = yearSpan.value;
  if (!span) return;
  const next = monthPanelYear.value + delta;
  // 翻到数据范围之外会看到一片置灰月份，无意义 -> 挡住
  if (next > span.newest || next < span.oldest) return;
  monthPanelYear.value = next;
}

/**
 * 年份标题旁的「跳到最新/最早数据年」快捷跳转。
 *
 * @param {"newest" | "oldest"} target - 跳转目标。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * jumpToYear("oldest"); // 跳回最早有数据的年份
 * ```
 */
function jumpToYear(target: "newest" | "oldest") {
  const span = yearSpan.value;
  if (!span) return;
  monthPanelYear.value = target === "newest" ? span.newest : span.oldest;
}

/**
 * 选择某个月：按该月整月区间 emit 出去，然后收起月份面板。
 *
 * @remarks
 * 用monthBoundsOf（绝对年月）而非 monthBounds(offset)：
 * 月份面板是按年翻页后精确点选某一月，若把年月换算成「相对系统月的 offset」
 * 再算，跨年时极易偏一个月。
 *
 * @param {MonthCell} cell - 被点击的月份格子。
 * @returns {void} 无返回值；emit 区间后关闭面板。
 * @example
 * ```ts
 * pickMonth({ year: 2026, month0: 7, key: "2026-08", hasData: true });
 * // -> emit { start: "2026-08-01", end: "2026-08-31" }
 * ```
 */
function pickMonth(cell: MonthCell) {
  if (!cell.hasData) return;
  const { start, end } = monthBoundsOf(cell.year, cell.month0, props.min, props.max);
  // 同步落到pending：月份面板与日历面板共用这份临时状态，
  // 否则关掉月份面板再开日历会看到旧区间，与按钮显示不一致
  pending.value = { start, end };
  emit("change", { start, end });
  closeMonthPanel();
}

/**
 * 快捷按钮「近一周」：以数据最新日为终点的 7 天（单品分析页的默认区间）。
 *
 * @remarks
 * max 取不到时退回到当前已选终点，避免点了没反应；
 * 计算出的起点为空时兜底成单日区间，保证 emit 出的永远是一段有效区间。
 *
 * @returns {void} 无返回值；emit 出近一周区间后关闭面板。
 * @example
 * ```ts
 * applyLastWeek(); // 如 max=2026-09-17 -> { start: "2026-09-10", end: "2026-09-17" }
 * ```
 */
function applyLastWeek() {
  // max 取不到时退回到当前已选终点，避免点了没反应
  const end = props.max || props.end;
  if (!end) return;
  const r = recentDaysRange(7, props.min, end);
  if (!r.start || !r.end) r.start = r.end; // 兜底：至少给一个单日区间
  emit("change", r);
  closePanel();
}
</script>

<template>
  <!-- ref=drpRootRef：月份面板按月份按钮对齐时需要量出本容器的边界（见 positionMonthPop） -->
  <div class="drp" data-testid="date-range" ref="drpRootRef">
    <button class="drp-trigger" data-testid="date-range-trigger" type="button" @click="openPanel" ref="triggerRef">
      <span class="drp-icon">📅</span>
      <span :class="{ placeholder: !props.start && !props.end }">{{ label }}</span>
      <span class="caret">▾</span>
    </button>
    <div class="drp-quick" data-testid="date-range-quick">
      <button v-if="showLastWeek" class="qm" type="button" @click="applyLastWeek">近一周</button>
      <button class="qm" type="button" @click="applyMonth(0)">当月</button>
      <button class="qm" type="button" @click="applyMonth(-1)">上月</button>
      <button
        class="qm qm-month"
        :class="{ active: monthOpen || activeMonthKey }"
        type="button"
        ref="monthTriggerRef"
        @click="toggleMonthPanel"
      >
        {{ monthBtnLabel }}<span class="qm-caret">▾</span>
      </button>
    </div>

    <div v-if="monthOpen" class="drp-backdrop" @click="closeMonthPanel"></div>

    <div
      v-if="monthOpen"
      class="drp-pop drp-mpop" data-testid="date-range-month-panel"
      ref="monthPopRef"
      :style="monthPopStyle"
    >
      <div class="drp-head">
        <button
          class="nav"
          type="button"
          :disabled="!yearSpan || monthPanelYear >= yearSpan.newest"
          @click="shiftYear(-1)"
        >
          ‹
        </button>
        <span class="mlabel">{{ monthPanelTitle }}</span>
        <button
          class="nav"
          type="button"
          :disabled="!yearSpan || monthPanelYear <= yearSpan.oldest"
          @click="shiftYear(1)"
        >
          ›
        </button>
      </div>

      <div class="mpop-grid">
        <button
          v-for="c in monthGrid"
          :key="c.key"
          class="mcell"
          :class="{ nodata: !c.hasData, on: c.key === activeMonthKey }"
          type="button"
          :disabled="!c.hasData"
          @click="pickMonth(c)"
        >
          {{ c.month0 + 1 }}月
        </button>
      </div>

      <div class="drp-foot">
        <button
          class="link"
          type="button"
          :disabled="!yearSpan || monthPanelYear <= yearSpan.oldest"
          @click="jumpToYear('oldest')"
        >
          查看 {{ yearSpan?.oldest ?? "—" }} 年 ›
        </button>
        <span class="tip">灰色月份无数据</span>
      </div>
    </div>

    <div v-if="open" class="drp-backdrop" @click="closePanel"></div>

    <div v-if="open" class="drp-pop" data-testid="date-range-calendar" ref="popRef" :style="openRight ? { left: '0', right: 'auto' } : { right: '0', left: 'auto' }">
      <div class="drp-head">
        <button class="nav" type="button" @click="prevMonth">‹</button>
        <span class="mlabel">{{ monthLabel1 }}</span>
        <span class="mlabel">{{ monthLabel2 }}</span>
        <button class="nav" type="button" @click="nextMonth">›</button>
      </div>

      <div class="drp-body">
        <div class="drp-cal" v-for="(g, gi) in [grid1, grid2]" :key="gi">
          <div class="drp-week">
            <span v-for="w in weekdays" :key="w">{{ w }}</span>
          </div>
          <div class="drp-grid">
            <span
              v-for="(d, i) in g"
              :key="i"
              class="cell"
              :class="{
                empty: !d,
                disabled: d && disabled(d),
                start: d && isStart(d),
                end: d && isEnd(d),
                inrange: d && inRange(d),
              }"
              @click="pick(d)"
              >{{ d ? Number(d.slice(8)) : "" }}</span
            >
          </div>
        </div>
      </div>

      <div class="drp-foot">
        <button class="link" type="button" @click="clearRange">全区间</button>
        <span class="tip">点击起点与终点，自动应用</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.drp {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.drp-quick {
  display: inline-flex;
  align-items: center;
  gap: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-pill);
  padding: 3px;
  /* 抬到 .drp-backdrop(40) 之上：否则面板打开时全屏遮罩会盖住按钮组，
     再点「月份 ▾」会落到遮罩上（虽能关闭面板，但点击落点与视觉不符）。 */
  position: relative;
  z-index: 45;
}
.drp-quick .qm {
  border: none;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--color-text-3);
  font-size: 12px;
  padding: 5px 12px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.drp-quick .qm:hover {
  color: var(--color-brand);
  background: var(--color-brand-tint);
}
/* 月份按钮：与其它三个静态文案按钮区分——常态多一个下拉箭头，命中整月时显示年月并高亮 */
.drp-quick .qm-month {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.drp-quick .qm-month .qm-caret {
  font-size: 9px;
  color: var(--color-text-5);
}
.drp-quick .qm-month.active {
  color: var(--color-brand);
  background: var(--color-brand-tint);
  font-weight: 600;
}
.drp-quick .qm-month.active .qm-caret {
  color: var(--color-brand);
}
/* 翻页按钮到数据边界时禁用（禁用态不响应 hover，避免误以为还能翻） */
.drp-head .nav:disabled {
  color: var(--color-text-6);
  border-color: var(--color-bg);
  cursor: not-allowed;
  background: var(--color-surface-2);
}
.drp-head .nav:disabled:hover {
  border-color: var(--color-bg);
  color: var(--color-text-6);
}
.drp-foot .link:disabled {
  color: var(--color-text-6);
  cursor: not-allowed;
}
.drp-foot .link:disabled:hover {
  text-decoration: none;
}
/* 月份面板：比日历面板窄得多，网格 3 行 x 4 列 */
.drp-mpop {
  width: 264px;
}
.mpop-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
}
.mcell {
  height: 34px;
  border: none;
  background: transparent;
  border-radius: var(--radius-md);
  color: var(--color-text-2);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.mcell:hover:not(:disabled) {
  background: var(--color-brand-tint);
  color: var(--color-brand);
}
/* 无数据月份：置灰而非隐藏，让用户知道「这个月存在但没数据」 */
.mcell.nodata {
  color: var(--color-text-6);
  cursor: not-allowed;
}
.mcell.on {
  background: var(--color-brand);
  color: var(--color-surface);
  font-weight: 600;
}
.drp-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  padding: 7px 10px;
  font-size: 13px;
  color: var(--color-text-2);
  background: var(--color-surface);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-variant-numeric: tabular-nums;
}
.drp-trigger:hover {
  border-color: var(--color-brand);
}
.drp-trigger:focus {
  outline: none;
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(225, 37, 27, 0.08);
}
.drp-trigger .placeholder {
  color: var(--color-text-5);
}
.drp-icon {
  font-size: 13px;
}
.caret {
  color: var(--color-text-5);
  font-size: 10px;
}
.drp-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.drp-pop {
  position: absolute;
  top: calc(100% + 8px);
  z-index: 50;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.12);
  padding: 12px;
  width: max-content;
  max-width: calc(100vw - 24px);
}
.drp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 0 4px;
}
.drp-head .nav {
  width: 26px;
  height: 26px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-3);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
}
.drp-head .nav:hover {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.drp-head .mlabel {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-strong);
  flex: 1;
  text-align: center;
}
.drp-body {
  display: flex;
  gap: 18px;
}
.drp-week,
.drp-grid {
  display: grid;
  grid-template-columns: repeat(7, 30px);
  gap: 2px;
}
.drp-week {
  margin-bottom: 4px;
}
.drp-week span {
  text-align: center;
  font-size: 11px;
  color: var(--color-text-5);
  padding: 2px 0;
}
.cell {
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--color-text-2);
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
  transition: background 0.12s, color 0.12s;
}
.cell:hover:not(.empty):not(.disabled) {
  background: var(--color-brand-tint);
  color: var(--color-brand);
}
.cell.empty {
  cursor: default;
}
.cell.disabled {
  color: var(--color-text-6);
  cursor: not-allowed;
  text-decoration: line-through;
}
.cell.inrange {
  background: var(--color-brand-tint);
  border-radius: 0;
}
.cell.start,
.cell.end {
  background: var(--color-brand);
  color: var(--color-surface);
  font-weight: 600;
}
.cell.start {
  border-radius: 6px 0 0 6px;
}
.cell.end {
  border-radius: 0 6px 6px 0;
}
.cell.start.end {
  border-radius: var(--radius-md);
}
.drp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding: 0 4px;
}
.drp-foot .link {
  background: none;
  border: none;
  color: var(--color-brand);
  font-size: 12px;
  cursor: pointer;
  padding: 4px 6px;
}
.drp-foot .link:hover {
  text-decoration: underline;
}
.drp-foot .tip {
  font-size: 11px;
  color: var(--color-text-5);
}
@media (max-width: 640px) {
  .drp-body {
    flex-direction: column;
    gap: 10px;
  }
}
</style>
