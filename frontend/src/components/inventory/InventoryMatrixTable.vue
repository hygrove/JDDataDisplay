<script setup lang="ts">
// 库存矩阵表（紧凑版）：左侧 3 个冻结列（产品图 / 信息 / SKU）+ 每个指标一列。
// 设计要点（对照 2026-10 需求截图）：
// - 每城指标不再展开成 8 个城市子列，而是收进一列：单元格内竖排「城市: 值」，
//   可累加指标在末行追加「【合计】: X」——合计 = 该行 8 城自身求和（不是全国汇总）；
// - 全国指标仍是单列单值；合计行（tfoot）只保留全国指标的合计。
// - 第一列产品图按 **SKU** 取图：后端建索引后随行下发 sku_pic（"/inv-pics/{业务线}/{SKU}.jpg"），
//   展示走 1:1 缩略图 /thumbs/inv/{业务线}/120/{SKU}.{avif|webp}（商品图本身是正方形，
//   展示框也是 1:1 的 120×120，缩略图按 1:1 呈现，像素对像素不缩放），
//   逐级降级 avif → webp → 原图 → 「暂无图片」占位；点击图片可看大图。
//   ⚠️ 与 POP 单品明细模块的分工：POP 那套按 **SPU** 取图（/thumbs/{96|120|180}/{spu}.*），
//   两套链路彼此独立，本表只用 SKU 这套。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type {
  InventoryFilters,
  InventoryFilterOptions,
  InventoryMetric,
  InvCell,
  InventorySlowCity,
  InvRow,
} from "../../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 可见的「非身份」指标（per_city + national，已按展示顺序）；身份列由本组件内置 */
  visibleMetrics: InventoryMetric[];
  /** 城市白名单（按展示顺序） */
  cities: string[];
  /** 当页行（全列 dict；全国滞销行附带 slow_cities 全城市明细） */
  rows: InvRow[];
  /** 指标 key → 合计值（per_city 为 8 城求和单一值） */
  totals: Record<string, number | null>;
  /** 指标 key → 每城求和数组（下标对应 cities） */
  cityTotals: Record<string, number[]>;
  /** 全城市滞销聚合（供「全国滞销件数」列头悬浮显示城市拆分） */
  slowCities?: InventorySlowCity[];
  /** 信息列筛选条件（三级类目 / 销售员 / 上下柜状态；空数组 = 不限） */
  filters: InventoryFilters;
  /** 信息列筛选项候选（父级从结构分布聚合而来） */
  filterOptions: InventoryFilterOptions;
  /** 当前排序指标 key（父级持有并随分页请求下发；null = 未排序） */
  sortKey?: string | null;
  /** 当前排序方向（null = 未排序） */
  sortOrder?: "asc" | "desc" | null;
  /** 城市码 → 阈值（城市级预警，所有 SKU 统一生效；未加载预警时不着色） */
  thresholds?: Record<string, number>;
  /** 城市码数组（与 cities 同序；现货库存列着色按 code 查阈值与在途） */
  cityCodes?: string[];
}>();

/** 筛选条件变化（信息列筛选面板点「确定」时抛出）。 */
const emit = defineEmits<{
  (e: "update:filters", v: InventoryFilters): void;
  /** 点击可排序表头：把新的排序状态上报父级（父级负责回查接口，全量数据排序） */
  (e: "update:sort", v: { key: string | null; order: "asc" | "desc" | null }): void;
}>();

/**
 * 左侧冻结列定义（固定顺序与宽度，sticky 偏移由此推导）。
 *
 * @remarks
 * 「信息」列竖排 5 个身份字段：名称 / 上下柜 / 类目 / SPU / 销售员，
 * 对应需求截图里的第二列。
 */
const LEAD_COLS = [
  { key: "image", width: 150 },
  { key: "info", width: 120 },
  { key: "sku", width: 130 },
] as const;

/** 信息列竖排的字段清单（顺序即展示顺序）。 */
const INFO_LINES = [
  { field: "sku_name" },
  { field: "shelf_status", tag: true },
  { field: "category" },
  { field: "spu" },
  { field: "seller" },
] as const;

/** 冻结列左偏移（sticky 固定列用）。 */
const leadLeft = computed<number[]>(() => {
  const arr: number[] = [];
  let acc = 0;
  for (const c of LEAD_COLS) {
    arr.push(acc);
    acc += c.width;
  }
  return arr;
});

/** 冻结列总宽（空态 / 表头 colspan 基准之一）。 */
const LEAD_TOTAL_W = LEAD_COLS.reduce((s, c) => s + c.width, 0);

/**
 * 支持点击表头排序的列：仅「现货库存」。
 *
 * @remarks
 * 排序口径 = 该行 8 城合计（即单元格末行「【合计】」）。
 */
const SORT_METRIC_KEY = "city_stock";

/** 每城指标列宽（要竖排 8 个城市 + 合计，比原来单城子列宽）。 */
const METRIC_COL_W = 118;

/**
 * 「现货库存」列的列宽：比其它每城指标列宽，要额外放下「数值槽 + 缺口后缀」。
 *
 * @remarks
 * 三个固定槽位合计 52（城市名）+ 30（数值）+ 34（后缀）= 116px，
 * 加单元格左右内边距各 10px → 列宽需 ≥136px，取 140px（余量 4px）。
 * 只加宽这一列、不动 METRIC_COL_W：否则整张表每列都 +22px，横向明显变宽。
 */
const STOCK_COL_W = 140;

/**
 * 每个指标列的宽度（表头 th 用它定列宽，列宽由此传导到该列所有单元格）。
 *
 * @remarks
 * 只有「现货库存」列用 STOCK_COL_W，其余每城指标列保持 METRIC_COL_W。
 */
function metricColW(m: InventoryMetric): number {
  return m.key === SORT_METRIC_KEY ? STOCK_COL_W : METRIC_COL_W;
}

/**
 * 可见指标列（不含冻结列）：per_city / national 都是一列。
 */
const metricCols = computed<InventoryMetric[]>(() => props.visibleMetrics);

/** 总列数（空态 colspan 用）。 */
const totalCols = computed(() => LEAD_COLS.length + metricCols.value.length);

/**
 * 单元格数值格式化：整数带千分位；小数最多 2 位；空/非法 → "—"。
 */
function fmtNum(v: InvCell): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const r = Math.round(n * 100) / 100;
  return Number.isInteger(r)
    ? r.toLocaleString("zh-CN")
    : r.toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/**
 * 现货库存列的城市行（可能带三档着色信息）。
 */
interface CityLine {
  city: string;
  text: string;
  /** 三档状态：healthy / incoming / alert；null = 不着色（非现货列 / 无阈值 / 下柜行） */
  status?: "healthy" | "incoming" | "alert" | null;
  /** 悬浮详情（现货 / 在途 / 阈值 / 档位说明） */
  tip?: string;
  /**
   * 缺口后缀：预警 = 「缺N」（离阈值还差几件），在途 = 「途N」（在途几件可补足）；健康态为空串。
   *
   * @remarks
   * 与数值同色（红 / 琥珀），排在自己的固定槽位里居中 → 后缀成列。
   */
  suffix?: string;
}

/**
 * 每城指标在单元格里的竖排行：城市名 + 格式化值。
 *
 * @remarks
 * 「现货库存」列（city_stock）附加三档预警着色（2026-10 新增）：
 *   healthy（现货 ≥ 阈值）不着色；incoming（在途可补足）黄色；alert 红色；
 *   下柜行 / 无阈值数据时不着色。判定口径与后端 alert_rules 一致。
 */
