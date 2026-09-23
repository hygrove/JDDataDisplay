// 与 backend/jobs/models.py 手工对齐的 TS 类型（唯一事实来源是 Pydantic 模型）

export interface MetricRecord {
  visitors: number | null; // 商品访客数
  buyers: number | null; // 成交客户数
  orders: number | null; // 成交单量
  items: number | null; // 成交商品件数
  amount: number | null; // 成交金额
  avg_price: number | null; // 客单价 = 成交金额 / 成交客户数
  search_impressions: number | null; // 搜索曝光次数
  search_clicks: number | null; // 搜索点击次数（用于重算点击率，不单独展示）
  search_click_rate: number | null; // 搜索点击率 = 搜索点击次数 / 搜索曝光次数
  promotion_cost: number | null; // 推广花费（推广表「花费」）
  promotion_amount: number | null; // 推广成交金额（推广表「总订单金额」）
  conversion_rate: number | null; // 成交转化率 = 成交客户数 / 商品访客数
  roi: number | null; // ROI = 推广成交金额 / 推广花费
  promotion_ratio: number | null; // 推广占比 = 推广花费 / 成交金额
  refund_orders: number | null; // 取消及售后退款单量
  refund_amount: number | null; // 取消及售后退款金额
}

// 单 SPU 在某一日的指标快照（区间模式下铺成「指标×日期」矩阵）
export interface DayMetric {
  date: string; // YYYY-MM-DD
  metrics: MetricRecord;
}

export interface Row {
  shop: string;
  spu: string;
  spu_name: string | null;
  category: string | null;
  image: string | null;
  date: string; // 单日模式=该日；区间模式为空串（改用 days）
  metrics: MetricRecord; // 单日模式=days[0]；区间模式全 None（改用 days）
  days: DayMetric[]; // 区间模式核心：该 SPU 在所选日期范围内逐日数据（含缺失日）
}

export interface PagedRows {
  total: number;
  page: number;
  page_size: number;
  rows: Row[];
}

export interface DailySummary extends MetricRecord {
  date: string; // "店铺|YYYY-MM-DD"
}

export interface TopSpu {
  spu: string;
  spu_name: string | null;
  shop: string;
  image: string | null;
  amount: number;
  visitors: number;
  buyers: number;
  items: number;
}

export interface ShopSummary {
  shop: string;
  amount: number;
  visitors: number;
  buyers: number;
  items: number;
}

export interface ModuleSummary {
  module_id: string;
  updated_at: string;
  daily: DailySummary[];
  top_spus: TopSpu[];
  shop_totals: ShopSummary[];
}

export interface ModuleManifest {
  module_id: string;
  title: string;
  description: string;
  shops: string[];
  date_range: string[];
  spu_count: number;
  row_count: number;
  updated_at: string | null;
}

export interface Manifest {
  generated_at: string;
  modules: ModuleManifest[];
}

export interface BatchStatus {
  last_run_at: string | null;
  success: boolean;
  attempts: number;
  duration_seconds: number;
  message: string;
  source_dir?: string | null;
  modules?: string[];
}

export type MetricKey = keyof MetricRecord;

// ---------- SPU 单品分析 ----------
export interface SpuDailyPoint {
  date: string;
  is_holiday: boolean;
  metrics: MetricRecord;
}

export interface CompareGroup {
  days: number;
  visitors_avg: number;
  buyers_avg: number;
  orders_avg: number;
  items_avg: number;
  amount_avg: number;
  avg_price: number | null;
  search_impressions_avg: number;
  search_click_rate: number | null;
  refund_orders_avg: number;
  refund_amount_avg: number;
  conversion_rate: number | null;
  promotion_cost_avg: number | null;
  promotion_amount_avg: number | null;
  roi: number | null;
  promotion_ratio: number | null;
}

export interface SpuAnalysis {
  spu: string;
  spu_name: string | null;
  category: string | null;
  image: string | null;
  shops: string[];
  date_range: string[];
  daily: SpuDailyPoint[];
  workday: CompareGroup;
  holiday: CompareGroup;
  insight: string;
}
