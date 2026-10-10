// 与 backend/jobs/models.py 手工对齐的 TS 类型（**唯一事实来源是后端的 Pydantic 模型**）。
//
// ⚠️ 改这份文件的同时必须同步后端 backend/jobs/models.py：
//    后端加字段而前端没加，前端会静默拿到 undefined；
//    前端加了而后端没有，字段恒为 undefined（页面显示 --）。二者都要改才算改完。
//
// 通用约定：所有指标字段都是 `number | null`。
//   null 表示「该指标缺失」（页面展示 `--`），与「值为 0」语义完全不同——
//   0 是真实统计到 0，null 是压根没这个数据，不可混用。

/**
 * 单日单 SPU 的指标集合（对应后端 MetricRecord）。
 *
 * @remarks
 * 比率类字段（conversion_rate / avg_price / search_click_rate / roi / promotion_ratio）
 * 由后端按「先求和再相除」算出，前端不要自己再算一遍。
 *
 * @example
 * ```ts
 * const m: MetricRecord = { ...空指标对象, visitors: 100, buyers: 10 };
 * m.conversion_rate; // 0.1（后端已算好）
 * ```
 */
export interface MetricRecord {
  /** 商品访客数 */
  visitors: number | null;
  /** 成交客户数 */
  buyers: number | null;
  /** 成交单量 */
  orders: number | null;
  /** 成交商品件数 */
  items: number | null;
  /** 成交金额 */
  amount: number | null;
  /** 客单价 = 成交金额 / 成交客户数 */
  avg_price: number | null;
  /** 搜索曝光次数 */
  search_impressions: number | null;
  /** 搜索点击次数（仅作为 search_click_rate 的分子，不单独展示） */
  search_clicks: number | null;
  /** 搜索点击率 = 搜索点击次数 / 搜索曝光次数 */
  search_click_rate: number | null;
  /** 推广花费（推广表「花费」列） */
  promotion_cost: number | null;
  /** 推广成交金额（推广表「总订单金额」列） */
  promotion_amount: number | null;
  /** 成交转化率 = 成交客户数 / 商品访客数 */
  conversion_rate: number | null;
  /** ROI = 推广成交金额 / 推广花费 */
  roi: number | null;
  /** 推广占比 = 推广花费 / 成交金额 */
  promotion_ratio: number | null;
  /** 推广净收益 = 推广成交金额 − 推广花费 */
  promo_profit: number | null;
  /** 取消及售后退款单量 */
  refund_orders: number | null;
  /** 取消及售后退款金额 */
  refund_amount: number | null;
}

/**
 * 单 SPU 在某一日的指标快照（区间模式下铺成「指标 × 日期」矩阵的一列）。
 *
 * @example
 * ```ts
 * const d: DayMetric = { date: "2026-09-24", metrics: {} as MetricRecord };
 * ```
 */
export interface DayMetric {
  /** 日期，格式 YYYY-MM-DD */
  date: string;
  /** 当日指标；缺失日的所有字段为 null */
  metrics: MetricRecord;
}

/**
 * 扁平化的表格行（一个 SPU 一行）。
 *
 * @remarks
 * 两种模式：
 * - 单日模式：`date` 为该日、`metrics` 等于 `days[0].metrics`，`days` 只有 1 项；
 * - 区间模式：`date` 为空串、`metrics` 全 null，真实数据全在 `days` 里。
 *
 * @example
 * ```ts
 * // 区间模式取某天的值：
 * const cell = row.days.find((d) => d.date === "2026-09-24")?.metrics;
 * ```
 */
export interface Row {
  /** 店铺名 */
  shop: string;
  /** SPU 编号 */
  spu: string;
  /** 商品名称；映射表缺失时为 null，前端降级显示 SPU 号 */
  spu_name: string | null;
  /** 类目 */
  category: string | null;
  /** 图片访问路径，如 "/images/100123.png" */
  image: string | null;
  /** 单日模式=该日；区间模式为空串（改用 days） */
  date: string;
  /** 单日模式=days[0]；区间模式全 null（改用 days） */
  metrics: MetricRecord;
  /** 区间模式核心：该 SPU 在所选日期范围内逐日数据（含缺失日，缺失日指标全 null） */
  days: DayMetric[];
}

/**
 * 分页结果（供前端无限滚动 / 翻页消费）。
 */
