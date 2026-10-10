// /promo 页的纯逻辑：排序、过滤、差额语义、文案格式化、免责与集中度约束解读。
//
// ⚠️ 为什么单独成模块、而不是写在 PromoSpuTable.vue 的 <script> 里：
//   这些是**可断言的判定**（null 排哪、阈值取多少、什么色）。
//   混进组件就只能靠肉眼看，而肉眼看不出「roi=null 被当成 0 参与排序」
//   「阈值内的小额 delta 被染成加投」这类错 —— 它们在页面上看着完全正常。
//   与 metrics.ts 同一思路：**判定集中在一处，组件只渲染**。
//
// ⚠️ 术语纪律（见 CONTEXT.md）：一律「投产比」，⛔ 不出现「边际ROI」/「亏损」/「预测」。
// ⚠️ 配色纪律：差额用**中性蓝/灰**表达增减，⛔ 不是涨红跌绿。
//   本模块只返回语义名（add / cut / flat），⛔ 不返回色值 ——
//   色值归design token 管，换肤时不该来这里改。
import { fmtCurrency } from "./index";
import type { PromoSpu } from "../types";

/** 档位的展示元信息：标签、说明、配色token 名。 */
export interface QuadMeta {
  /** 中文标签，如「核心高效」 */
  label: string;
  /** 一句话说明这个档意味着什么（tooltip 用） */
  hint: string;
  /** design token 名（如 `--color-brand`）；真实色值由 CSS 变量或 cssVar() 解析 */
  token: string;
}

/** 四象限档位键（= 后端 promo.py 的 QUADRANT_*）。 */
export type PromoQuadrant = NonNullable<PromoSpu["quadrant"]>;

/**
 * 过滤用的档位键：四档 + 「未分档」。
 *
 * ⚠️ 为什么要多一个 `none`：投产比算不出的品（区间内推广花费为 0）
 *    没有档位。⛔ 不能让它在选任何档时都被顺带吞掉 —— 用户会以为数据丢了。
 *    也不能让它混进任一档 —— 那是凭空给它安一个「高效/低效」的结论。
 *    所以它是一档，可以被单独筛出。
 */
export type PromoQuadrantKey = PromoQuadrant | "none";


/**
 * 全部档位的展示元信息（标签 / 说明 / 配色 token）。
 *
 * ⚠️ **单一事实来源**：四象限散点图（PromoQuadrantChart）与 SPU 决策表
 *   （PromoSpuTable）都要用「档位 → 标签 / 颜色」的映射。
 *   两处各写一份必然漂移 —— 会出现「图上点是红的、表里却叫潜力待加」。
 *   ⛔ 不在组件里另写中文枚举映射，也不要在前端重新推导颜色
 *   （色值只写 token 名，真实色值由 CSS 变量或 echarts 侧 cssVar() 解析）。
 *
 * @remarks
 * 配色语义与后端 QUADRANT_* 一一对应：
 *   core      核心高效 = 高投产比 + 高花费 → 品牌红（要保住、要加投）
 *   potential 潜力待加 = 高投产比 + 低花费 → 蓝（值得加预算）
 *   loser     低效吞金 = 低投产比 + 高花费 → 灰（该削减，但不该报警）
 *   trial     观察新品 = 低投产比 + 低花费 → 琥珀（小额观察）
 *   none      未分档   = 投产比算不出     → 弱灰（不是低效，是无依据）
 */
export const QUAD_META: Record<PromoQuadrantKey, QuadMeta> = {
  core: {
    label: "核心高效",
    hint: "投产比高、花费规模大—— 保住并优先加投",
    token: "--color-brand",
  },
  potential: {
    label: "潜力待加",
    hint: "投产比高于本店中位但花钱少 —— 值得加预算",
    token: "--color-link",
  },
  loser: {
    label: "低效吞金",
    hint: "投产比低于本店中位且花钱多 —— 优先削减",
    token: "--color-text-5",
  },
  trial: {
    label: "观察新品",
    hint: "投产比低但花钱少 —— 小额观察或下架",
    token: "--color-amber",
  },
  none: {
    label: "未分档",
    hint: "投产比无法计算（推广花费为 0），不参与四象限分档",
    token: "--color-text-6",
  },
};

