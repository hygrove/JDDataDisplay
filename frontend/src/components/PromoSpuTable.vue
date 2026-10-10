<script setup lang="ts">
// SPU 决策表：逐品呈现「当前分配 vs 建议分配」，支撑运营一个个执行。
//
// ⚠️ 判定全在 `modules/promoTable.ts`（排序 / 过滤 / 差额语义 / 文案格式），
//    本组件只负责渲染 —— 与 metrics.ts 同一纪律：判定集中一处，别处只画。
//    改动排序阈值或配色语义时改那个模块，⛔ 不要在这里重新判断一遍。
//
// ⚠️ 配色纪律：差额用**中性蓝/灰**表达增减，⛔ 不是涨红跌绿。
//    这里的增减是「多花钱 / 少花钱」，不是价格涨跌——
//    用红绿会被读成「这个品在亏钱 / 赚钱」，而数据源根本没有毛利字段，算不出来。
//
// ⚠️ 术语纪律（见 CONTEXT.md）：一律「投产比」，⛔ 不出现「边际ROI」/「亏损」/「预测」。
//
// 滚动结构沿用 GenericTable.vue 的既有方案：**单滚动容器 + position:sticky**
//（表头钉顶、建议列钉右），⛔ 不用「绝对定位叠加层 + transform 同步」那套早期写法
//（固定列会脱离容器遮挡整页）。此处行数有限（真实 3 店共 10 行），
// 因此不接虚拟滚动 —— 虚拟滚动是为上千行数据准备的，10 行用它只是徒增复杂度。
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { fmtBy } from "../modules";
import {
  DEFAULT_SORT,
  DELTA_EPS,
  QUAD_META,
  QUADRANT_KEYS,
  deltaTone,
  filterByQuadrant,
  formatDelta,
  formatRoi,
  quadMeta,
  quadrantCounts,
  sortSpus,
} from "../modules/promoTable";
import type { PromoAnalysis, PromoSpu } from "../types";

const props = defineProps<{ analysis: PromoAnalysis }>();
const router = useRouter();

/** 推广数据所在模块（点击行跳单品分析页时拼路由用）。 */
const PROMO_MODULE = "pop_spu_detail";

// ---------- 汇总（与工单 05 保持同一份统计口径） ----------
const total = computed(() => props.analysis.spus.length);
const skipped = computed(() => props.analysis.spus.filter((s) => s.skipped));
const addCount = computed(() => props.analysis.spus.filter((s) => s.delta > DELTA_EPS).length);
const cutCount = computed(() => props.analysis.spus.filter((s) => s.delta < -DELTA_EPS).length);
const unstableCount = computed(() => props.analysis.spus.filter((s) => s.unstable).length);

// ---------- 排序 ----------
const sort = ref<{ key: typeof DEFAULT_SORT.key; dir: typeof DEFAULT_SORT.dir }>({
  ...DEFAULT_SORT,
});

/**
 * 表头点击排序：三态循环「降序 → 升序」。
 *
 * ⚠️ 与 GenericTable 的「未选中 → 降 → 升 → 降」不同：这里永远有默认排序，
 *    首点就是反向（升序），符合「表头已有箭头时点它=翻转」的直觉。
 *
 * @param {typeof DEFAULT_SORT.key} key - 被点击的列键。
 * @returns {void} 无返回值；直接改写sort ref 触发重算。
 * @example
 * ```ts
 * toggleSort("roi"); // 首次点击 → roi 升序
 * ```
 */
function toggleSort(key: typeof DEFAULT_SORT.key) {
  sort.value =
    sort.value.key === key
      ? { key, dir: sort.value.dir === "desc" ? "asc" : "desc" }
      : { key, dir: "desc" };
}

/** 某列的排序箭头：当前列显示方向，其余显示「可排」提示。 */
function arrowOf(key: typeof DEFAULT_SORT.key): string {
  if (sort.value.key !== key) return "⇅";
  return sort.value.dir === "desc" ? "↓" : "↑";
}

// ---------- 分档过滤（chip 多选） ----------
/** 选中的档位键集合；空集 = 不筛选。 */
const picked = ref<Set<string>>(new Set());

/**
 * 切换某个档位的选中状态。
 *
 * @param {string} key - 档位键。
 * @returns {void} 无返回值；改写 picked 触发重算。
 * @example
 * ```ts
 * toggleQuad("core"); // 再点一次取消（回到「不筛选」）
 * ```
 */