export interface PagedRows {
  /** 过滤后的**总**条数（不是当页条数） */
  total: number;
  /** 当前页码，从 1 开始 */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 当页行列表 */
  rows: Row[];
}

/**
 * 按「店铺 + 日期」预聚合的汇总行。
 *
 * @remarks
 * date 字段形如 `"钻芯旗舰店|2026-09-24"`——后端把店铺前缀塞进 date，
 * 是为了让所有店铺共用同一个 summary 文件，前端按当前店铺前缀过滤后再拆出日期。
 */
export interface DailySummary extends MetricRecord {
  /** "店铺|YYYY-MM-DD" 形式的复合日期键 */
  date: string;
}

/**
 * TOP 榜单里的一条 SPU 记录（按成交金额排序）。
 */
export interface TopSpu {
  /** SPU 编号 */
  spu: string;
  /** 商品名称，可为空 */
  spu_name: string | null;
  /** 所属店铺名 */
  shop: string;
  /** 图片访问路径，可为空 */
  image: string | null;
  /** 区间累计成交金额 */
  amount: number;
  /** 区间累计访客数 */
  visitors: number;
  /** 区间累计客户数 */
  buyers: number;
  /** 区间累计商品件数 */
  items: number;
}

/**
 * 单个店铺的区间汇总（饼图展示店铺占比用）。
 */
export interface ShopSummary {
  /** 店铺名 */
  shop: string;
  /** 区间累计成交金额 */
  amount: number;
  /** 区间累计访客数 */
  visitors: number;
  /** 区间累计客户数 */
  buyers: number;
  /** 区间累计商品件数 */
  items: number;
}

/**
 * 模块汇总（图表用）。
 */
export interface ModuleSummary {
  /** 模块标识 */
  module_id: string;
  /** 生成时间（ISO 字符串） */
  updated_at: string;
  /** 按「店铺+日期」的逐日汇总 */
  daily: DailySummary[];
  /** TOP SPU 榜单 */
  top_spus: TopSpu[];
  /** 各店铺区间汇总（饼图用） */
  shop_totals: ShopSummary[];
}

/**
 * manifest 中的单个模块条目。
 */
export interface ModuleManifest {
  /** 模块标识（同时是前端路由参数） */
  module_id: string;
  /** 模块中文名 */
  title: string;
  /** 模块说明 */
  description: string;
  /** 该模块包含的店铺名列表 */
  shops: string[];
  /** 数据覆盖的日期区间 [最早日, 最晚日]；无数据时为空数组 */
  date_range: string[];
  /** SPU 数量（按 shop+spu 去重） */
  spu_count: number;
  /** 明细行数 */
  row_count: number;
  /** 该模块数据更新时间；从未更新过为 null */
  updated_at: string | null;
}

/**
 * 全局模块清单（前端首屏第一个接口 /api/manifest 的返回）。
 */
export interface Manifest {
  /** manifest 生成时间（ISO 字符串） */
  generated_at: string;
  /** 各模块条目 */
  modules: ModuleManifest[];
}

/**
 * 批处理运行状态（/api/status 与 /api/refresh 的返回）。
 */
export interface BatchStatus {
  /** 上次运行开始时间；从未运行过为 null */
  last_run_at: string | null;
  /** 是否成功 */
  success: boolean;
  /** 实际执行次数（含首次） */
  attempts: number;
  /** 总耗时秒数 */
  duration_seconds: number;
  /** 结果说明；失败时含最后一次异常信息 */
  message: string;
  /** 本次使用的数据源目录（排查「读错目录」用） */
  source_dir?: string | null;
  /** 成功时产出的模块 id 列表 */
  modules?: string[];
}

/** 指标 key 的联合类型，由 MetricRecord 的字段名派生 */
export type MetricKey = keyof MetricRecord;

// ---------- SPU 单品分析 ----------
/**
 * 单品分析里的单日点。
 */
export interface SpuDailyPoint {
  /** 日期 YYYY-MM-DD */
  date: string;
  /** 该日是否按节假日口径统计（用于前端区分工作日/节假日配色） */
  is_holiday: boolean;
  /** 当日指标（已按该 SPU 的各店铺求和，比率已重算） */
  metrics: MetricRecord;
}