/**
 * 取档位的展示元信息。
 *
 * @param {PromoSpu["quadrant"]} quadrant - 后端给的档位；null 表示未分档。
 * @returns {QuadMeta} 标签 / 说明 / 配色 token；未分档返回 `none` 那一份。
 * @throws 无。
 * @example
 * ```ts
 * quadMeta("core").label; // "核心高效"
 * quadMeta(null).label;   // "未分档"
 * ```
 */
export function quadMeta(quadrant: PromoSpu["quadrant"]): QuadMeta {
  return QUAD_META[quadrant ?? "none"];
}

/** 全部过滤维度，固定顺序（chip 组按此渲染）。 */
export const QUADRANT_KEYS: readonly PromoQuadrantKey[] = [
  "core",
  "potential",
  "loser",
  "trial",
  "none",
];

/** 可排序的列键。⛔ 不含「建议文案」—— 那是自由文本，排不出序。 */
export type PromoSortKey = "current_cost" | "suggested_cost" | "delta" | "roi";

/** 排序方向。 */
export type PromoSortDir = "asc" | "desc";

/** 排序状态。 */
export interface PromoSort {
  key: PromoSortKey;
  dir: PromoSortDir;
}

/**
 * 默认排序：按建议分配额降序。
 *
 * ⚠️ 为什么不是「差额绝对值降序」（工单 07 初稿的写法）：
 *   建议分配额是运营真正要执行的那一列——「先加预算的是谁」，
 *   排序就应当服务于执行顺序，而不是服务于「变动幅度最大的是谁」。
 *   差额排序保留为可切换列（表头点一下就能换），两种诉求都满足。
 */
export const DEFAULT_SORT: PromoSort = { key: "suggested_cost", dir: "desc" };

/**
 * 差额判定阈值（元）。
 *
 * ⚠️ 必须与工单 05 汇总条（`delta > 0.005` 记为加投、`< −0.005` 记为减投）同值：
 *   否则同一页上半屏说「建议加投 0 个」、下半屏表格里却有 3 行蓝字，
 *   用户无法判断该信哪个。两处各写一个魔数必然漂移。
 */
export const DELTA_EPS = 0.005;

/**
 * 取某个 SPU 在指定排序键上的数值。
 *
 * @remarks
 * ⚠️ `roi` 为 null 时返回 0 —— 这**不代表它投产比为 0**，
 *    只是为了让类型收敛能参与减法。真正的 null 品在 `sortSpus` 里
 *    已被单独排走，永远走不到这里参与名次竞争。
 *
 * @param {PromoSpu} s - 单个 SPU 明细。
 * @param {PromoSortKey} key - 排序键。
 * @returns {number} 该 SPU 的排序数值。
 * @throws 无。key 超出联合类型时返回 0（TypeScript 已在编译期约束）。
 * @example
 * ```ts
 * sortValue(s, "suggested_cost"); // 4023.91
 * sortValue(s, "delta");           // Math.abs(delta)，注意取的是绝对值
 * ```
 */
function sortValue(s: PromoSpu, key: PromoSortKey): number {
  switch (key) {
    case "current_cost":
      return s.current_cost;
    case "suggested_cost":
      return s.suggested_cost;
    case "delta":
      // ⚠️ 绝对值：-300（减投）比 +50（加投）更该被优先看到，
      //    按带符号值排序会把大额减投甩到最后，等于把最该动的行藏起来。
      return Math.abs(s.delta);
    case "roi":
      return s.roi ?? 0;
  }
}