function cityLines(m: InventoryMetric, row: Record<string, InvCell>): CityLine[] {
  const off = isOffshelfRow(row);
  const colorize = m.key === SORT_METRIC_KEY && props.cityCodes && props.thresholds && !off;
  return props.cities.map((c, i) => {
    const line: CityLine = { city: c, text: fmtNum(row[m.fields[i]]) };
    if (!colorize) return line;
    const code = props.cityCodes![i];
    const t = props.thresholds![code];
    if (t === undefined) return line;
    const stock = Number(row[m.fields[i]]);
    const purchase = Number(row[`${code}_purchase`]);
    const st: CityLine["status"] =
      stock >= t ? "healthy" : stock + purchase >= t ? "incoming" : "alert";
    line.status = st;
    // 缺口后缀：预警行写「缺N」（离阈值还差几件），在途行写「途N」（在途几件可补足）
    // 数值非有限（脏数据）时兜底为 0，避免页面上出现「缺NaN」
    const gap = Number.isFinite(stock) ? Math.max(0, Math.round(t - stock)) : 0;
    const purch = Number.isFinite(purchase) ? Math.round(purchase) : 0;
    line.suffix =
      st === "alert" ? `缺${gap}` : st === "incoming" ? `途${purch}` : "";
    line.tip =
      `${c} · 现货 ${fmtNum(stock)} · 在途 ${fmtNum(purchase)} · 阈值 ${fmtNum(t)}\n` +
      (st === "alert"
        ? "预警：现货 + 在途均低于阈值，需补货"
        : st === "incoming"
          ? "在途：现货低于阈值，采购未到货可补足缺口"
          : "健康：现货不低于阈值");
    return line;
  });
}

/** 下柜行判定（shelf_status 含「下」，与后端 is_offshelf 同口径）：不着色。 */
function isOffshelfRow(row: Record<string, InvCell>): boolean {
  const v = row["shelf_status"];
  return typeof v === "string" && v.includes("下");
}

/**
 * 每城指标行内合计文本：= 该行 8 城自身求和（对照需求截图）。
 *
 * @remarks
 * 注意与后端 city_totals（全量筛选的每城汇总）区分：截图里的「【合计】」
 * 紧贴本行的 8 个城市值，语义是「本行 8 城加总」；
 * 比率类指标（aggregatable=false）不显示合计。
 */
function rowCityTotal(m: InventoryMetric, row: Record<string, InvCell>): string | null {
  if (!m.aggregatable) return null;
  let s = 0;
  for (const f of m.fields) {
    const v = Number(row[f]);
    if (Number.isFinite(v)) s += v;
  }
  return fmtNum(s);
}

/** 支持点击表头排序的列：仅「现货库存」——见文件上方 SORT_METRIC_KEY。 */

/** 「现货库存」列元信息；该列被指标配置勾掉时表头不再显示排序入口。 */
const sortMetric = computed<InventoryMetric | null>(
  () => props.visibleMetrics.find((m) => m.key === SORT_METRIC_KEY) ?? null
);

/** 当前排序方向（由父级持有；null = 未排序）。 */
const sortOrder = computed<"asc" | "desc" | null>(() => props.sortOrder ?? null);

/** 表头是否处于「已排序」状态（用于渲染排序箭头）。 */
const sorted = computed<boolean>(
  () => props.sortKey === SORT_METRIC_KEY && sortOrder.value !== null
);

/**
 * 指标列表头点击：仅「现货库存」列响应排序，其余列保持原有交互（无副作用）。
 *
 * @remarks
 * **不在前端排数据**：排序作用于全部行而非当页 50 条，故这里只上报新的排序状态，
 * 由父级带着 sort_by / sort_order 回查 /rows（后端先排序再分页）。
 * 状态机：未排序 → 降序 → 升序 → 降序（不做「取消排序」态，免得点第三下才回原序）。
 */
function onMetricHeadClick(m: InventoryMetric): void {
  if (m.key !== SORT_METRIC_KEY) return;
  const cur = sortOrder.value;
  const next = cur === null ? "desc" : cur === "desc" ? "asc" : "desc";
  emit("update:sort", { key: SORT_METRIC_KEY, order: next });
}

/**
 * 行在 props.rows 中的序号（v-for 的 key 用，与排序状态无关）。
 *
 * @remarks
 * 排序由后端完成，换页即换一批行对象，key 用「SKU + 页内序号」即可稳定定位，
 * 避免同一批 DOM 被误复用（产品图错位 / 复制按钮状态串行）。
 */
function rowKey(row: InvRow, i: number): string {
  return `${String(row["sku"] ?? "")}-${i}`;
}

/**
 * 悬浮提示文本：仅字符串值返回（数字/空值不提示）。
 */
function infoTitle(v: InvCell | undefined): string | undefined {
  return typeof v === "string" ? v : undefined;
}

/**
 * 「全国滞销件数」列头弹窗的悬停状态。
 *
 * @remarks
 * 纯 CSS :hover 方案会失效：弹窗自身 overflow 滚动容器会裁掉用于「桥接缝隙」
 * 的伪元素，鼠标从表头移向列表途中一旦离开触发区，列表立刻消失。
 * 这里改为 JS 状态机：进入触发区/命中层即展开，离开后延迟 ~280ms 收起，
 * 配合命中层 padding 扩大范围，鼠标移入列表的整个过程列表始终可见。
 */
const popOpen = ref(false);
let popTimer: number | undefined;

/** 进入触发区或命中层：立即展开，并取消待执行的收起。 */
function onPopEnter(): void {
  if (popTimer !== undefined) {
    clearTimeout(popTimer);
    popTimer = undefined;
  }
  popOpen.value = true;
}

/** 离开触发区/命中层：延迟收起，给鼠标移入列表留出时间窗口。 */
function onPopLeave(): void {
  if (popTimer !== undefined) clearTimeout(popTimer);
  popTimer = window.setTimeout(() => {
    popOpen.value = false;
    popTimer = undefined;
  }, 280);
}

/**
 * 上下柜状态 → 标签样式。
 */
function shelfTag(v: InvCell): { text: string; cls: string } | null {
  if (typeof v !== "string" || !v) return null;
  if (v.includes("上")) return { text: v, cls: "up" };
  if (v.includes("下")) return { text: v, cls: "down" };
  return { text: v, cls: "neutral" };
}

/**
 * 「全国滞销件数」列的全城市拆分行：城市 → 件数（仅 >0），按件数降序。
 *
 * @remarks
 * 数据来自后端 slow_cities（约 70 个城市/仓，不限于 8 城白名单）；
 * 末行「全国合计」单独展示权威全国值——源表里个别行的全国口径与
 * 城市合计存在出入，两者并列展示，不混为一个「合计」。
 */
function slowCityLines(row: InvRow): { city: string; text: string }[] {
  const sc = row.slow_cities;
  if (!sc) return [];
  return Object.entries(sc)
    .filter(([, v]) => Number(v) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([city, v]) => ({ city, text: fmtNum(v) }));
}

/**
 * 城市行行高倍数。
 *
 * @remarks
 * 必须与 CSS `.cityline { line-height }` 保持一致：滞销列的可视高度按
 * 「现货库存列的城市数 × 该行高」换算，两列才能严格等高。
 */
const CITYLINE_LH = 1.8;

/**
 * 滞销列的可视行数基准 = 现货库存列的城市数（8 城白名单）。
 *
 * @remarks
 * 滞销城市可能有几十个，直接铺开会把行撑得极高；这里把可视高度锁到
 * 「现货库存列 8 个城市 + 合计」的尺寸，超出的城市隐藏，悬浮看全量。
 */
const slowMaxLines = computed<number>(() => props.cities.length);

/** 滞销列城市列表的可视高度（与现货库存列的 N 个城市行等高）。 */
const slowListMaxH = computed<string>(() => `${slowMaxLines.value * CITYLINE_LH}em`);

/**
 * 信息列「商品名称」可显示行数（方案 A：纯公式，零 DOM 测量）。
 *
 * @remarks
 * 行高由冻结列之外的指标列决定：产品图固定 120px；每城指标列 = 城市数 × 行高
 * （aggregatable 列多一行「合计」）；全国滞销列被 slowListMaxH 锁到同样高度。
 * 因此「名称可用高度 = max(图高, 指标列高) − 其余 4 行身份字段高度」，再除以名称行高。
 * 与 slowListMaxH 复用同一套「行高 = 城市数 × CITYLINE_LH」假设（见上），
 * 不引入 ResizeObserver / 两遍测量，零布局读取开销。
 *
 * 兜底：没有任何多行指标（只剩单行全国指标）时，行高退回产品图 120px，
 * 可用高度只够 3 行 → 锁 3 行（与现状一致），避免名称把行撑高。
 *
 * 已知失真（影响可忽略）：比率类 per_city 指标（aggregatable=false）无「合计」行，
 * 实际行高比公式少一行高（22.5px），会多给 1 行；默认勾选的「现货库存」是
 * aggregatable，合计行存在，故常态下无此偏差。
 */