/**
 * 工作日组 / 节假日组的日均统计（对应后端 analysis.GroupStats）。
 *
 * @remarks
 * 比率类字段均为「先求和再相除」得出，不是各日比率的平均；
 * 分母为 0 时为 null（页面展示 `--`）。
 */
export interface CompareGroup {
  /** 该组天数；为 0 时各项日均按 0 处理 */
  days: number;
  /** 日均访客数 */
  visitors_avg: number;
  /** 日均客户数 */
  buyers_avg: number;
  /** 日均单量 */
  orders_avg: number;
  /** 日均件数 */
  items_avg: number;
  /** 日均成交金额 */
  amount_avg: number;
  /** 客单价；无客户时为 null */
  avg_price: number | null;
  /** 日均搜索曝光 */
  search_impressions_avg: number;
  /** 搜索点击率；无曝光时为 null */
  search_click_rate: number | null;
  /** 日均退款单量 */
  refund_orders_avg: number;
  /** 日均退款金额 */
  refund_amount_avg: number;
  /** 成交转化率；无访客时为 null */
  conversion_rate: number | null;
  /** 日均推广花费；无推广数据时为 null */
  promotion_cost_avg: number | null;
  /** 日均推广成交金额；无推广数据时为 null */
  promotion_amount_avg: number | null;
  /** ROI；无推广花费时为 null */
  roi: number | null;
  /** 推广占比；无成交时为 null */
  promotion_ratio: number | null;
}

/**
 * 单品分析接口的完整返回（/api/.../analysis）。
 *
 * @example
 * ```ts
 * const a: SpuAnalysis = await fetchSpuAnalysis("pop_spu_detail", "100123");
 * a.daily[0].metrics.amount; // 首日成交金额
 * a.insight;                 // 自动生成的分析文案
 * ```
 */
export interface SpuAnalysis {
  /** SPU 编号 */
  spu: string;
  /** 商品名称，可为空 */
  spu_name: string | null;
  /** 类目，可为空 */
  category: string | null;
  /** 图片访问路径，可为空 */
  image: string | null;
  /** 该 SPU 出现的店铺列表 */
  shops: string[];
  /** 实际统计区间 [最早日, 最晚日] */
  date_range: string[];
  /** 逐日指标点 */
  daily: SpuDailyPoint[];
  /** 工作日组统计 */
  workday: CompareGroup;
  /** 节假日组统计 */
  holiday: CompareGroup;
  /** 自动生成的综合分析文案 */
  insight: string;
}

/**
 * 推广分析里的单个 SPU 条目（`/api/promo/analysis` 响应，字段与后端 `PromoSpu` 一一对应）。
 *
 * ⚠️ 术语：一律「投产比」，⛔ 不出现「边际ROI」——边际 ROI 需要反事实数据
 *    （关掉某 SPU 的推广会怎样），平台不提供，本项目算不出来。
 */
export interface PromoSpu {
  /** SPU 编号 */
  spu: string;
  /** 商品名称，可为空 */
  spu_name: string | null;
  /** 图片访问路径，可为空 */
  image: string | null;
  /** 区间内 Σ推广花费（元） */
  current_cost: number;
  /** 区间内 Σ推广成交金额（元） */
  current_amount: number;
  /** 投产比 = Σ成交 ÷ Σ花费（先求和再相除）；分母 0 时为 null */
  roi: number | null;
  /** 推广净收益 = Σ成交 − Σ花费（未计毛利，不代表利润） */
  profit: number;
  /** 日均推广花费占比 ∈ [0,1]；= Σ(该品当日花费 ÷ 该店当日总花费) ÷ 天数 */
  cost_share: number | null;
  /** 日均推广成交占比，计算方式同上 */
  amount_share: number | null;
  /** 逐日投产比的变异系数；无法计算时 null */
  roi_cv: number | null;
  /** 波动过大（由后端两级规则判定：绝对兜底 1.0 + 店内 P70 分位） */
  unstable: boolean;
  /** 贪心算法给出的建议分配额（元） */
  suggested_cost: number;
  /** 建议 − 当前 = suggested_cost − current_cost（正=加投、负=减投） */
  delta: number;
  /** 四象限分档；roi 不可算时为 null */
  quadrant: "core" | "potential" | "loser" | "trial" | null;
  /** 后端生成的一句话建议，前端直接展示，勿在前端另写一份 */
  advice: string;
  /** True 表示未参与分配（无推广花费 / 投产比无法计算） */
  skipped: boolean;
  /** 未参与分配的原因文案 */
  skip_reason: string | null;
}