/**
 * 按指定键排序 SPU 列表（返回**新数组**，不修改入参）。
 *
 * ⚠️ 三条不可让步的规则：
 *   1. ⛔ 不就地排序 —— 调用方通常传`props.analysis.spus`（响应式只读数据），
 *      原地 sort 会污染 props 且不触发更新。
 *   2. `roi = null` 的品**恒排最后**，两个方向都是。理由：它没有分档依据，
 *      若让它参与排序，null 会被当0 处理而落到「投产比最低」那一档，
 *      等于凭空造出一个「最差品」的结论。
 *   3. `delta` 一律按**绝对值**排，理由见 sortValue 的注释。
 *
 * @param {PromoSpu[]} spus - 待排序的 SPU 列表（不会被修改）。
 * @param {PromoSortKey} key - 排序键。
 * @param {PromoSortDir} dir - 排序方向。
 * @returns {PromoSpu[]} 排好序的新数组；入参为空时返回空数组。
 * @throws 无。空数组不崩。
 * @example
 * ```ts
 * sortSpus(spus, "suggested_cost", "desc"); // 建议分配额从大到小
 * sortSpus(spus, "roi", "desc");            // 投产比高的在前，null 的沉底
 * ```
 */
export function sortSpus(
  spus: PromoSpu[],
  key: PromoSortKey,
  dir: PromoSortDir,
): PromoSpu[] {
  const mul = dir === "desc" ? -1 : 1;
  return [...spus].sort((a, b) => {
    if (key === "roi") {
      // ⚠️ 这段必须在方向翻转**之外**：若放进比较值里再乘 mul，
      //    降序时 null 会被翻到最前，等于把「算不出投产比」显示成「投产比最高」。
      const na = a.roi == null;
      const nb = b.roi == null;
      if (na || nb) return na && nb ? 0 : na ? 1 : -1;
    }
    return mul * (sortValue(a, key) - sortValue(b, key));
  });
}

/**
 * 取某个 SPU 的过滤维度键（`quadrant` 为 null 时归入 `none`）。
 *
 * @param {PromoSpu} s - 单个 SPU 明细。
 * @returns {PromoQuadrantKey} 四档之一或 `none`。
 * @throws 无。
 * @example
 * ```ts
 * quadrantKeyOf({ quadrant: "core" });  // "core"
 * quadrantKeyOf({ quadrant: null });    // "none"
 * ```
 */
export function quadrantKeyOf(s: PromoSpu): PromoQuadrantKey {
  return s.quadrant ?? "none";
}

/**
 * 按档位过滤（chip 多选）。
 *
 * @remarks
 * ⚠️ 空选 = **显示全部**，不是显示为空。
 *   「一个都不选」在多选控件里通常意味着「取消筛选」，
 *   而如果渲染成空表，用户会以为该店没有推广数据 —— 这是最容易被误读的一种空态。
 *
 * @param {PromoSpu[]} spus - 全量 SPU 列表（不会被修改）。
 * @param {ReadonlySet<string>} selected - 选中的档位键；空集表示不过滤。
 * @returns {PromoSpu[]} 过滤后的新数组，**保持入参原有顺序**（排序由 sortSpus 负责）。
 * @throws 无。空入参返回空数组。
 * @example
 * ```ts
 * filterByQuadrant(spus, new Set());// 全部
 * filterByQuadrant(spus, new Set(["core", "loser"])); // 只要这两档
 * ```
 */
export function filterByQuadrant(
  spus: PromoSpu[],
  selected: ReadonlySet<string>,
): PromoSpu[] {
  if (selected.size === 0) return [...spus];
  return spus.filter((s) => selected.has(quadrantKeyOf(s)));
}

