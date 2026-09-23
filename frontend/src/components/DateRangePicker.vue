<script setup lang="ts">
// 合并的日期范围选择器：点开日历后，连续点击「起点 → 终点」即可选中并自动应用。
// 带两个月视图，支持跨月选择；范围外日期禁用；提供「全区间」一键还原。
// 额外提供快捷按钮：当月 / 上月（与数据可用区间取交集）；
// showLastWeek=true 时再追加「近一周」——以数据最新日为终点的 7 天（默认区间，单品分析页用）。
import { computed, ref, nextTick } from "vue";
import { monthBounds, recentDaysRange } from "../utils/month";

const props = defineProps<{
  start: string;
  end: string;
  min?: string;
  max?: string;
  showLastWeek?: boolean; // 是否显示「近一周」快捷按钮
}>();
const emit = defineEmits<{
  (e: "change", v: { start: string; end: string }): void;
}>();

const pad = (n: number) => String(n).padStart(2, "0");
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const label = computed(() => {
  if (props.start && props.end) return `${props.start} ~ ${props.end}`;
  if (props.start) return props.start;
  return "全区间";
});

const open = ref(false);
const openRight = ref(true); // 默认向右展开（避开左侧导航栏）
const triggerRef = ref<HTMLElement | null>(null);
const popRef = ref<HTMLElement | null>(null);
const viewYear = ref(0);
const viewMonth = ref(0); // 0-indexed
const pending = ref<{ start: string; end: string }>({ start: "", end: "" });

const weekdays = ["一", "二", "三", "四", "五", "六", "日"];

function baseDate(): string {
  return props.start || props.min || todayStr();
}
function initView() {
  const [y, m] = baseDate().split("-").map(Number);
  viewYear.value = y;
  viewMonth.value = m - 1;
}
function openPanel() {
  initView();
  pending.value = { start: props.start, end: props.end };
  open.value = true;
  openRight.value = true; // 先默认向右，positionPop 按可用空间校正
  nextTick(positionPop);
}
function closePanel() {
  open.value = false;
}
// 展开方向：右侧有足够空间就向右（远离左侧导航栏），否则向左（单品分析页触发按钮在右侧时用）
function positionPop() {
  const t = triggerRef.value;
  const p = popRef.value;
  if (!t || !p) return;
  const tr = t.getBoundingClientRect();
  const pw = p.offsetWidth;
  const vw = window.innerWidth;
  const margin = 12;
  openRight.value = tr.right + pw + margin <= vw;
}

function gridFor(year: number, month0: number): (string | null)[] {
  const first = new Date(year, month0, 1);
  const daysInMonth = new Date(year, month0 + 1, 0).getDate();
  const startWeekday = (first.getDay() + 6) % 7; // 周一=0
  const cells: (string | null)[] = [];
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(`${year}-${pad(month0 + 1)}-${pad(d)}`);
  }
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}
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

function disabled(date: string): boolean {
  if (props.min && date < props.min) return true;
  if (props.max && date > props.max) return true;
  return false;
}
function isStart(date: string): boolean {
  return pending.value.start === date;
}
function isEnd(date: string): boolean {
  return pending.value.end === date;
}
function inRange(date: string): boolean {
  const { start, end } = pending.value;
  if (!start || !end) return false;
  return date > start && date < end;
}

function pick(date: string | null) {
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
  if (p.start && p.end) {
    emit("change", { start: p.start, end: p.end });
    closePanel();
  }
}

function clearRange() {
  emit("change", { start: "", end: "" });
  closePanel();
}

// 当月(offset=0) / 上月(offset=-1)：把范围设为对应月份，并与数据可用区间取交集
function applyMonth(offset: number) {
  const { start, end } = monthBounds(offset, props.min, props.max);
  emit("change", { start, end });
  closePanel();
}

// 近一周：以数据最新日（max）为终点的 7 天，数据不足则被 min 截断
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
  <div class="drp">
    <button class="drp-trigger" type="button" @click="openPanel" ref="triggerRef">
      <span class="drp-icon">📅</span>
      <span :class="{ placeholder: !props.start && !props.end }">{{ label }}</span>
      <span class="caret">▾</span>
    </button>
    <div class="drp-quick">
      <button v-if="showLastWeek" class="qm" type="button" @click="applyLastWeek">近一周</button>
      <button class="qm" type="button" @click="applyMonth(0)">当月</button>
      <button class="qm" type="button" @click="applyMonth(-1)">上月</button>
    </div>

    <div v-if="open" class="drp-backdrop" @click="closePanel"></div>

    <div v-if="open" class="drp-pop" ref="popRef" :style="openRight ? { left: '0', right: 'auto' } : { right: '0', left: 'auto' }">
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
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  padding: 3px;
}
.drp-quick .qm {
  border: none;
  border-radius: 999px;
  background: transparent;
  color: #475569;
  font-size: 12px;
  padding: 5px 12px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.drp-quick .qm:hover {
  color: #e1251b;
  background: #fef2f2;
}
.drp-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 7px 10px;
  font-size: 13px;
  color: #334155;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-variant-numeric: tabular-nums;
}
.drp-trigger:hover {
  border-color: #e1251b;
}
.drp-trigger:focus {
  outline: none;
  border-color: #e1251b;
  box-shadow: 0 0 0 3px rgba(225, 37, 27, 0.08);
}
.drp-trigger .placeholder {
  color: #94a3b8;
}
.drp-icon {
  font-size: 13px;
}
.caret {
  color: #94a3b8;
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
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
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
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #fff;
  color: #475569;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
}
.drp-head .nav:hover {
  border-color: #e1251b;
  color: #e1251b;
}
.drp-head .mlabel {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
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
  color: #94a3b8;
  padding: 2px 0;
}
.cell {
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #334155;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  transition: background 0.12s, color 0.12s;
}
.cell:hover:not(.empty):not(.disabled) {
  background: #fef2f2;
  color: #e1251b;
}
.cell.empty {
  cursor: default;
}
.cell.disabled {
  color: #cbd5e1;
  cursor: not-allowed;
  text-decoration: line-through;
}
.cell.inrange {
  background: #fef2f2;
  border-radius: 0;
}
.cell.start,
.cell.end {
  background: #e1251b;
  color: #fff;
  font-weight: 600;
}
.cell.start {
  border-radius: 6px 0 0 6px;
}
.cell.end {
  border-radius: 0 6px 6px 0;
}
.cell.start.end {
  border-radius: 6px;
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
  color: #e1251b;
  font-size: 12px;
  cursor: pointer;
  padding: 4px 6px;
}
.drp-foot .link:hover {
  text-decoration: underline;
}
.drp-foot .tip {
  font-size: 11px;
  color: #94a3b8;
}
@media (max-width: 640px) {
  .drp-body {
    flex-direction: column;
    gap: 10px;
  }
}
</style>