function toggleQuad(key: string) {
  const next = new Set(picked.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  picked.value = next;
}

/** 全部档位的计数（据此把没有数据的 chip 置灰）。 */
const counts = computed(() => quadrantCounts(props.analysis.spus));

// ---------- 过滤 → 排序 ----------
const rows = computed(() =>
  sortSpus(
    filterByQuadrant(props.analysis.spus, picked.value),
    sort.value.key,
    sort.value.dir,
  ),
);

/** 过滤后为空、但全量并不为空 → 说明是 chip 把行筛没了，给一句可操作的提示。 */
const filteredEmpty = computed(() => rows.value.length === 0 && total.value > 0);

/**
 * 空态文案：说清「是筛选滤掉的、且怎么恢复」。
 *
 * ⚠️⛔ 不要只写「没有数据」—— 本店明明有 N 个 SPU，
 *    用户看到这句会以为数据丢了。全量本身为空是另一回事（后端empty_reason 管）。
 *
 * @returns {string} 形如「本店没有「低效吞金」档的 SPU —— 取消勾选可看到全部 7 个。」
 */
const emptyText = computed(() => {
  const names = [...picked.value]
    .map((k) => QUAD_META[k as keyof typeof QUAD_META]?.label ?? k)
    .join("、");
  return `本店没有${names ? `「${names}」档的` : ""}SPU —— 取消勾选档位可看到全部 ${total.value} 个。`;
});

/**
 * 换店铺时清空筛选并回到默认排序。
 *
 * ⚠️ 实测踩过：不重置会出「凭空空表」——
 *   在钻芯旗舰店（核心高效 4 个）勾了「核心高效」，再切到钻芯官方旗舰店
 *   （该档 0 个）→ 表格整个空了，而用户刚做的操作与新店铺毫无关系，
 *   看起来像数据丢了。排序同理：按着A 店的投产比排，切到 B 店仍是那个序。
 *
 * watch 的是 `shop` 而不是整个 analysis：后者每次刷新都是新对象，
 * 会把用户在同一家店内点好的筛选也一并清掉。
 */
watch(
  () => props.analysis.shop,
  () => {
    picked.value = new Set();
    sort.value = { ...DEFAULT_SORT };
  },
);

// ---------- 缩略图 ----------
/**
 * 生成缩略图访问路径（复用 ModuleView / SpuAnalysisView 的既有约定）。
 *
 * ⚠️ 与views 里同名函数的实现一致：原图 `/images/100123.png` → 缩略图
 *    `/thumbs/96/100123.avif`。后端批处理会按尺寸预生成 avif/webp。
 *
 * @param {string | null | undefined} orig - 后端给的原图路径。
 * @param {number} size - 缩略图尺寸（后端产出 96 / 120 / 180）。
 * @param {"avif" | "webp"} fmt - 缩略图格式。
 * @returns {string} 缩略图路径；无原图时返回空串（渲染占位块而非破图）。
 * @example
 * ```ts
 * thumb("/images/100123.png", 96); // "/thumbs/96/100123.avif"
 * thumb(null, 96);                 // ""
 * ```
 */
function thumb(orig: string | null | undefined, size: number, fmt: "avif" | "webp" = "avif"): string {
  if (!orig) return "";
  const stem = orig.replace("/images/", "").replace(/\.[^.]+$/, "");
  return `/thumbs/${size}/${stem}.${fmt}`;
}

/** 图片加载失败时退回占位块（⛔ 不留浏览器默认的破图图标）。 */
function onImgError(ev: Event) {
  const el = ev.target as HTMLElement;
  el.style.display = "none";
}

// ---------- 行内跳转 ----------
/**
 * 点击商品单元格 → 跳该 SPU 的单品分析页（与散点图点击气泡同一入口）。
 *
 * @param {PromoSpu} s - 被点击的 SPU。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * goSpu(s); // 路由跳到 /module/pop_spu_detail/spu/100273219974
 * ```
 */
function goSpu(s: PromoSpu) {
  void router.push({ name: "spu-analysis", params: { moduleId: PROMO_MODULE, spu: s.spu } });
}
</script>

<template>
  <div class="table-box" data-testid="promo-table-box">
    <h3>
      <span>SPU 分配方案</span>
      <span class="hint">当前分配 vs 建议分配 · 点表头排序 · 点商品列看单品分析</span>
    </h3>

    <!-- 汇总数字：不是占位符，是真实统计，用户能立刻判断方案的影响面 -->
    <div class="sum-row" data-testid="promo-table-summary">
      <div class="sum-item">
        <span class="si-label">参与分配</span>
        <span class="si-value">{{ total - skipped.length }} 个</span>
      </div>
      <div class="sum-item">
        <span class="si-label">建议加投</span>
        <span class="si-value add">{{ addCount }} 个</span>
      </div>
      <div class="sum-item">
        <span class="si-label">建议减投</span>
        <span class="si-value cut">{{ cutCount }} 个</span>
      </div>
      <div class="sum-item">
        <span class="si-label">投产比波动大</span>
        <span class="si-value">{{ unstableCount }} 个</span>
      </div>
      <div v-if="skipped.length" class="sum-item">
        <span class="si-label">未参与分配</span>
        <span class="si-value muted">{{ skipped.length }} 个</span>
      </div>
    </div>

    <!-- 分档过滤：多选 chip。⛔ 不做单选下拉——运营的问题通常是
         「低效吞金那几个先看看」，同时看两档是常态。空选 = 不过滤。 -->
    <div class="chips" data-testid="promo-table-filters">
      <button
        v-for="k in QUADRANT_KEYS"
        :key="k"
        class="chip"
        :class="['c-' + k, { on: picked.has(k), empty: counts[k] === 0 }]"
        :title="QUAD_META[k].hint"
        :data-testid="'promo-chip-' + k"
        @click="toggleQuad(k)"
      >
        <span class="dot"></span>{{ QUAD_META[k].label }}
        <span class="cnum">{{ counts[k] }}</span>
      </button>
      <span class="chip-hint">
        {{ picked.size ? "已选 " + picked.size + " 档" : "未筛选（点选档位可过滤）" }}
      </span>
    </div>

    <!-- 表格：单滚动容器，横向纵向都在此滚；表头 sticky top、建议列 sticky right -->
    <div class="tw" data-testid="promo-table-scroll">
      <div class="trow trow-head">
        <div class="td c-spu">商品</div>
        <div
          v-for="c in [
            { key: 'current_cost', title: '当前花费', cls: 'c-cur' },
            { key: 'suggested_cost', title: '建议花费', cls: 'c-sug' },
            { key: 'delta', title: '差额', cls: 'c-delta' },
            { key: 'roi', title: '投产比', cls: 'c-roi' },
          ]"
          :key="c.key"
          class="td num sortable"
          :class="[c.cls, { active: sort.key === c.key }]"
          :data-testid="'promo-th-' + c.key"
          @click="toggleSort(c.key as typeof DEFAULT_SORT.key)"
        >
          {{ c.title }}<span class="arrow">{{ arrowOf(c.key as typeof DEFAULT_SORT.key) }}</span>
        </div>
        <div class="td c-quad">分档</div>
        <div class="td c-flag">稳定性</div>
        <div class="td c-advice">建议</div>
      </div>

      <div v-for="s in rows" :key="s.spu" class="trow" data-testid="promo-table-row">
        <div class="td c-spu" @click="goSpu(s)">
          <span class="thumb-box">
            <img
              v-if="thumb(s.image, 96)"
              class="thumb"
              :src="thumb(s.image, 96)"
              :alt="s.spu_name || s.spu"
              loading="lazy"
              @error="onImgError"
            />
          </span>
          <span class="spu-txt">
            <span class="spu-name">{{ s.spu_name || s.spu }}</span>
            <span class="spu-id">{{ s.spu }}</span>
          </span>
        </div>
        <div class="td num c-cur">{{ fmtBy("currency", s.current_cost) }}</div>
        <div class="td num c-sug">{{ fmtBy("currency", s.suggested_cost) }}</div>
        <!-- 差额：中性蓝=加投 / 中性灰=减投 / 弱化=不变 -->
        <div class="td num tone c-delta" :class="'t-' + deltaTone(s.delta)" data-testid="promo-td-delta">
          {{ formatDelta(s.delta) }}
        </div>
        <!-- ⛔ 用 formatRoi 而非 fmtBy("decimal")：后者对 null 返回 "0.00"，
             会把「投产比算不出来」显示成「投产比 0」 -->
        <div class="td num c-roi">{{ formatRoi(s.roi) }}</div>
        <div class="td c-quad">
          <span class="qtag" :class="'q-' + (s.quadrant ?? 'none')" :title="quadMeta(s.quadrant).hint">
            {{ quadMeta(s.quadrant).label }}
          </span>
        </div>
        <div class="td c-flag">
          <span v-if="s.unstable" class="flag" title="逐日投产比波动较大，建议小步试投并密切观察">
            ⚠ 波动大
          </span>
          <span v-else class="flag-none">—</span>
        </div>
        <div class="td c-advice">{{ s.advice }}</div>
      </div>

      <!-- 空态要分两种说：筛出来的空 ≠ 本来就空 -->
      <div v-if="filteredEmpty" class="tfoot" data-testid="promo-table-filtered-empty">
        {{ emptyText }}
      </div>
      <div v-else-if="rows.length" class="tfoot" data-testid="promo-table-foot">
        共 {{ rows.length }} 个 SPU{{ picked.size ? "（已筛选，全量 " + total + "）" : "" }} ·
        差额按绝对值排序，投产比不可计算的品恒排最后
      </div>
    </div>
  </div>
