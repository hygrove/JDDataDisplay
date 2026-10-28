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

// ---------- 库存管理模块 ----------
/** 库存单元格：数值（REAL）或文本（TEXT），缺失/空值可能为中文或 null。 */
export type InvCell = number | string | null;

/**
 * 单个库存指标的元信息（来自后端 inventory_metrics.build_metrics，前端不写死）。
 *
 * @remarks
 * kind 决定列渲染方式：
 * - `identity` 身份列（商品名称/SPU/类目/上下柜/销售员），锁定恒可见；
 * - `per_city` 每城指标，渲染成「8 城子列」；
 * - `national` 全国指标，单列。
 *
 * fields 为对应 DB 字段名数组：per_city 长度 8（按 CITIES 顺序）、national/identity 长度 1。
 */
export interface InventoryMetric {
  /** 指标唯一标识（city_<suffix> / 字段名） */
  key: string;
  /** 展示标题 */
  title: string;
  /** 指标类型 */
  kind: "identity" | "per_city" | "national";
  /** 涉及的 DB 字段名数组 */
  fields: string[];
  /** 开页是否默认可见 */
  default_visible: boolean;
  /** 是否锁定（身份列恒可见、不可勾选） */
  locked: boolean;
  /** 合计行是否对该指标求和（不合计则显 "—"） */
  aggregatable: boolean;
}

/**
 * 库存模块首屏元信息（/api/inventory/meta）。
 */
export interface InventoryMeta {
  /** 全部指标（含 identity/per_city/national） */
  metrics: InventoryMetric[];
  /** 城市白名单（按展示顺序） */
  cities: string[];
  /** 可选店铺列表 */
  shops: string[];
  /** 可选快照日期列表（降序） */
  dates: string[];
  /**
   * 每店各自最新的快照日期：{店铺: "YYYY-MM-DD"}。
   *
   * @remarks
   * 库存按「每店保留自己最新一份」淘汰（ADR-0015），**各店日期可能不同**——
   * 某店当天漏下载时它停在上一份。故前端按店分别标注日期，不给跨店单一「数据截至」。
   */
  shop_dates: Record<string, string>;
}

/**
 * 库存行 = 全列 dict + 可选的全城市滞销明细（后端按行附带）。
 *
 * @remarks
 * `slow_cities` 来自 inventory_city_slow 长表：城市名 → 滞销件数（只含 >0 的城市，
 * 约 70 个城市/仓，不限于 8 城白名单）。仅「全国滞销件数」列使用，
 * 普通行不携带该字段（后端按需附加，控制响应体）。
 *
 * `sku_pic` 是库存 SKU 商品图的访问路径，命中图片时才有值（详见下方字段注释）。
 */
export type InvRow = Record<string, InvCell> & {
  /** 全城市滞销明细：城市名 → 件数（仅 >0） */
  slow_cities?: Record<string, number>;
  /**
   * 库存 SKU 商品图访问路径（后端建索引后随行下发），
   * 形如 "/inv-pics/jpzy/100080186911.jpg"。仅当该 SKU 在
   * `ResourceData/inventory_sku_pic` 下命中图片时存在；无图行不携带该字段，
   * 前端渲染「暂无图片」占位。
   */
  sku_pic?: string;
};

/**
 * 全城市滞销聚合项（/api/inventory/slow-cities）。
 */
export interface InventorySlowCity {
  /** 城市/仓名（含衍生名，如「广州补货B」） */
  city: string;
  /** 该城市滞销件数合计 */
  units: number;
  /** 是否属于 8 城白名单（前端可高亮） */
  core: boolean;
}

/**
 * 结构分布（/api/inventory/distributions）：概览卡的唯一数据源。
 *
 * @remarks
 * 统计单位为去重后的 (店铺, SKU)：多天快照下同一 SKU 只计一次，
 * 取最新快照的上下柜 / 类目 / 销售员属性。
 */
export interface InventoryDistributions {
  /** 统计范围概览 */
  scope: {
    /** 店铺列表（按首次出现顺序） */
    shops: string[];
    /** 全范围去重 SKU 数 */
    sku_total: number;
    /** 各店 SKU 数之和（同一 SKU 跨店会重复计） */
    sku_rows: number;
    /** 明细行数（含多天快照） */
    detail_rows: number;
  };
  /** 维度 1：SKU 覆盖（总数 + 店铺细分） */
  sku: {
    /** 全范围去重 SKU 数 */
    total: number;
    /** 每店 SKU 数与占比 */
    by_shop: { shop: string; sku_count: number; share: number }[];
  };
  /** 维度 2：上下柜状态（总数 + 店铺分布） */
  shelf: {
    /** 全范围状态 → SKU 数 */
    total: Record<string, number>;
    /** 每店的状态拆分 */
    by_shop: { shop: string; shelf: Record<string, number>; total: number }[];
  };
  /** 维度 3：三级类目（含每店数量） */
  category: {
    /** 类目个数 */
    count: number;
    /** 类目列表（按 SKU 数降序） */
    items: {
      category: string;
      sku_count: number;
      /** 覆盖店铺数 */
      shop_count: number;
      /** 店铺 → 该店该类目 SKU 数 */
      by_shop: Record<string, number>;
    }[];
  };
  /** 维度 4：销售员（按店铺分布） */
  seller: {
    /** 跨店去重的人数 */
    person_count: number;
    /** 人次（店铺 × 销售员）之和 */
    person_times: number;
    /** 销售员列表（按 SKU 数降序） */
    items: {
      seller: string;
      sku_count: number;
      /** 服务店铺数 */
      shop_count: number;
      /** 店铺 → 该店该销售员 SKU 数 */
      by_shop: Record<string, number>;
    }[];
    /** 每店人数与人员明细 */
    by_shop: {
      shop: string;
      seller_count: number;
      sellers: { seller: string; sku_count: number }[];
    }[];
  };
}