const NAME_LINE_LH = 1.5;    // CSS .name-text line-height
const INFO_LINE_LH = 1.7;    // CSS .info-line line-height（上柜/类目/SPU/销售员）
const INFO_META_LINES = 4;   // 除名称外的身份字段行数
const IMG_CONTENT_H = 120;   // CSS .imgbox 高度（产品图列，行高下限）
const BASE_FS = 12.5;        // CSS .mt font-size，em 行高换算基准
const nameLineClamp = computed<number>(() => {
  const N = props.cities.length;
  let metricH = 0;
  for (const m of props.visibleMetrics) {
    if (m.kind === "per_city") {
      metricH = Math.max(metricH, (m.aggregatable ? N + 1 : N) * CITYLINE_LH * BASE_FS);
    } else if (m.key === "nat_slow") {
      metricH = Math.max(metricH, N * CITYLINE_LH * BASE_FS);
    } else {
      metricH = Math.max(metricH, INFO_LINE_LH * BASE_FS);
    }
  }
  const otherH = Math.max(IMG_CONTENT_H, metricH);
  const avail = otherH - INFO_META_LINES * INFO_LINE_LH * BASE_FS;
  const lines = Math.floor(avail / (NAME_LINE_LH * BASE_FS));
  return Math.max(3, Math.min(12, lines));
});

/** 8 城白名单集合（浮层里高亮核心城市，与列头浮层口径一致）。 */
const coreCitySet = computed<Set<string>>(() => new Set(props.cities));

/** 每行滞销城市拆分行（与 rows 同序，避免模板里重复计算同一份数据）。 */
const slowRowLines = computed<{ city: string; text: string }[][]>(() =>
  props.rows.map((r) => slowCityLines(r))
);

/**
 * 「全国滞销件数」单元格的悬浮浮层状态：展示被裁剪掉的完整城市清单。
 *
 * @remarks
 * 浮层用 position: fixed + Teleport 挂到 body：表格容器 .mt-scroll 是
 * overflow:auto 的滚动上下文，绝对定位的浮层会被它裁掉。
 */
const slowTipOpen = ref(false);
const slowTipLines = ref<{ city: string; text: string }[]>([]);
const slowTipPos = ref({ top: 0, left: 0 });
const slowTipMaxH = ref(300);
/** 浮层是否翻转到单元格左侧（右侧空间不足时）。 */
const slowTipFlip = ref(false);
let slowTipTimer: number | undefined;

/** 浮层宽度（与 CSS `.slow-tip` 的 width 保持一致）。 */
const SLOW_TIP_W = 210;
/** 浮层与单元格之间的间距。 */
const SLOW_TIP_GAP = 6;

/** 取消待执行的收起计时。 */
function cancelSlowTipTimer(): void {
  if (slowTipTimer !== undefined) {
    clearTimeout(slowTipTimer);
    slowTipTimer = undefined;
  }
}

/**
 * 鼠标进入单元格：仅当城市数超出可视行数（即有隐藏城市）时展开浮层。
 *
 * @remarks
 * 浮层默认贴在单元格**右侧**（与用户视线方向一致，不遮挡当前列）；
 * 右侧空间不足（靠近视口右边缘）时翻到左侧，上下再做一次视口内收拢。
 */
function onSlowTipEnter(e: MouseEvent, lines: { city: string; text: string }[]): void {
  if (lines.length <= slowMaxLines.value) return;
  cancelSlowTipTimer();
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  // 右侧放不下就翻到左侧
  let left = rect.right + SLOW_TIP_GAP;
  let flip = false;
  if (left + SLOW_TIP_W > window.innerWidth - 8) {
    left = rect.left - SLOW_TIP_GAP - SLOW_TIP_W;
    flip = true;
  }
  left = Math.max(8, Math.min(left, window.innerWidth - SLOW_TIP_W - 8));
  // 纵向：贴着单元格顶部，必要时收拢进视口
  const maxH = Math.max(140, Math.min(320, window.innerHeight - 24));
  const top = Math.max(8, Math.min(rect.top, window.innerHeight - maxH - 12));
  slowTipLines.value = lines;
  slowTipPos.value = { top, left };
  slowTipMaxH.value = maxH;
  slowTipFlip.value = flip;
  slowTipOpen.value = true;
}

/** 鼠标移入浮层本身：保持展开（便于在浮层里滚动查看全部城市）。 */
function keepSlowTip(): void {
  cancelSlowTipTimer();
}

/** 鼠标离开：延迟收起，给鼠标移入浮层留出时间窗口。 */
function onSlowTipLeave(): void {
  cancelSlowTipTimer();
  slowTipTimer = window.setTimeout(() => {
    slowTipOpen.value = false;
    slowTipTimer = undefined;
  }, 280);
}

/** 强制收起（表格滚动 / 窗口缩放后浮层位置会失效）。 */
function closeSlowTip(): void {
  slowTipOpen.value = false;
}

/**
 * 滚动时收起浮层。
 *
 * @remarks
 * 用捕获阶段监听，才能收到表格容器 .mt-scroll 自身的 scroll 事件；
 * 但**浮层内部滚动**时事件 target 落在浮层里，此时必须放行，
 * 否则会出现「在浮层里滚动列表 → 浮层自己把自己关掉」。
 */
function onWindowScroll(e: Event): void {
  const t = e.target as HTMLElement | null;
  const closest = (sel: string): boolean =>
    !!t && typeof t.closest === "function" && !!t.closest(sel);
  if (closest(".slow-tip")) return;
  // 表格内部滚动不影响表头位置（表头 sticky），筛选面板可继续开着；
  // 页面滚动会让表头整体移位 → 收起面板，避免错位。
  if (filterOpen.value && !closest(".mt-scroll")) filterOpen.value = false;
  closeSlowTip();
}

onMounted(() => {
  // 捕获阶段监听：表格容器自身的滚动也会触发 scroll 事件
  window.addEventListener("scroll", onWindowScroll, true);
  window.addEventListener("resize", closeSlowTip);
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onEsc);
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", onWindowScroll, true);
  window.removeEventListener("resize", closeSlowTip);
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onEsc);
  cancelSlowTipTimer();
  if (copyTimer !== undefined) clearTimeout(copyTimer);
});

/**
 * 库存 SKU 商品图：原图路径 → 缩略图路径。
 *
 * 约定（与后端 backend/jobs/inv_pics.py 一一对应）：
 *   原图  /inv-pics/{业务线}/{SKU}.jpg
 *   缩略图 /thumbs/inv/{业务线}/{尺寸}/{SKU}.{avif|webp}
 *
 * @remarks
 * 刻意沿用项目既有的 thumb() 模式（见 views/ModuleView.vue）：后端只给原图路径，
 * 前端按展示尺寸换算缩略图路径，省得后端把两套路径都塞进响应体。
 */
function invThumb(pic: string, size: number, fmt: "avif" | "webp"): string {
  const parts = pic.split("/").filter(Boolean);
  if (parts.length < 3) return "";
  const sub = parts[parts.length - 2];
  const sku = (parts[parts.length - 1] ?? "").replace(/\.[^.]+$/, "");
  if (!sub || !sku) return "";
  return `/thumbs/inv/${sub}/${size}/${sku}.${fmt}`;
}

/**
 * 缩略图尺寸档（像素）。
 *
 * @remarks
 * 商品图本身是 1:1 的正方形（源图 800×800），展示框也做成 1:1（120×120），
 * 缩略图按 **1:1 呈现**：120px 缩略图直接铺在 120px 框里，像素对像素、不缩放不重采样，
 * 边缘最锐利（缩略图长边就是 120，宽度同为 120）。
 * 不做 2x 档：DPR 高只是让浏览器用更多物理像素画同一块区域，
 * 对"1:1 呈现"这个诉求没有帮助，反而多一倍流量。
 */
const THUMB_SIZE = 120;

/**
 * 每行图片的降级档位：0=avif 缩略图 / 1=webp 缩略图 / 2=原图 / 3=占位（无图可显示）。
 *
 * @remarks
 * 用「行 key → 档位」的记录而不是 img.dataset：档位放在响应式状态里，
 * Vue 重渲染后不会把已经降级过的 src 又打回原值，行为确定可推理。
 */