</template>

<style scoped>
.table-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: 14px 18px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
.table-box h3 {
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
/* ---------- 汇总数字条 ---------- */
.sum-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 12px 0 4px;
}
.sum-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  font-size: 12px;
}
.si-label {
  color: var(--color-text-4);
}
.si-value {
  font-weight: 700;
  font-size: 14px;
  color: var(--color-text-strong);
  font-variant-numeric: tabular-nums;
}
/* 与表格差额同一套语义色（add=中性蓝 / cut=中性灰），⛔ 不是涨红跌绿 */
.si-value.add {
  color: var(--color-link);
}
.si-value.cut {
  color: var(--color-text-3);
}
.si-value.muted {
  color: var(--color-text-4);
}
/* ---------- 分档过滤 chip ---------- */
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 7px;
  margin: 10px 0 8px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--color-text-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.chip:hover {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.chip.on {
  background: var(--color-brand-tint-3);
  border-color: var(--color-brand);
  color: var(--color-brand-darker);
  font-weight: 600;
}
/* 无数据的档：置灰但**保留可见** —— 藏起来用户就不知道这家店的分布长什么样。
   ⚠️ 置灰 ≠ disabled：早期版本给计数 0 的档加了 disabled 属性，
   结果浏览器直接吞掉 click，用户点它毫无反应、也无任何提示，
   看起来像「过滤功能坏了」。现在仍可点，点了会走filteredEmpty 空态分支
   （表里明说「该档在本店没有 SPU」），反馈是诚实的。 */
.chip.empty {
  opacity: 0.5;
}
.chip.empty .cnum {
  color: var(--color-text-6);
}
.chip .dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-circle);
  flex: none;
}
.c-core .dot {
  background: var(--color-brand);
}
.c-potential .dot {
  background: var(--color-link);
}
.c-loser .dot {
  background: var(--color-text-5);
}
.c-trial .dot {
  background: var(--color-amber);
}
.c-none .dot {
  background: var(--color-text-6);
}
.cnum {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-5);
}
.chip.on .cnum {
  color: var(--color-brand-darker);
}
.chip-hint {
  font-size: 11px;
  color: var(--color-text-4);
  margin-left: 2px;
}
/* ---------- 表格：单滚动容器 + sticky ---------- */
/* 横向纵向都在此滚动，表头 sticky-top 与建议列 sticky-right 共享同一上下文，
   横向滚动时始终逐列对齐，固定列也绝不会脱离容器去遮挡整页 */