/**
 * 各档位的 SPU 数量（含 `none` 未分档）。
 *
 * @remarks
 * ⚠️ 存在的意义是让 chip 能「置灰无数据的档」而不是把空档藏起来 ——
 *    四档固定摆出来（其中两个是 0），用户才知道这家店的分布长什么样。
 *
 * @param {PromoSpu[]} spus - 全量 SPU 列表（不会被修改）。
 * @returns {Record<PromoQuadrantKey, number>} 5 个键齐全，无数据的档为 0。
 * @throws 无。
 * @example
 * ```ts
 * quadrantCounts(spus); // { core: 4, potential: 0, loser: 1, trial: 2, none: 0 }
 * ```
 */
export function quadrantCounts(spus: PromoSpu[]): Record<PromoQuadrantKey, number> {
  const out = { core: 0, potential: 0, loser: 0, trial: 0, none: 0 } as Record<
    PromoQuadrantKey,
    number
  >;
  for (const s of spus) out[quadrantKeyOf(s)]++;
  return out;
}

/**
 * 差额的语义档：加投 / 减投 / 不变。
 *
 * @remarks
 * ⚠️ 返回的是**语义名而不是色值**：颜色归 design token（`.tone-add` 用中性蓝、
 *   `.tone-cut` 用中性灰），换肤时不必来改判定逻辑。
 *   ⛔ 不要在这里写「涨红跌绿」——这里的增减是「多花钱/少花钱」，
 *   不是价格涨跌，用红绿会被误读成「亏损/盈利」。
 *
 * @param {number} delta - 建议分配额 − 当前分配额（元）。
 * @returns {"add" | "cut" | "flat"} 三档之一；|delta| ≤ DELTA_EPS 时为 `flat`。
 * @throws 无。NaN 会落进 `flat`（不会返回 undefined 污染 CSS 类名）。
 * @example
 * ```ts
 * deltaTone(1200);   // "add"
 * deltaTone(-300);   // "cut"
 * deltaTone(0.001);  // "flat"
 * ```
 */
export function deltaTone(delta: number): "add" | "cut" | "flat" {
  if (!Number.isFinite(delta) || Math.abs(delta) <= DELTA_EPS) return "flat";
  return delta > 0 ? "add" : "cut";
}

/**
 * 投产比展示文本。
 *
 * @remarks
 * ⚠️⛔ 不可用 `fmtBy("decimal", roi)` 顶替：那条链走的是 `fmtDecimal(null)`
 *    →返回字符串 `"0.00"`，会把「投产比算不出来」显示成「投产比 0」，
 *    进而误导成「这个品完全没投产比效果」。**算不出 ≠ 等于 0**。
 *
 * @param {number | null | undefined} roi - 投产比；null 表示分母为 0 算不出。
 * @returns {string} 有值时为两位小数字符串，否则为 `"--"`。
 * @throws 无。NaN 同样降级为 `"--"`。
 * @example
 * ```ts
 * formatRoi(3.1051); // "3.11"
 * formatRoi(0);      // "0.00"（真实 0，必须与算不出区分开）
 * formatRoi(null);   // "--"
 * ```
 */
export function formatRoi(roi: number | null | undefined): string {
  if (roi == null || Number.isNaN(roi)) return "--";
  return roi.toFixed(2);
}

/**
 * 差额展示文本（带符号金额）。
 *
 * @remarks
 * ⚠️ 阈值内显示破折号 `—` 而不是 `¥0.00`：后者的 0.00 会被读成一个金额，
 *    而这里真正要表达的是「这个品不需要动」。
 *
 * @param {number | null | undefined} delta - 建议 − 当前（元）。
 * @returns {string} 形如 `"+¥1,200.00"` / `"-¥800.50"`；不变时为 `"—"`。
 * @throws 无。NaN 降级为 `"—"`。
 * @example
 * ```ts
 * formatDelta(1200);  // "+¥1,200.00"
 * formatDelta(-800.5); // "-¥800.50"
 * formatDelta(0);     // "—"
 * ```
 */