const picStage = ref<Record<string, number>>({});

/** 取某行当前降级档位（默认 0 = 缩略图）。 */
function stageOf(key: string): number {
  return picStage.value[key] ?? 0;
}

/** 记录某行降级档位。 */
function setStage(key: string, stage: number): void {
  picStage.value = { ...picStage.value, [key]: stage };
}

/**
 * 该行应显示的图片地址；返回 null 表示「不显示图片，只显示占位」。
 *
 * @remarks
 * 降级链：avif 缩略图 → webp 缩略图 → 原始大图 → 占位。
 * 缩略图与原图是成对生成的（avif/webp 同时出），所以 avif 失败时 webp 也很可能不存在，
 * 但仍按链逐级降级：任一档存在就能显示，比直接跳到 1MB 原图更划算。
 */
function picSrc(row: InvRow, ri: number): string | null {
  const pic = row["sku_pic"];
  if (typeof pic !== "string" || !pic) return null;
  const stage = stageOf(rowKey(row, ri));
  if (stage === 0) return invThumb(pic, THUMB_SIZE, "avif") || invThumb(pic, THUMB_SIZE, "webp") || pic;
  if (stage === 1) return invThumb(pic, THUMB_SIZE, "webp") || pic;
  if (stage === 2) return pic;
  return null;
}

/** 图片加载失败：沿降级链退到下一档，全部失败则露出「暂无图片」占位。 */
function onPicError(row: InvRow, ri: number): void {
  const key = rowKey(row, ri);
  const stage = stageOf(key);
  setStage(key, stage >= 2 ? 3 : stage + 1);
}

/** 行数据换页/筛选后整体替换：旧的降级档位不再适用，清掉避免档位错位。 */
watch(
  () => props.rows,
  () => {
    picStage.value = {};
  }
);

/**
 * 点击图片放大看原图。
 *
 * @remarks
 * 浮层用 position: fixed + Teleport 挂到 body：表格容器 .mt-scroll 是 overflow:auto
 * 的滚动上下文，绝对定位的浮层会被它裁掉（与 slow-tip / filter-panel 同一个坑）。
 */
const zoomOpen = ref(false);
const zoomSrc = ref("");
const zoomCaption = ref("");

/** 打开大图浮层。 */
function openZoom(row: InvRow, ri: number): void {
  const pic = row["sku_pic"];
  if (typeof pic !== "string" || !pic) return;
  if (stageOf(rowKey(row, ri)) >= 3) return; // 已彻底失败，没有可放大的图
  zoomSrc.value = pic;
  zoomCaption.value = `${String(row["sku"] ?? "")} · ${String(row["sku_name"] ?? "")}`;
  zoomOpen.value = true;
}

/** 关闭大图浮层。 */
function closeZoom(): void {
  zoomOpen.value = false;
}

/**
 * 「信息」列筛选面板状态。
 *
 * @remarks
 * 面板浮在表头下方，用 position: fixed + Teleport 挂到 body：
 * 表格容器 .mt-scroll 是 overflow:auto 的滚动上下文，绝对定位的面板会被裁掉。
 */
const filterOpen = ref(false);
/** 面板里的草稿值（点「确定」才提交，避免每勾一次就重拉数据） */
const filterDraft = ref<InventoryFilters>({ category: [], seller: [], shelf_status: [] });
const filterPos = ref({ top: 0, left: 0 });
const filterMaxH = ref(360);
/** 面板根节点（点击面板外部关闭时判断命中） */
const filterPanelRef = ref<HTMLElement | null>(null);

/** 已生效的筛选项数量（表头漏斗上的角标）。 */
const filterCount = computed(
  () =>
    props.filters.category.length +
    props.filters.seller.length +
    props.filters.shelf_status.length
);

/** 打开筛选面板：把当前条件复制成草稿，并按按钮位置定位面板。 */
function openFilter(e: MouseEvent): void {
  const el = e.currentTarget as HTMLElement;
  const r = el.getBoundingClientRect();
  filterDraft.value = {
    category: [...props.filters.category],
    seller: [...props.filters.seller],
    shelf_status: [...props.filters.shelf_status],
  };
  const top = r.bottom + 6;
  const w = 280;
  filterPos.value = { top, left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) };
  filterMaxH.value = Math.max(200, Math.min(420, window.innerHeight - top - 12));
  filterOpen.value = true;
}

/** 关闭筛选面板。 */
function closeFilter(): void {
  filterOpen.value = false;
}

/** 草稿里勾选 / 取消一个值。 */
function toggleFilterValue(key: keyof InventoryFilters, value: string): void {
  const arr = filterDraft.value[key];
  const i = arr.indexOf(value);
  if (i >= 0) arr.splice(i, 1);
  else arr.push(value);
}

/** 草稿里某维度是否已选中。 */
function filterPicked(key: keyof InventoryFilters, value: string): boolean {
  return filterDraft.value[key].includes(value);
}

/** 清空某个维度的草稿。 */
function clearFilterGroup(key: keyof InventoryFilters): void {
  filterDraft.value[key] = [];
}

/** 提交筛选条件。 */
function applyFilter(): void {
  emit("update:filters", {
    category: [...filterDraft.value.category],
    seller: [...filterDraft.value.seller],
    shelf_status: [...filterDraft.value.shelf_status],
  });
  filterOpen.value = false;
}

/** 重置：清空全部维度并立即生效。 */
function resetFilter(): void {
  emit("update:filters", { category: [], seller: [], shelf_status: [] });
  filterOpen.value = false;
}

/** 点击面板外部关闭。 */
function onDocClick(e: MouseEvent): void {
  if (!filterOpen.value) return;
  const t = e.target as HTMLElement | null;
  if (t && typeof t.closest === "function") {
    if (t.closest(".filter-panel") || t.closest(".th-filter")) return;
  }
  filterOpen.value = false;
}

/** Esc 关闭（筛选面板 / 大图浮层）。 */
function onEsc(e: KeyboardEvent): void {
  if (e.key !== "Escape") return;
  filterOpen.value = false;
  closeZoom();
}

/** 行内 SKU 编码文本（复制按钮用）。 */
function skuOf(row: InvRow): string {
  const v = row["sku"];
  return v === null || v === undefined ? "" : String(v);
}

/** 最近一次复制成功的 SKU（按钮图标与提示据此切换）。 */
const copiedSku = ref<string | null>(null);
let copyTimer: number | undefined;

/**
 * 写剪贴板。
 *
 * @remarks
 * navigator.clipboard 只在安全上下文（https / localhost）可用，
 * 局域网 http 访问时降级为 execCommand('copy')。
 */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 继续走降级方案 */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** 复制 SKU 编码：成功后按钮短暂显示「已复制」。 */
async function copySku(sku: string): Promise<void> {
  if (!sku) return;
  if (!(await writeClipboard(sku))) return;
  copiedSku.value = sku;
  if (copyTimer !== undefined) clearTimeout(copyTimer);
  copyTimer = window.setTimeout(() => {
    copiedSku.value = null;
    copyTimer = undefined;
  }, 1600);
}
</script>