.tw {
  position: relative;
  overflow: auto;
  max-height: 520px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}
.trow {
  display: flex;
  align-items: stretch;
  /* ⛔ 本值必须等于各列宽之和（250+104+104+112+84+96+88+322 = 1160）。
     不一致时窄屏下最后几列会被挤出容器、横向滚不到。 */
  min-width: 1160px;
  border-bottom: 1px solid var(--color-border-4);
}
.trow:last-child {
  border-bottom: none;
}
.trow:not(.trow-head):hover {
  background: var(--color-surface-2);
}
.trow-head {
  position: sticky;
  top: 0;
  z-index: 4;
  background: var(--color-surface-2);
  border-bottom: 1px solid var(--color-border);
}
.td {
  box-sizing: border-box;
  padding: 9px 10px;
  font-size: 13px;
  color: var(--color-text);
  display: flex;
  align-items: center;
  flex: none;
  /* ⛔ 缺这一行会让 .c-advice 的长文案把定宽列撑开：
     flex item 的 min-width 默认是 auto（= min-content），建议列那句
     「投产比高于本店中位且花费规模较大。建议增加推广预算约 2,440 元。」
     撑出的 min-content > 322px，实测整表从 1160 被顶到 1181 —— 于是
     「min-width = 各列宽之和」这个不变量失效，横向滚动量算不准。 */
  min-width: 0;
}
.td.num {
  justify-content: flex-end;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
/* 列宽：定宽保证表头与数据逐列对齐。
   ⚠️ 刻意**不用 :nth-child** 选列 —— 那依赖「每行列数完全相同」这个前提，
   一旦表头多一行筛选或数据行少一列，列宽就整体错位且极难发现。
   显式类名让列宽跟着列自己走，加删列不影响其他列。 */
.c-spu {
  width: 250px;
  gap: 8px;
}
.c-cur {
  width: 104px;
}
.c-sug {
  width: 104px;
}
.c-delta {
  width: 112px;
}
.c-roi {
  width: 84px;
}
.c-quad {
  width: 96px;
}
.c-flag {
  width: 88px;
}
/* 建议列：钉在右缘。窄屏横向滚动时，运营最需要一直可见的就是「该怎么做」 */
.c-advice {
  width: 322px;
  position: sticky;
  right: 0;
  z-index: 2;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border-4);
  box-shadow: -6px 0 10px rgba(15, 23, 42, 0.06);
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--color-text-2);
  align-items: flex-start;
  padding-top: 11px;
}
.trow-head .c-advice {
  z-index: 5;
  background: var(--color-surface-2);
  border-left: 1px solid var(--color-border-2);
  align-items: center;
  padding-top: 9px;
  font-weight: 600;
  color: var(--color-text-3);
}
.trow:not(.trow-head):hover .c-advice {
  background: var(--color-surface-2);
}
/* 列间竖线 */
.trow-head .td:not(:last-child) {
  border-right: 1px solid var(--color-border-2);
}
.td:not(.c-advice):not(:last-child) {
  border-right: 1px solid var(--color-border-4);
}
/* 表头排序态 */
.td.sortable {
  cursor: pointer;
  user-select: none;
  justify-content: flex-end;
  font-weight: 600;
  color: var(--color-text-3);
}
.td.sortable:hover,
.td.sortable.active {
  color: var(--color-brand);
}
.arrow {
  font-size: 11px;
  margin-left: 3px;
}
/* ---------- 商品单元格 ---------- */
.thumb-box {
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: var(--radius-md);
  background: var(--color-surface-3);
  border: 1px solid var(--color-border-4);
  overflow: hidden;
}
.thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.spu-txt {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.spu-name {
  font-size: 13px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 名字为空的品退而显示 SPU 号，不留空白格 */
.spu-id {
  font-size: 11px;
  color: var(--color-text-5);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* ---------- 差额三态：中性蓝/灰，⛔ 不是涨红跌绿 ---------- */
.tone.t-add {
  color: var(--color-link);
  font-weight: 600;
}
.tone.t-cut {
  color: var(--color-text-3);
}
.tone.t-flat {
  color: var(--color-text-5);
}
/* ---------- 分档标签 ---------- */
.qtag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11.5px;
  border-radius: var(--radius-pill);
  border: 1px solid;
  white-space: nowrap;
}
.qtag.q-core {
  color: var(--color-brand-darker);
  background: var(--color-brand-tint-3);
  border-color: var(--color-brand-border-2);
}
.qtag.q-potential {
  color: var(--color-link);
  background: var(--color-surface-2);
  border-color: var(--color-link-2);
}
.qtag.q-loser {
  color: var(--color-text-3);
  background: var(--color-surface-2);
  border-color: var(--color-border-3);
}
.qtag.q-trial {
  color: var(--color-amber);
  background: var(--color-surface-2);
  border-color: var(--color-amber);
}
.qtag.q-none {
  color: var(--color-text-5);
  background: var(--color-surface-3);
  border-color: var(--color-border-3);
}
/* ---------- 稳定性 ---------- */
.flag {
  display: inline-block;
  padding: 2px 7px;
  font-size: 11px;
  color: var(--color-amber);
  background: var(--color-surface-2);
  border: 1px solid var(--color-amber);
  border-radius: var(--radius-pill);
  white-space: nowrap;
}
.flag-none {
  color: var(--color-text-6);
}
/* ---------- 表尾 ---------- */
.tfoot {
  padding: 11px 12px;
  text-align: center;
  font-size: 11.5px;
  color: var(--color-text-4);
  border-top: 1px solid var(--color-border-4);
}
/* 尊重系统「减少动效」偏好（工单 08）
   ⚠️ 这条必须写在**子组件自己**的 scoped 样式里，不能依赖父组件：
   Vue scoped 会给元素加上数据-v-属性选择器，父组件样式表里的 `.chip`
   选择器**选中不到**本组件的元素（实测：CDP 读到 `.chip` 在 reduce 下 transition 仍为 0.15s，
   而同一屏的 `.rate-fill` 已成功关掉）。这类错在 build 时零报错，只能靠浏览器实测发现。 */
@media (prefers-reduced-motion: reduce) {
  .chip {
    transition: none;
  }
}
</style>