/**
 * 库存分页明细响应（/api/inventory/rows）。
 *
 * @remarks
 * rows 为「全列 dict」——每个元素含 inventory_daily 的全部列（含 8 城字段）；
 * 前端按 visibleMetrics 投影成矩阵列，后端不负责挑列，保证单一事实来源在 inventory_metrics。
 *
 * city_totals 为「每城指标 → 每城求和」数组：下标 i 对应 cities[i]，
 * 仅供矩阵表每城子列底部合计使用（与列一一对应）。
 */
export interface InventoryPagedRows {
  /** 过滤后总行数 */
  total: number;
  /** 当前页码，从 1 开始 */
  page: number;
  /** 每页条数 */
  page_size: number;
  /** 当页行（全列 dict，全国滞销行附带 slow_cities） */
  rows: InvRow[];
  /** 按指标 key 的合计（per_city 为 8 城求和的单一值；national 单列合计；identity 为 null） */
  totals: Record<string, number | null>;
  /** 每城指标 → 每城求和数组（下标对应 cities） */
  city_totals: Record<string, number[]>;
}

/**
 * 库存 KPI 聚合（/api/inventory/summary）。
 */
export interface InventorySummary {
  /** SKU 数 */
  total_skus: number;
  /** 现货库存总量（8 城求和） */
  stock: number;
  /** 滞销件数（8 城求和） */
  slow: number;
  /** 采购未到货（8 城求和） */
  purchase: number;
  /** 近 30 日出库（8 城求和） */
  out_30: number;
  /** 全国滞销件数 */
  nat_slow: number;
}

/**
 * 信息列筛选条件（多选；空数组 = 不限）。
 *
 * @remarks
 * 对应矩阵表「信息」列里的三个身份字段：三级类目 / 销售员 / 上下柜状态。
 * 值为后端 inventory_daily 的原始字段值（空值统一用「未标注」表示）。
 */
export interface InventoryFilters {
  /** 三级类目 */
  category: string[];
  /** 销售员 */
  seller: string[];
  /** 上下柜状态 */
  shelf_status: string[];
}

/**
 * 信息列筛选项候选（由结构分布聚合而来）。
 */
export interface InventoryFilterOptions {
  /** 三级类目候选 */
  categories: string[];
  /** 销售员候选 */
  sellers: string[];
  /** 上下柜状态候选 */
  shelf_statuses: string[];
}

/**
 * 库存明细查询参数。
 */
export interface InventoryRowsQuery {
  /** 店铺名；留空 = 全部店铺 */
  shop?: string;
  /** 快照日期 YYYY-MM-DD；留空 = 每店各自最新（ADR-0015 per-shop 口径） */
  date?: string;
  /** SKU / 商品名称 / SPU 模糊搜索 */
  keyword?: string;
  /** 三级类目筛选（多选） */
  category?: string[];
  /** 销售员筛选（多选） */
  seller?: string[];
  /** 上下柜状态筛选（多选） */
  shelf_status?: string[];
  /** 排序指标 key（inventory_metrics 的 key，如 `city_stock`）；留空 = 不排序 */
  sort_by?: string;
  /** 排序方向：desc（默认） / asc */
  sort_order?: "asc" | "desc";
  /** 页码，从 1 开始 */
  page: number;
  /** 每页条数，最大 500 */
  page_size: number;
}

/* ============================== 库存预警与补货建议 ============================== */

/**
 * 预警三档状态（与 backend.jobs.alert_rules 对齐）。
 *
 * - healthy：现货 ≥ 城市阈值
 * - incoming：现货 < 阈值 ≤ 现货+在途（在途可补足）
 * - alert：现货+在途 < 阈值（在途也填不上窟窿）
 * 下柜 SKU 由后端排除，不出现在计数与清单里。
 */
export type AlertStatus = "healthy" | "incoming" | "alert";

/**
 * 单城市预警汇总（/api/inventory/alerts/summary 的 cities 数组项）。
 */
export interface AlertCityStat {
  /** 城市中文名（与 meta.cities 同序同值） */
  city: string;
  /** 城市码（bj/wh/sh/gz/cd/xa/dz/sy，与 DB 字段前缀同源） */
  code: string;
  /** 城市级阈值（所有 SKU 统一生效；0 = 该城不检测） */
  threshold: number;
  /** 预警计数（现货+在途 < 阈值） */
  red: number;
  /** 在途计数（现货不足但在途可补足） */
  yellow: number;
  /** 健康计数 */
  green: number;
  /** 该城建议补货合计（默认参数 9 天 / 20% / 14 日） */
  restock_total: number;
}