<template>
  <div class="mt-scroll" data-testid="inv-matrix-scroll">
    <table class="mt" data-testid="inv-matrix-table">
      <thead>
        <tr>
          <!-- 冻结列：产品图 / 信息 / SKU -->
          <th
            v-for="(c, i) in LEAD_COLS"
            :key="'lead-' + c.key"
            class="th th-lead"
            :style="{ left: leadLeft[i] + 'px', width: c.width + 'px', minWidth: c.width + 'px' }"
          >
            <span class="th-label">{{ c.key === "image" ? "产品图" : c.key === "info" ? "信息" : "sku编码" }}</span>
            <!-- 信息列筛选：三级类目 / 销售员 / 上下柜状态 -->
            <button
              v-if="c.key === 'info'"
              class="th-filter"
              :class="{ active: filterCount > 0 }"
              data-testid="inv-info-filter-btn"
              title="按类目 / 销售员 / 上下柜状态筛选"
              @click.stop="openFilter"
            >
              <svg class="th-filter-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" /></svg>
              <span v-if="filterCount" class="th-filter-badge">{{ filterCount }}</span>
            </button>
          </th>

          <!-- 指标列：每城指标 / 全国指标均一列 -->
          <th
            v-for="m in metricCols"
            :key="'m-' + m.key"
            class="th th-metric"
            :class="{ sortable: m.key === SORT_METRIC_KEY, 'sort-active': sorted }"
            :style="{ width: metricColW(m) + 'px', minWidth: metricColW(m) + 'px' }"
            :data-testid="m.key === SORT_METRIC_KEY ? 'inv-sort-stock' : undefined"
            :aria-sort="sorted ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined"
            :title="m.key === SORT_METRIC_KEY ? '点击按本列合计排序（对全部数据生效）' : undefined"
            @click="onMetricHeadClick(m)"
          >
            <span class="th-label">{{ m.title }}</span>
            <!-- 排序指示：现货库存列可点，未排序显示双向箭头（悬停加深），已排序显示方向箭头 -->
            <span
              v-if="m.key === SORT_METRIC_KEY"
              class="th-sort-ico"
              :class="{ asc: sortOrder === 'asc', desc: sortOrder === 'desc' }"
              aria-hidden="true"
            >
              <svg v-if="!sorted" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 15 5 5 5-5" /><path d="m7 9 5-5 5 5" /></svg>
              <svg v-else-if="sortOrder === 'asc'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 14 5-5 5 5" /></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 10 5 5 5-5" /></svg>
            </span>
            <!-- 全国滞销件数：列头悬浮看全城市拆分（不限 8 城） -->
            <span
              v-if="m.key === 'nat_slow' && (slowCities ?? []).length > 0"
              class="th-pop-wrap"
              @mouseenter="onPopEnter"
              @mouseleave="onPopLeave"
            >
              <span class="th-pop-hint">{{ (slowCities ?? []).length }} 城</span>
              <!-- 独立命中层：把表头与列表之间的缝隙和四周留白都纳入 hover 区域，
                   鼠标移入途中不会离开触发区；再叠加延迟收起兜底 -->
              <span
                v-if="popOpen"
                class="th-pop-layer"
                @mouseenter="onPopEnter"
                @mouseleave="onPopLeave"
              >
                <span class="th-pop" @wheel.stop @mousedown.stop>
                  <span v-for="s in slowCities ?? []" :key="s.city" class="th-pop-row">
                    <span :class="{ core: s.core }">{{ s.city }}</span>
                    <span>{{ fmtNum(s.units) }}</span>
                  </span>
                  <span class="th-pop-more">共 {{ (slowCities ?? []).length }} 个城市</span>
                </span>
              </span>
            </span>
          </th>
        </tr>
      </thead>

      <tbody>
        <tr v-if="rows.length === 0">
          <td class="td empty" :colspan="totalCols">当前筛选条件下没有库存数据</td>
        </tr>
        <tr v-for="(row, ri) in rows" :key="rowKey(row, ri)" class="tr">
          <!-- 产品图（冻结）：按 SKU 取图，缩略图 → 原图 → 占位 逐级降级 -->
          <td
            class="td td-lead td-img"
            :style="{ left: leadLeft[0] + 'px', width: LEAD_COLS[0].width + 'px', minWidth: LEAD_COLS[0].width + 'px' }"
          >
            <div
              class="imgbox"
              :class="{ clickable: !!row.sku_pic && stageOf(rowKey(row, ri)) < 3 }"
              :title="row.sku_pic ? '点击查看大图' : '该 SKU 暂无商品图'"
              @click="openZoom(row, ri)"
            >
              <!-- 占位：永远垫在图片之下，图片彻底失败时自然露出（排版不塌） -->
              <span class="imgph">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect width="18" height="18" x="3" y="3" rx="2" />
                  <circle cx="9" cy="9" r="2" />
                  <path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" />
                </svg>
                <span>暂无图片</span>
              </span>
              <img
                v-if="picSrc(row, ri)"
                :src="picSrc(row, ri)!"
                :alt="String(row['sku_name'] ?? '')"
                loading="lazy"
                decoding="async"
                @error="onPicError(row, ri)"
              />
            </div>
          </td>
          <!-- 信息（冻结，竖排身份字段） -->
          <td
            class="td td-lead td-info"
            :style="{ left: leadLeft[1] + 'px', width: LEAD_COLS[1].width + 'px', minWidth: LEAD_COLS[1].width + 'px', '--name-lines': nameLineClamp }"
          >
            <div v-for="l in INFO_LINES" :key="l.field" class="info-line" :class="{ 'name-line': l.field === 'sku_name' }">
              <span
                v-if="'tag' in l && l.tag && shelfTag(row[l.field])"
                class="tag"
                :class="shelfTag(row[l.field])!.cls"
              >{{ shelfTag(row[l.field])!.text }}</span>
              <span
                v-else
                class="info-text"
                :class="{ 'name-text': l.field === 'sku_name' }"
                :title="infoTitle(row[l.field])"
              >{{ fmtNum(row[l.field]) }}</span>
            </div>
          </td>
          <!-- SKU（冻结，水平垂直居中 + 复制按钮） -->
          <td
            class="td td-lead td-sku"
            :style="{ left: leadLeft[2] + 'px', width: LEAD_COLS[2].width + 'px', minWidth: LEAD_COLS[2].width + 'px' }"
          >
            <div class="sku-wrap">
              <span class="sku-text">{{ skuOf(row) }}</span>
              <button
                class="copy-btn"
                :class="{ done: copiedSku === skuOf(row) }"
                data-testid="inv-copy-sku"
                :title="copiedSku === skuOf(row) ? '已复制' : '复制 SKU 编码'"
                @click.stop="copySku(skuOf(row))"
              >
                <svg v-if="copiedSku !== skuOf(row)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
              </button>
            </div>
          </td>

          <!-- 指标列 -->
          <td
            v-for="m in metricCols"
            :key="'c-' + m.key"
            class="td td-metric"
            :class="{ 'td-nat': m.kind === 'national', 'td-stock': m.key === SORT_METRIC_KEY }"
          >
            <!-- 每城指标：竖排「城市: 值」，可累加指标末行合计；
                 现货库存列城市行按三档预警着色（alert 红字 / incoming 琥珀棕字，健康不着色），
                 并在数值右侧追加同色缺口后缀（缺N / 途N） -->
            <template v-if="m.kind === 'per_city'">
              <div
                v-for="line in cityLines(m, row)"
                :key="line.city"
                class="cityline"
                :class="{
                  'cl-alert': line.status === 'alert',
                  'cl-incoming': line.status === 'incoming',
                }"
                :title="line.tip"
              >
                <span class="city">{{ line.city }}</span>
                <!-- 现货库存列：数值按三档着色（预警红 / 在途棕 / 健康中性），
                     后缀走同色缺口标记（缺N / 途N）；两者各占固定槽位，数字与后缀各自成列 -->
                <span class="val">{{ line.text }}</span>
                <span v-if="m.key === SORT_METRIC_KEY" class="suf">{{ line.suffix }}</span>
              </div>
              <div v-if="rowCityTotal(m, row) !== null" class="cityline total">
                <span class="city">【合计】</span>
                <span class="val">{{ rowCityTotal(m, row) }}</span>
              </div>
            </template>
            <!-- 全国滞销件数：城市清单按「现货库存列」的行数固定高度，超出隐藏；
                 悬浮显示完整城市拆分（同商品名称：截断 + 悬浮看全量） -->
            <template v-else-if="m.key === 'nat_slow'">
              <div
                v-if="slowRowLines[ri] && slowRowLines[ri].length"
                class="slow-cell"
                :class="{ trunc: slowRowLines[ri].length > slowMaxLines }"
                @mouseenter="onSlowTipEnter($event, slowRowLines[ri])"
                @mouseleave="onSlowTipLeave"
              >
                <div class="slow-list" :style="{ maxHeight: slowListMaxH }">
                  <div v-for="line in slowRowLines[ri]" :key="line.city" class="cityline">
                    <span class="city">{{ line.city }}</span>
                    <span class="val">{{ line.text }}</span>
                  </div>
                </div>
                <div class="cityline nat-total">
                  <span class="city">全国合计</span>
                  <span class="val">{{ fmtNum(row[m.fields[0]]) }}</span>
                </div>
              </div>
              <!-- 无拆分数据（该行无滞销）时退回单值 -->
              <template v-else>{{ fmtNum(row[m.fields[0]]) }}</template>
            </template>
            <!-- 其他全国指标：单值 -->
            <template v-else>{{ fmtNum(row[m.fields[0]]) }}</template>
          </td>
        </tr>
      </tbody>

      <tfoot>
        <tr class="tf">
          <td
            v-for="(c, i) in LEAD_COLS"
            :key="'ft-' + c.key"
            class="td td-lead td-total"
            :style="{ left: leadLeft[i] + 'px', width: c.width + 'px', minWidth: c.width + 'px' }"
          >
            {{ c.key === "info" ? "合计" : "" }}
          </td>
          <td
            v-for="m in metricCols"
            :key="'ftm-' + m.key"
            class="td td-metric td-total"
            :class="{ 'td-nat': m.kind === 'national' }"
          >
            {{ m.kind === "national" ? fmtNum(totals[m.key]) : "" }}
          </td>
        </tr>
      </tfoot>
    </table>
  </div>

  <!-- 滞销列被隐藏的城市清单：Teleport 到 body，避免被表格滚动容器裁剪 -->
  <Teleport to="body">
    <div
      v-if="slowTipOpen"
      class="slow-tip"
      :class="{ flip: slowTipFlip }"
      :style="{ top: slowTipPos.top + 'px', left: slowTipPos.left + 'px' }"
      @mouseenter="keepSlowTip"
      @mouseleave="onSlowTipLeave"
    >
      <!-- 滚动区单独一层：外层 overflow 可见，才能用伪元素把「单元格 → 浮层」
           之间的缝隙桥接起来，鼠标横移时浮层不会闪退 -->
      <div class="slow-tip-body" :style="{ maxHeight: slowTipMaxH + 'px' }" @wheel.stop>
        <div v-for="line in slowTipLines" :key="line.city" class="slow-tip-row">
          <span class="city" :class="{ core: coreCitySet.has(line.city) }">{{ line.city }}</span>
          <span class="val">{{ line.text }}</span>
        </div>
        <div class="slow-tip-more">共 {{ slowTipLines.length }} 个城市</div>
      </div>
    </div>
  </Teleport>

  <!-- 信息列筛选面板：Teleport 到 body，避免被表格滚动容器裁剪 -->
  <Teleport to="body">
    <div
      v-if="filterOpen"
      ref="filterPanelRef"
      class="filter-panel"
      data-testid="inv-info-filter-panel"
      :style="{ top: filterPos.top + 'px', left: filterPos.left + 'px', maxHeight: filterMaxH + 'px' }"
      @click.stop
    >
      <div class="filter-head">
        <span class="filter-title">筛选</span>
        <button class="filter-close" title="关闭" @click="closeFilter">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>
      </div>

      <div class="filter-body">
        <div class="filter-group">
          <div class="filter-group-head">
            <span class="filter-group-title">三级类目</span>
            <button class="filter-link" @click="clearFilterGroup('category')">清空</button>
          </div>
          <div class="filter-items">
            <label
              v-for="c in filterOptions.categories"
              :key="'cat-' + c"
              class="filter-item"
              :class="{ on: filterPicked('category', c) }"
            >
              <input
                type="checkbox"
                :checked="filterPicked('category', c)"
                @change="toggleFilterValue('category', c)"
              />
              <span>{{ c }}</span>
            </label>
          </div>
          <p v-if="!filterOptions.categories.length" class="filter-empty">暂无可选项</p>
        </div>

        <div class="filter-group">
          <div class="filter-group-head">
            <span class="filter-group-title">销售员</span>
            <button class="filter-link" @click="clearFilterGroup('seller')">清空</button>
          </div>
          <div class="filter-items">
            <label
              v-for="s in filterOptions.sellers"
              :key="'sel-' + s"
              class="filter-item"
              :class="{ on: filterPicked('seller', s) }"
            >
              <input
                type="checkbox"
                :checked="filterPicked('seller', s)"
                @change="toggleFilterValue('seller', s)"
              />
              <span>{{ s }}</span>
            </label>
          </div>
          <p v-if="!filterOptions.sellers.length" class="filter-empty">暂无可选项</p>
        </div>

        <div class="filter-group">
          <div class="filter-group-head">
            <span class="filter-group-title">上下柜状态</span>
            <button class="filter-link" @click="clearFilterGroup('shelf_status')">清空</button>
          </div>
          <div class="filter-items">
            <label
              v-for="s in filterOptions.shelf_statuses"
              :key="'sh-' + s"
              class="filter-item"
              :class="{ on: filterPicked('shelf_status', s) }"
            >
              <input
                type="checkbox"
                :checked="filterPicked('shelf_status', s)"
                @change="toggleFilterValue('shelf_status', s)"
              />
              <span>{{ s }}</span>
            </label>
          </div>
          <p v-if="!filterOptions.shelf_statuses.length" class="filter-empty">暂无可选项</p>
        </div>
      </div>

      <div class="filter-foot">
        <button class="filter-reset" @click="resetFilter">重置</button>
        <button class="filter-apply" data-testid="inv-info-filter-apply" @click="applyFilter">确定</button>
      </div>
    </div>
  </Teleport>

  <!-- 点击第一列图片 → 看原图大图：Teleport 到 body，避免被表格滚动容器裁掉 -->
  <Teleport to="body">
    <div
      v-if="zoomOpen"
      class="pic-zoom"
      data-testid="inv-pic-zoom"
      @click="closeZoom"
    >
      <button class="pic-zoom-close" title="关闭（Esc）" @click.stop="closeZoom">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
      </button>
      <div class="pic-zoom-frame" @click.stop>
        <img :src="zoomSrc" :alt="zoomCaption" />
        <div class="pic-zoom-cap">{{ zoomCaption }}</div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mt-scroll {
  overflow: auto;
  /* 表格可视高度预算。
   *
   * 原来写死 `calc(100vh - 320px)`，但表格上方实际占用约 490px
   * （标题 84 + 概览卡 269 + 筛选条 55 + 计数行 30 + 若干间距），
   * 320px 的预留根本不够 → 表格底部（含合计行）被顶到视口之外，
   * 页面滚到底也露不全；现在按 200px 预留，把表格整体调高，
   * 页面下拉即可完整看到表格底部与合计行。
   *
   * min-height 兜底矮屏：视口不足时仍保留 420px，
   * 保证页面有足够滚动行程（否则筛选条里的指标配置面板露不全）。
   */
  max-height: calc(100vh - 200px);
  min-height: 420px;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.mt {
  border-collapse: separate;
  border-spacing: 0;
  width: max-content;
  min-width: 100%;
  font-size: 12.5px;
}
/* ---- 表头 ---- */
/* 全表头（含左侧 3 个冻结列）统一用品牌蓝：
   之前 `.th` 用中性 `--color-text-2`，而指标列在 `.sort-active` 下才转品牌色，
   导致「产品图 / 信息 / sku编码」三列表头是黑字、后续指标列是蓝字，同一行表头两种颜色。
   这里把基色直接定为 `--color-brand`，整行表头视觉统一；
   可排序列的悬停 / 已排序状态改用更深的 `--color-brand-dark` 保留反馈。

   2026-10 视觉优化（表头层级）：
   - 底色由 `--color-surface-2`(#f8fafc) 改为品牌蓝强档 `#dbeafe`（blue-100，库存视图 `--color-surface-head`）。
     原底色与纯白数据区只差 1.05:1，肉眼分不出表头与数据；浅蓝底对白底 1.09:1，
     克制但可辨，且与表头蓝字同族（"同色系分层"）。底色必须不透明——表头是 sticky 的。
   - 字号 12.5px → 14px、字重 600 → 700、字距 +0.2px：
     数据区维持 12.5px 保密度，表头跳到 14px 拉开层级（中文小字号下加粗比放大更有效）。 */
.th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--color-surface-head, #dbeafe);
  color: var(--color-brand);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.2px;
  text-align: center;
  padding: 10px 8px;
  border-bottom: 1px solid var(--color-border-3);
  border-right: 1px solid var(--color-border-3);
  white-space: nowrap;
  user-select: none;
}
/* 冻结列表头：顶层 + 冻结，z-index 最高 */
.th-lead {
  position: sticky;
  top: 0;
  left: 0;
  z-index: 3;
  /* 必须与 .th 同底色：左三列表头若沿用旧底色，会与右侧指标列断成两截 */
  background: var(--color-surface-head, #dbeafe);
}
.th-label {
  display: inline-block;
  vertical-align: middle;
}
/* ---- 表头排序（仅「现货库存」列可点） ---- */
.th.sortable {
  cursor: pointer;
  user-select: none;
}
.th.sortable:hover {
  color: var(--color-brand-dark);
}
.th.sort-active {
  color: var(--color-brand-dark);
}
/* 排序指示箭头：跟随表头蓝色（继承 currentColor），默认半透明弱化，
   悬停或已排序时完全显形——不再单独给灰色，避免与蓝字表头割裂 */
.th-sort-ico {
  display: inline-flex;
  vertical-align: middle;
  margin-left: 3px;
  color: inherit;
  opacity: 0.5;
  transition: color 0.15s, opacity 0.15s;
}
.th-sort-ico svg {
  width: 14px;
  height: 14px;
}
.th.sortable:hover .th-sort-ico {
  opacity: 0.85;
}
.th-sort-ico.asc,
.th-sort-ico.desc,
.th.sortable:hover .th-sort-ico.asc,
.th.sortable:hover .th-sort-ico.desc {
  opacity: 1;
}
/* ---- 信息列筛选按钮（表头漏斗） ---- */
.th-filter {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-left: 4px;
  padding: 0;
  vertical-align: middle;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  /* 表头统一蓝字后，漏斗按钮跟随品牌蓝并默认半透明弱化（激活 / 悬停时实显），
     避免蓝色标题里夹一个灰图标 */
  color: var(--color-brand);
  opacity: 0.65;
  cursor: pointer;
  transition: color 0.15s, background 0.15s, border-color 0.15s, opacity 0.15s;
}
.th-filter:hover {
  color: var(--color-brand-dark);
  background: var(--color-surface);
  border-color: var(--color-border-2);
  opacity: 1;
}
.th-filter.active {
  color: var(--color-brand);
  opacity: 1;
}
.th-filter-ico {
  width: 14px;
  height: 14px;
}
.th-filter-badge {
  position: absolute;
  top: -5px;
  right: -6px;
  min-width: 13px;
  height: 13px;
  padding: 0 3px;
  border-radius: var(--radius-pill);
  background: var(--color-brand);
  color: #fff;
  font-size: 9px;
  line-height: 13px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}/* ---- 全国滞销件数：列头悬浮的城市拆分 ---- */
.th-pop-wrap {
  position: relative;
  display: inline-block;
  margin-left: 4px;
  vertical-align: middle;
}
.th-pop-hint {
  font-size: 10px;
  color: var(--color-brand);
  cursor: help;
}
.th-pop {
  display: block;
  width: 190px;
  max-height: 250px;
  overflow-y: auto;
  overscroll-behavior: contain; /* 滚到列表边界时不把滚动传给外层表格 */
  padding: 9px 11px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
  text-align: left;
  font-weight: 400;
  user-select: text; /* 列表内可正常选中/复制城市名 */
}
/* 命中层：包在弹窗外层的透明盒子，padding 把「表头 → 列表」的缝隙
   以及列表四周都纳入可命中区域（自身不裁剪，桥接一定生效）。 */
.th-pop-layer {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  padding: 14px 12px;
  background: transparent;
}
.th-pop-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  line-height: 1.8;
  font-size: 11.5px;
  color: var(--color-text-2);
  font-variant-numeric: tabular-nums;
}
.th-pop-row > span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.th-pop-row .core {
  color: var(--color-brand);
  font-weight: 600;
}
.th-pop-more {
  display: block;
  position: sticky;
  bottom: -9px;
  margin-top: 4px;
  padding-top: 5px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border-4);
  font-size: 11px;
  color: var(--color-text-5);
}
/* ---- 单元格 ---- */
/* 数据区（tbody）底色显式写死纯白 `--color-surface`：
   不再依赖 .mt-scroll 容器的继承底色。.td 会被冻结列 / 悬停 / 合计行 / 全国列
   各自覆写，"透明"意味着任一处漏写就会露出别的底色；写死后数据区永远是一张白卡，
   与库存页容器 `inv-view` 的灰底 (#f6f8fb) 形成明确边界。 */