export function formatDelta(delta: number | null | undefined): string {
  if (delta == null || Number.isNaN(delta)) return "—";
  if (Math.abs(delta) <= DELTA_EPS) return "—";
  return (delta > 0 ? "+" : "-") + fmtCurrency(Math.abs(delta));
}
// ============================================================
// 工单 08：优化模式 —— 免责文案校验 + 集中度约束解读
// ============================================================

/**
 * 单条免责文案的展示元信息（spec §8 的三处硬要求）。
 *
 * ⚠️ **顺序即展示顺序**：后端 `PROMO_DISCLAIMERS` 的数组顺序就是页面顺序，
 *    前端⛔ 不重排（重排会让「线性假设在前、大促在后」这个刻意安排丢掉）。
 *    `keyword` 是这条文案**不可替代**的信息点，用于自检与测试断言：
 *    若某天有人改文案时把关键词删了，断言会红，而不是静悄悄地少讲一件事。
 */
export interface DisclaimerMeta {
  /** 稳定标识（= 后端 DISCLAIMER_KINDS 的对应项），⛔ 不要用文案内容当 key。 */
  kind: "linearity" | "breakeven" | "campaign";
  /** 短标题，用于列表化展示。 */
  title: string;
  /** 该条必须命中的关键词；全部命中才算这条免责「说清了」。 */
  keyword: string;
  /** 视觉层级：warn = 需警觉（大促风险），info = 口径说明。 */
  level: "info" | "warn";
}

/** 三处免责的元信息表，与后端 PROMO_DISCLAIMERS 一一对应。 */
export const DISCLAIMER_META: readonly DisclaimerMeta[] = [
  {
    kind: "linearity",
    title: "线性假设",
    keyword: "线性",
    level: "info",
  },
  {
    kind: "breakeven",
    title: "保本线口径",
    keyword: "毛利",
    level: "info",
  },
  {
    kind: "campaign",
    title: "大促期风险",
    keyword: "大促",
    level: "warn",
  },
];

/** spec §8 要求的免责条数；少一条即视为「缺一不可」被破坏。 */
export const DISCLAIMER_COUNT = 3;

/** ⛔ 全站禁用词。出现在免责文案里说明有人绕过术语纪律改了后端常量。 */
export const BANNED_WORDS = ["边际", "亏损", "盈亏", "预测", "预计达成"] as const;

/** 一条免责文案的展示结果。 */
export interface DisclaimerView {
  meta: DisclaimerMeta;
  text: string;
  /**
   * 该条的关键词是否命中。
   *
   * ⚠️ `false` 不代表功能坏了，而是「这条免责没把该讲的事讲出来」——
   *   必须在页面上显式提示，否则就退化成悄悄少讲一件事。
   */
  ok: boolean;
}

/**
 * 把后端返回的免责文案组装成可展示的列表（并顺带做完整性自检）。
 *
 * ⚠️ 为什么前端还要再校验一遍：后端已是唯一事实来源，但spec §8 把「三处缺一不可」
 *   定为**合规硬要求**，值得在渲染层再兜一次底 —— 后端若被改回单串（老实现），
 *   这里会返回 `allOk: false`，页面可以据此显式报警，而不是只显示一段话。
 *
 * @param {string[] | null | undefined} disclaimers - 后端 `disclaimers` 数组。
 * @returns {{views: DisclaimerView[]; allOk: boolean}} views 按数组顺序，
 *   allOk 表示「恰好三条、每条非空、每条命中自己的关键词」。
 * @example
 * ```ts
 * checkDisclaimers(["线性假设…", "未计入毛利…", "大促期…"]).allOk; // true
 * checkDisclaimers(["线性假设…"]).allOk;                          // false（条数不足）
 * ```
 */