/**
 * 空态原因（后端判定，前端只渲染、不重复判断）。
 *
 * ⚠️ 三种空态的**处置方式不同**，不能用「有数据 / 没数据」的布尔表达：
 *   - `no_data`：提示换区间即可；
 *   - `too_few_spus`：**弱化展示而非报错** —— 贪心分配仍可执行（1 个 SPU 时结果就是它自己，
 *     只是填不满预算），只是四象限分位数不可信；
 *   - `all_zero`：投产比算不出来，提示换有花费的区间。
 * 把 `too_few_spus` 误当成错误处理掉是最容易犯的错。
 */
export type PromoEmptyReason = "no_data" | "too_few_spus" | "all_zero";

/**
 * 推广预算优化分析的完整返回体（`/api/promo/analysis`）。
 */
export interface PromoAnalysis {
  /** 本次分析使用的店铺 */
  shop: string;
  /** 实际统计区间起点 */
  start: string;
  /** 实际统计区间终点 */
  end: string;
  /** 数据中实际命中的日期区间（可能窄于请求区间） */
  date_range: string[];
  /** 区间内该店 Σ推广花费 */
  total_cost: number;
  /** 区间内该店 Σ推广成交金额 */
  total_amount: number;
  /** 当前分配下的推广成交总额 */
  current_total_amount: number;
  /** 贪心理论上限总成交（线性假设下的数学最优，**不可达成**） */
  ideal_total_amount: number;
  /** 保守估计 = ideal × BACKTEST_DISCOUNT(0.7) */
  conservative_total: number;
  /** 单品预算上限 = total_cost × CAP_RATIO(0.3) */
  cap: number;
  /** 因单品上限封顶而分不出去的预算（SPU 数少时 > 0） */
  unallocated: number;
  /** 可优化空间 = ideal − current */
  gap: number;
  /** 达成率 = current ÷ ideal；分母 0 时 null */
  achievement_rate: number | null;
  /** False 表示 SPU 数不足 4 个，分档不可信，前端应弱化四象限 */
  quadrant_reliable: boolean;
  /**
   * 四象限两条分割线的位置（投产比阈值、花费阈值，均为店内分位数 P50）。
   *
   * ⚠️ **必须直接用后端给的值，⛔ 不要在前端从 `spus` 重算分位数**：
   *    分割线是「分档结论的图示」。前端若自己实现一遍（哪怕公式看起来一样），
   *    插值约定或样本筛选一旦有差，就会出现「点被染成高效档却在分割线下方」
   *    的自相矛盾画面，且只在特定数据下偶发，极难排查。
   *    与 `aggregateMetrics` 同一纪律：口径单一事实来源在后端，前端只渲染。
   *    无可分档样本时为 null，此时图上不画分割线。
   */
  roi_threshold: number | null;
  cost_threshold: number | null;
  /**
   * 现状已超单品集中度上限（30%）的 SPU 标识（多个用「、」连接）；null = 现状本身可行。
   *
   * ⚠️ **非 null 时 `gap` 必然为负、`achievement_rate` 必然 > 1** —— 这不是算法出错，
   *    而是「现状把大部分预算压在少数品上，30% 上限要求把钱挪走」的必然结果。
   *    页面此时**必须改文案与配色**（说清「重新分配会让总成交下降」），
   *    ⛔ 不要照字面渲染成「可优化空间 −1,735 元」「达成率 208%」。
   */
  over_concentrated: string | null;
  /** 各 SPU 明细（按建议分配额降序） */
  spus: PromoSpu[];
  /** 空态原因；数据完整时为 null */
  empty_reason: PromoEmptyReason | null;
  /**
   * 免责文案（spec §8 三处硬要求），前端必须**逐条**展示。
   *
   * ⚠️ 是数组不是单串：单串拼接后无法断言「三处缺一不可」（删掉中间一条仍能grep 到首尾关键词）。
   * ⛔ 不要在前端补第四段免责 —— 那是第二个事实来源，改一处忘另一处必然漂移。
   */
  disclaimers: string[];
}