.td {
  padding: 7px 8px;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border-3);
  border-right: 1px solid var(--color-border-3);
  white-space: nowrap;
  color: var(--color-text-2);
  vertical-align: top;
}
.td-lead {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--color-surface);
}
/* 产品图：水平垂直居中 */
.td-img {
  text-align: center;
  vertical-align: middle;
}
/* 商品图卡片：白底 + 细边框，悬停转品牌蓝并浮起阴影（提示可点开看大图）
   尺寸 120×120 —— 与商品图 1:1 的比例一致，缩略图按 1:1 铺满，不留白也不裁切 */
.imgbox {
  position: relative;
  display: inline-block;
  width: 120px;
  height: 120px;
  line-height: 120px;
  text-align: center;
  vertical-align: middle;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-sm, 4px);
  overflow: hidden;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.imgbox.clickable {
  cursor: zoom-in;
}
.imgbox.clickable:hover {
  border-color: var(--color-brand);
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.16);
}
/* 占位：垫在图片之下，图片彻底失败时露出（与有图行等高等宽，排版不塌） */
.imgph {
  position: absolute;
  inset: 0;
  z-index: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  font-size: 11px;
  color: var(--color-text-5);
  background: var(--color-surface-2);
  line-height: 1.2;
}
.imgph svg {
  width: 20px;
  height: 20px;
  opacity: 0.7;
}
/* 图片按 1:1 铺满方框：框与缩略图都是 120×120，contain 后正好等比贴合 */
.imgbox img {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: var(--color-surface);
}
/* ---- 点击图片看大图（Teleport 到 body 的全屏浮层） ---- */
.pic-zoom {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background: rgba(15, 23, 42, 0.72);
  cursor: zoom-out;
}
.pic-zoom-frame {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: min(760px, 90vw);
  max-height: 88vh;
  padding: 12px;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 48px rgba(15, 23, 42, 0.3);
  cursor: default;
}
.pic-zoom-frame img {
  max-width: 100%;
  max-height: 74vh;
  object-fit: contain;
  display: block;
  margin: 0 auto;
}
.pic-zoom-cap {
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-3);
  text-align: center;
  word-break: break-all;
}
.pic-zoom-close {
  position: absolute;
  top: 18px;
  right: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  color: #fff;
  cursor: pointer;
}
.pic-zoom-close svg {
  width: 16px;
  height: 16px;
}
.pic-zoom-close:hover {
  background: rgba(255, 255, 255, 0.24);
}
/* 信息列：竖排；商品名称给 3 行，超出省略，悬浮 title 展示全名 */
.td-info {
  text-align: center;
}
.info-line {
  line-height: 1.7;
  color: var(--color-text-2);
}
.info-text {
  display: inline-block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
  white-space: nowrap;
}
.name-text {
  display: -webkit-box;
  -webkit-line-clamp: var(--name-lines, 3);
  -webkit-box-orient: vertical;
  white-space: normal;
  word-break: break-all;
  line-height: 1.5;
  width: 100%;
  overflow: hidden;
  text-align: left;
  color: var(--color-text-strong);
  font-weight: 600;
  cursor: help;
}
/* SKU 编码：水平垂直居中 + 复制按钮 */
.td-sku {
  text-align: center;
  vertical-align: middle;
  color: var(--color-text-3);
  font-variant-numeric: tabular-nums;
}
.sku-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  vertical-align: middle;
}
.sku-text {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.copy-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--color-text-3);
  cursor: pointer;
  opacity: 0.8; /* 常驻可见，悬停时完全显形 */
  transition: opacity 0.15s, color 0.15s, background 0.15s, border-color 0.15s;
}
.copy-btn svg {
  width: 14px;
  height: 14px;
  stroke-width: 2.25;
}
/* 悬停行 / 按钮自身时显形 */
.td-sku:hover .copy-btn,
.copy-btn:focus-visible {
  opacity: 1;
}
.copy-btn:hover {
  color: var(--color-brand);
  background: var(--color-surface);
  border-color: var(--color-border-2);
}
.copy-btn.done {
  opacity: 1;
  color: var(--color-success, #10b981);
}
/* 指标列：竖排「城市: 值」 */
.td-metric {
  padding: 7px 10px;
}
.td-nat {
  /* 数据区整体纯白：全国口径列不再另铺浅底（原先 #fafbfc 与白底几乎同色，
     实际也看不出分组），改为靠右对齐 + 单元格内「全国合计」蓝字区分口径。
     --slow-fade 同步改成白，滞销列的截断渐隐才与格底色一致。 */
  --slow-fade: var(--color-surface);
  background: var(--color-surface);
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-strong);
}
.cityline {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  line-height: 1.8;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-2);
}
.cityline .city {
  color: var(--color-text-2);
}
.cityline.total {
  border-top: 1px dashed var(--color-border-3);
  margin-top: 2px;
  padding-top: 2px;
  font-weight: 600;
  color: var(--color-text-strong);
}
/* ---- 现货库存列：三档预警着色（2026-10 改版：整行底色+左条 → 数值着色 + 同色缺口后缀）----
   结构 = 三个固定槽位：城市名 52px / 数值 30px / 后缀 34px（合计 116px + 内边距 20px = 140px 列宽）。
   · 数值槽里预警 / 在途 / 健康是**同一个 .val 元素**，只改颜色与字重
     → 数值与健康值天生同轴，不需要额外的对齐 CSS；
   · 后缀独占 34px 槽、文字居中 → 后缀成列，不随「缺14 / 途9」字宽漂移；
   · 槽位固定 → 所有行的数值宽度与间距完全一致。
   仅作用于 .td-stock（现货库存列），其它每城指标列布局零改动。
   行高：数字沿用行内 1.8em 行框，配合 align-items: baseline 与城市名共享基线
   ——既不撑行高（滞销列等高换算不受影响），数字也不会抬高 1~2px。 */
