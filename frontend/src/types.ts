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