/**
 * 8 城预警汇总响应。
 */
export interface InventoryAlertsSummary {
  /** 计算所用快照日期（请求未传时 = 最新快照） */
  date: string;
  /** 8 城汇总（顺序与 meta.cities 一致） */
  cities: AlertCityStat[];
  /** 建议补货合计所用参数 */
  params: RestockParams;
}

/**
 * 补货算法参数（后端夹取后回显）。
 */
export interface RestockParams {
  /** 覆盖天数（默认 9：每周采购一次，7 天周期 + 到货缓冲） */
  days: number;
  /** 安全系数（默认 0.2 = 20%） */
  safety: number;
  /** 销速窗口天数（7/14/30，默认 14） */
  window: number;
  /** 可信最少有货天数：不足则销速按参照收缩（页面标 ≈） */
  cred_min: number;
  /** 突发封顶倍数：短窗销速最多放大参照销速的多少倍 */
  burst_cap: number;
  /** 参照窗天数（收缩估算的历史基准） */
  ref_window: number;
  /** 销速算法演示算例（后端用真实函数算出，对话框只渲染不算） */
  algo_example: Record<string, number> | null;
}

/**
 * 补货建议单行（某城市某 SKU）。
 */
export interface RestockRow {
  /** SKU 编码 */
  sku: string | null;
  /** 商品名称 */
  sku_name: string | null;
  /** 店铺名 */
  shop: string | null;
  /** 城市名（SKU 视图里用它排版；城市视图里分组头已经写了城市） */
  city: string;
  /** 三级类目 */
  category: string | null;
  /** 上下柜状态（本清单只含上柜） */
  shelf_status: string | null;
  /** SPU 编码（同款不同规格的 SKU 共用） */
  spu: string | null;
  /** 销售员名称 */
  seller: string | null;
  /** ERP 编码（未登记时为 null → 页面显示「--」；库里不写 0，见 ADR-0009） */
  erp_code: string | null;
  /** 商品图访问路径（无图时为 null） */
  sku_pic: string | null;
  /** 该城预计可售天数（率值，不参与任何合计） */
  sellable_days: number | null;
  /** 现货库存 */
  stock: number;
  /** 采购未到货（在途） */
  purchase: number;
  /** 窗口出库件数（销速的分子；=0 表示该窗口没有任何出库记录） */
  out: number;
  /** 有效日均销速（件/天；有货天数不足可信门槛时为 30 日参照收缩估算值，页面标 ≈） */
  velocity: number;
  /** 窗口有货天数（销速的分母；0 = 整窗缺货，无销速依据） */
  avail: number;
  /** 销速估计方式：none=无依据(0) / measured=直接测得 / shrunk=收缩估算 */
  velocity_kind: "none" | "measured" | "shrunk";
  /** 库存缺口关注档位（""=不关注 / "关注"），覆盖率 + 缺货损失双锚判定 */
  gap_flag: string;
  /** 估算缺货损失（件）：缺货期被压住的需求 = 销速 × 缺货天数 */
  lost_sales: number;
  /** 目标库存（销速 × days × (1+safety)，取整） */
  target: number;
  /** 建议补货量（target − 现货 − 在途，向上取整） */
  qty: number;
  /** 该城市当前预警档位（healthy/incoming/alert） */
  status: AlertStatus;
}

/**
 * 按城市分组的补货建议。
 */
export interface RestockGroup {
  /** 城市中文名 */
  city: string;
  /** 城市码 */
  code: string;
  /** 该城阈值（展示参考） */
  threshold: number;
  /** 建议行（qty 降序） */
  rows: RestockRow[];
  /** 该城建议量小计 */
  subtotal: number;
}

/**
 * 补货建议清单响应（/api/inventory/restock）。
 */
export interface RestockResponse {
  /** 视图标识：城市视图 */
  view: "city";
  /** 计算所用快照日期 */
  date: string;
  /** 本次计算实际生效的参数（非法值已被后端夹回默认） */
  params: RestockParams;
  /** 按城市分组（仅含有建议行的城市，按 CITIES 顺序） */
  groups: RestockGroup[];
  /** 8 城建议量总计（本周建议采购总量） */
  grand_total: number;
}

/** SKU 视图：平铺长表，一行一个 SKU × 城市。 */
export interface RestockSkuResponse {
  /** 视图标识：SKU 视图 */
  view: "sku";
  /** 计算所用快照日期 */
  date: string;
  /** 本次计算实际生效的参数 */
  params: RestockParams;
  /** 平铺行（按 SKU 编码升序、同 SKU 按城市白名单顺序） */
  rows: RestockRow[];
  /** 建议量总计（与城市视图的 grand_total 同口径） */
  grand_total: number;
  /** 涉及的 SKU 个数 */
  sku_count: number;
}

/** 补货清单响应（两个视图的联合类型）。 */
export type RestockListResponse = RestockResponse | RestockSkuResponse;