.td-stock .cityline {
  justify-content: flex-start;
  align-items: baseline;
  gap: 0;
}
.td-stock .cityline .city {
  flex: 0 0 52px;
  width: 52px;
}
.td-stock .cityline .val {
  flex: 0 0 30px;
  width: 30px;
  display: flex;
  justify-content: center;
}
/* 预警：红字加粗；在途：琥珀棕字加粗；健康：沿用原样的中性灰常规字重 */
.td-stock .cityline.cl-alert .val {
  color: #dc2626;
  font-weight: 600;
}
.td-stock .cityline.cl-incoming .val {
  color: #b45309;
  font-weight: 600;
}
.td-stock .cityline .suf {
  flex: 0 0 34px;
  width: 34px;
  text-align: center;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.7;
}
.td-stock .cityline.cl-alert .suf {
  color: #dc2626;
}
.td-stock .cityline.cl-incoming .suf {
  color: #b45309;
}
/* 全国指标列底部的「全国合计」（区别于每城指标的【合计】） */
.cityline.nat-total {
  border-top: 1px dashed var(--color-border-3);
  margin-top: 2px;
  padding-top: 2px;
  font-weight: 600;
  color: var(--color-brand);
}
/* ---- 全国滞销件数：固定高度的城市清单 + 悬浮全量浮层 ---- */
/* 清单可视高度由 JS 按「现货库存列城市数 × 城市行高」换算后内联给出，
   与右侧「现货库存」列（8 城 + 合计）严格等高，多出的城市被裁掉。 */