export function checkDisclaimers(
  disclaimers: readonly string[] | null | undefined,
): { views: DisclaimerView[]; allOk: boolean } {
  const list = disclaimers ?? [];
  const views: DisclaimerView[] = list.map((text, i) => {
    // ⚠️ 元信息按**位置**取而非按文本匹配：多出/少掉条目时不会张冠李戴，
    //    缺失的位置直接判定为不合格（text 为空串，页面显示占位）。
    const meta = DISCLAIMER_META[i];
    const t = (text ?? "").trim();
    return {
      meta,
      text: t,
      ok: meta != null && t !== "" && t.includes(meta.keyword),
    };
  });
  const allOk =
    views.length === DISCLAIMER_COUNT &&
    views.every((v) => v.ok) &&
    // ⛔ 文案自身不得含禁用词：即使条数与关键词都对，词一旦出现仍是事故
    list.every((t) => !BANNED_WORDS.some((w) => String(t).includes(w)));
  return { views, allOk };
}

/** 集中度约束的解读结果。 */
export interface CapNotice {
  /** 提示级别：warn = 现状已违规或分不出钱；info = 仅说明规则。 */
  level: "info" | "warn";
  /** 标题，一句话说清发生了什么。 */
  title: string;
  /** 详情，可含金额（已格式化）。 */
  detail: string;
}

/**
 * 生成「单品集中度上限」的解读文案（spec §5.2优化模式要求常驻展示）。
 *
 * ⚠️ 判定完全依据后端给的两个标志（`over_concentrated` / `unallocated`），
 *    ⛔ 前端**不自己算** `current_cost > cap` ——「哪些算超限」是业务口径
 *    （cap = 总预算 × CAP_RATIO），前端再实现一遍必然与后端漂移
 *    （同 `compute_metrics` / `compute_quadrant_thresholds` 纪律）。
 *
 * 三种状态：
 *  - 现状违规（`over_concentrated` 非空）：把注意力引向「这是约束的成本，不是收益」
 *  - 有钱分不出去（`unallocated` > 阈值）：说明这是数学必然而非 bug
 *  - 其余：只陈述规则本身
 *
 * @param {object} args - 输入。
 * @param {string|null|undefined} args.overConcentrated - 后端 `over_concentrated`。
 * @param {number|null|undefined} args.unallocated - 后端 `unallocated`（元）。
 * @param {number|null|undefined} args.cap - 后端 `cap`（单品上限，元）。
 * @param {string} args.capText - 已格式化的 cap 文本（如 `"¥3,184.07"`）。
 * @param {string} args.unallocatedText - 已格式化的 unallocated 文本。
 * @returns {CapNotice} 解读文案。
 * @example
 * ```ts
 * capNotice({ overConcentrated: "1001", unallocated: 0, cap: 3000,
 *             capText: "¥3,000.00", unallocatedText: "¥0.00" }).level; // "warn"
 * ```
 */
export function capNotice(args: {
  overConcentrated?: string | null;
  unallocated?: number | null;
  cap?: number | null;
  capText: string;
  unallocatedText: string;
}): CapNotice {
  const over = args.overConcentrated;
  if (over) {
    return {
      level: "warn",
      title: "现状已超出单品集中度上限",
      detail:
        `${over} 的推广花费已超过单品上限（${args.capText} = 总预算 × 30%）。` +
        `现状本身不满足集中度约束，方案必须把钱挪走 —— 重新分配后总成交会下降，` +
        `这是分散风险的代价，不是收益。`,
    };
  }
  if (args.unallocated != null && args.unallocated > DELTA_EPS) {
    return {
      level: "warn",
      title: "有预算无法分配出去",
      detail:
        `单品预算上限为 ${args.capText}（总预算 × 30%），` +
        `而可分配的 SPU 太少，剩余 ${args.unallocatedText} 无处可去。` +
        `这是数学必然（品数少于 4 个时必然剩钱），不是计算错误。`,
    };
  }
  return {
    level: "info",
    title: "单品预算上限",
    detail:
      `方案要求任一单品的推广花费不超过 ${args.capText}（总预算 × 30%），` +
      `以避免预算过度集中在少数商品上。`,
  };
}
