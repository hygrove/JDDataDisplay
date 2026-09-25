// 日期区间快捷筛选辅助：
//   monthBounds      给定「相对当月的偏移」（0=当月，-1=上月），返回该月区间，
//                    并与数据可用区间 [min, max] 取交集——月份内日期不全时「有多少天算多少天」；
//                    若整月落在数据区间之外（如数据偏旧），退化到最近的数据端点，保证能筛出数据。
//   recentDaysRange  以数据最新日 max 为终点往前推 N 天（含终点当天），用于「近一周」等快捷区间。
//
// 用法：
//   - 日期区间筛选（单品分析页）：直接用返回的 { start, end }；
//   - 单日筛选（模块页）：取 end 即该月「最新有数据的一天」。
//
// ⚠️ 全程用本地时间的 Date 构造 + 手动补零拼字符串，刻意不依赖 toISOString()：
//    toISOString() 会转成 UTC，在东八区会把「当月 1 号」算成上个月的最后一天，差一天。

/**
 * 把数字补零成两位字符串（1 -> "01"）。
 *
 * @param {number} n - 待补零的数字（0~99）。
 * @returns {string} 补零后的两位字符串。
 * @example
 * ```ts
 * pad(9);   // "09"
 * pad(12);  // "12"
 * ```
 */
const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 日期区间：起止均为 "YYYY-MM-DD" 字符串。
 */
export interface MonthRange {
  /** 区间起始日期；无有效区间时为空串 */
  start: string;
  /** 区间截止日期；无有效区间时为空串 */
  end: string;
}

/**
 * 计算「相对当月的某个月」的日期区间，并与数据可用区间取交集。
 *
 * @param {number} offset - 相对当月的月份偏移：0 = 当月，-1 = 上月，1 = 下月。
 * @param {string} [min] - 数据可用区间的最早日期 "YYYY-MM-DD"；可选，用于取交集。
 * @param {string} [max] - 数据可用区间的最晚日期 "YYYY-MM-DD"；可选，用于取交集。
 * @returns {MonthRange} 取交集后的日期区间。
 *      整月完全落在数据区间之外时，退化成 { start: 端点, end: 端点 }（保证能筛出数据）。
 * @example
 * ```ts
 * monthBounds(0, "2026-09-01", "2026-09-24");  // { start: "2026-09-01", end: "2026-09-24" }
 * monthBounds(-1, "2026-09-01", "2026-09-24"); // 上月整月无数据 -> 退化到 { start: "2026-09-01", end: "2026-09-01" }
 * ```
 */
export function monthBounds(offset: number, min?: string, max?: string): MonthRange {
  const now = new Date();
  let y = now.getFullYear();
  let m0 = now.getMonth() + offset; // 0-indexed：0=1月
  // 跨年归一：偏移可能让月份落到 0~11 之外（如 -1 月或 +12 月），循环进位/退位到合法范围
  while (m0 < 0) {
    m0 += 12;
    y -= 1;
  }
  while (m0 > 11) {
    m0 -= 12;
    y += 1;
  }
  const start = `${y}-${pad(m0 + 1)}-01`;
  // new Date(y, m0+1, 0) 的「第 0 天」= 上个月最后一天，这是取当月天数的惯用写法
  const lastDay = new Date(y, m0 + 1, 0).getDate();
  const end = `${y}-${pad(m0 + 1)}-${pad(lastDay)}`;

  // 整月落在数据区间之外：退化到最近的数据端点（保证有数据可选，而不是返回空区间）
  if (max && start > max) return { start: max, end: max };
  if (min && end < min) return { start: min, end: min };

  // 部分重叠：按数据区间裁剪两端
  const cs = min && start < min ? min : start;
  const ce = max && end > max ? max : end;
  return { start: cs, end: ce };
}

/**
 * 最近 N 天区间：以数据最新日 max 为终点往前推 N 天（含终点当天，故 start = max - (N-1) 天）。
 *
 * @param {number} days - 区间天数，含终点当天（如 7 表示「近一周」）；小于 1 时按 1 处理。
 * @param {string} [min] - 数据最早日期；可选，用于把起点截断在数据范围内。
 * @param {string} [max] - 数据最新日期（同时作为区间终点）；**必填**，缺失时返回空区间。
 * @returns {MonthRange} 最近 N 天的区间。
 *      数据不足 N 天时被 min 截断；max 缺失时返回 { start: "", end: "" }
 *      （交给调用方按「全区间」处理）。
 * @example
 * ```ts
 * recentDaysRange(7, "2026-08-01", "2026-09-17"); // { start: "2026-09-10", end: "2026-09-17" }
 * recentDaysRange(7, undefined, undefined);       // { start: "", end: "" }
 * ```
 */
export function recentDaysRange(days: number, min?: string, max?: string): MonthRange {
  // 没有最新日就无法定位「最近」，返回空区间让调用方退化到全区间
  if (!max) return { start: "", end: "" };
  const span = Math.max(days, 1);
  const [y, m, d] = max.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  // setDate 会自动处理跨月/跨年借位，不必自己算每月天数
  dt.setDate(dt.getDate() - (span - 1));
  const raw = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
  // 起点不能早于数据最早日；终点固定为 max
  const cs = min && raw < min ? min : raw;
  const ce = max;
  return { start: cs, end: ce };
}