.slow-list {
  position: relative;
  overflow: hidden;
}
.slow-cell.trunc {
  cursor: help;
}
/* 截断提示：底部渐隐，暗示下面还有城市（仅在真的被裁剪时出现） */
.slow-cell.trunc .slow-list::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 7px;
  pointer-events: none;
  background: linear-gradient(to bottom, rgba(148, 163, 184, 0), var(--slow-fade, var(--color-surface-3)));
}
/* 浮层外壳：只负责定位，overflow 保持可见（内部才有桥接伪元素） */
.slow-tip {
  position: fixed;
  z-index: 1000;
  width: 210px;
}
/* 桥接伪元素：把「单元格 → 浮层」之间的 6px 缝隙纳入可命中区域，
   鼠标横移过去时浮层不会闪退（左右两侧方向由 .flip 切换）。 */
.slow-tip::before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -10px;
  width: 10px;
}
.slow-tip.flip::before {
  left: auto;
  right: -10px;
}
/* 浮层内容：独立滚动区 */
.slow-tip-body {
  overflow-y: auto;
  overscroll-behavior: contain; /* 滚到浮层边界时不把滚动传给页面 */
  padding: 8px 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.18);
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-2);
  font-variant-numeric: tabular-nums;
  text-align: left;
  user-select: text; /* 浮层内可正常选中/复制城市名 */
}
.slow-tip-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.slow-tip-row > span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.slow-tip-row .core {
  color: var(--color-link);
  font-weight: 600;
}
.slow-tip-more {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--color-border-4);
  font-size: 11px;
  color: var(--color-text-5);
}
/* ---- 信息列筛选面板 ---- */
.filter-panel {
  position: fixed;
  z-index: 1000;
  width: 280px;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.16);
  font-size: 12.5px;
  color: var(--color-text-2);
}
.filter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--color-border-4);
  flex: none;
}
.filter-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-strong);
}
.filter-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--color-text-5);
  cursor: pointer;
}
.filter-close svg {
  width: 14px;
  height: 14px;
}
.filter-close:hover {
  color: var(--color-brand);
  background: var(--color-surface-2);
}
.filter-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 4px 12px 8px;
}
.filter-group {
  padding: 8px 0;
  border-bottom: 1px dashed var(--color-border-4);
}
.filter-group:last-child {
  border-bottom: none;
}
.filter-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.filter-group-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text-strong);
}
.filter-link {
  background: none;
  border: none;
  padding: 0;
  font-size: 11.5px;
  color: var(--color-text-5);
  cursor: pointer;
}
.filter-link:hover {
  color: var(--color-brand);
  text-decoration: underline;
}
.filter-items {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 8px;
}
.filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  border-radius: var(--radius-sm);
  color: var(--color-text-3);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, color 0.15s;
}
.filter-item:hover {
  background: var(--color-surface-2);
}
.filter-item.on {
  color: var(--color-text-strong);
  background: var(--color-brand-tint-4, var(--color-surface-2));
}
.filter-item input {
  flex: none;
  accent-color: var(--color-brand);
  cursor: pointer;
}
.filter-item span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.filter-empty {
  font-size: 11.5px;
  color: var(--color-text-5);
  padding: 2px 0;
}
.filter-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border-top: 1px solid var(--color-border-4);
  background: var(--color-surface-3);
  flex: none;
}
.filter-reset {
  background: none;
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 12px;
  color: var(--color-text-3);
  cursor: pointer;
}
.filter-reset:hover {
  color: var(--color-brand);
  border-color: var(--color-brand);
}
.filter-apply {
  background: var(--color-brand);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 5px 14px;
  font-size: 12.5px;
  cursor: pointer;
}
.filter-apply:hover {
  box-shadow: 0 4px 10px rgba(37, 99, 233, 0.28);
}
.tr:hover .td {
  background: var(--color-surface-2);
}
.tr:hover .td-lead {
  background: var(--color-surface-2);
}
/* 行悬停时全国列底色跟随变化，截断渐隐色要同步 */
.tr:hover .td-nat {
  --slow-fade: var(--color-surface-2);
}
.empty {
  text-align: center;
  color: var(--color-text-5);
  padding: 40px 0;
}
/* ---- 合计行 ---- */
/* 合计行是独立的一条浅灰带（比数据区的纯白深一档、比表头的浅蓝浅一档），
   整条统一底色，含全国合计格——原 `.tf .td-nat` 的另铺底色已随数据区一起取消。 */
.tf .td {
  position: sticky;
  bottom: 0;
  background: var(--color-surface-2);
  font-weight: 600;
  color: var(--color-text-strong);
  border-top: 1px solid var(--color-border-3);
}
.tf .td-lead {
  z-index: 1;
  background: var(--color-surface-2);
}
/* ---- 上下柜标签 ---- */
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  line-height: 1.6;
}
.tag.up {
  color: #16a34a;
  background: #ecfdf3;
}
.tag.down {
  color: #dc2626;
  background: #fef2f2;
}
.tag.neutral {
  color: var(--color-text-4);
  background: var(--color-surface-2);
}
</style>
