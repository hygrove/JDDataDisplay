// 日期区间快捷筛选辅助：
//   monthBounds      给定「相对当月的偏移」（0=当月，-1=上月），返回该月区间，
//                    并与数据可用区间 [min, max] 取交集——月份内日期不全时「有多少天算多少天」；
//                    若整月落在数据区间之外（如数据偏旧），退化到最近的数据端点，保证能筛出数据。
//   monthBoundsOf    monthBounds 的「绝对年月」版本：直接给定年 + 月返回该月区间。
//                    月份选择器按年翻页后需要精确命中某年某月，不能用 offset 换算
//                    （offset 是相对系统当前月的，跨年时极易算错）。
//   availableMonths  由数据可用区间 [min, max] 反推「有数据月份清单」（含中间无数据的空档月），
//                    供月份选择器渲染：既不写死近N 个月，也不会漏掉数据跨度大的月份。
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
 * 某个月份的信息（供月份选择器渲染一个月份格子）。
 */
export interface MonthCell {
  /** 年份，如 2026 */
  year: number;
  /** 月份（0-indexed：0=1月），与 Date 构造保持一致，避免渲染层再做 +/-1 换算出错 */
  month0: number;
  /** 展示文案，如 "2026-09" */
  key: string;
  /** 该月是否有数据落在 [min, max] 内；false 时选择器应置灰而非隐藏 */
  hasData: boolean;
}

/**
 * 计算「绝对年月」的某个月」的日期区间，并与数据可用区间取交集。
 *
 * @remarks
 * 这是 monthBounds 的底层实现。单独暴露是因为月份选择器按年翻页后要精确命中
 * 某一年的某个月，而 monthBounds 的offset 是**相对系统当前月**的：
 * 用 `offset = (目标年 - 今年) * 12 + (目标月 - 今月)` 换算在跨年场景极易算错，
 * 直接传绝对年月没有这类歧义。
 *
 * @param {number} year - 目标年份，如 2026。
 * @param {number} month0 - 目标月份（0-indexed：0=1月，11=12月）。
 * @param {string} [min] - 数据可用区间的最早日期 "YYYY-MM-DD"；可选，用于取交集。
 * @param {string} [max] - 数据可用区间的最晚日期 "YYYY-MM-DD"；可选，用于取交集。
 * @returns {MonthRange} 取交集后的日期区间。
 *      整月完全落在数据区间之外时，退化成 { start: 端点, end: 端点 }（保证能筛出数据）。
 * @example
 * ```ts
 * monthBoundsOf(2026, 8, "2026-09-01", "2026-09-24"); // { start: "2026-09-01", end: "2026-09-24" }
 * monthBoundsOf(2025, 0);                            // { start: "2025-01-01", end: "2025-01-31" }
 * ```
 */
export function monthBoundsOf(year: number, month0: number, min?: string, max?: string): MonthRange {
  const start = `${year}-${pad(month0 + 1)}-01`;
  // new Date(y, m+1, 0) 的「第 0 天」= 上个月最后一天，这是取当月天数的惯用写法
  const lastDay = new Date(year, month0 + 1, 0).getDate();
  const end = `${year}-${pad(month0 + 1)}-${pad(lastDay)}`;

  // 整月落在数据区间之外：退化到最近的数据端点（保证有数据可选，而不是返回空区间）
  if (max && start > max) return { start: max, end: max };
  if (min && end < min) return { start: min, end: min };

  // 部分重叠：按数据区间裁剪两端
  const cs = min && start < min ? min : start;
  const ce = max && end > max ? max : end;
  return { start: cs, end: ce };
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
  return monthBoundsOf(y, m0, min, max);
}

/**
 * 由数据可用区间 [min, max] 反推「有数据的月份清单」，从新到旧排序。
 *
 * @remarks
 * 为什么由数据推导而不是写死近 N 个月：
 * 1. 数据可能只有 2 个月，写死会出现一堆点不动的空月份；
 * 2. 数据可能跨 2 年，写死又会让早期月份直接选不到。
 * 因此以 min/max 的月份为上下界，**逐月枚举**（含中间没有数据的空档月）。
 *
 * 空档月**不删除而是标记 hasData=false**，由选择器置灰显示——让用户看到
 * 「这个月存在但没数据」，而不是以为按钮坏了。
 *
 * ⚠️ 但要注意：min/max 是**连续**的两端（数据最早日 / 最晚日），因此凡是落在
 * [min, max] 跨度内的月份必然与该区间有交集，**这里返回的 hasData 实际恒为 true**。
 * 真正的「无数据月份」出现在**翻到数据范围之外的年份**时——那些年份整年都不在
 * [min, max] 内，由调用方（DateRangePicker.monthGrid）的兜底对象标记 hasData=false 并置灰。
 * 保留该字段是为了让「有数据/无数据」的判定只有一处，不用在渲染层重新比较日期。
 *
 * @param {string} [min] - 数据可用区间的最早日期 "YYYY-MM-DD"；缺失时按今天兜底。
 * @param {string} [max] - 数据可用区间的最晚日期 "YYYY-MM-DD"；缺失时按今天兜底。
 * @returns {MonthCell[]} 月份清单，按时间倒序（最新月在前）；min/max 都缺失时返回空数组。
 * @example
 * ```ts
 * availableMonths("2025-11-20", "2026-02-03");
 * // [ {year:2026,month0:1,key:"2026-02",hasData:true},
 * //   {year:2026,month0:0,key:"2026-01",hasData:true},
 * //   {year:2025,month0:11,key:"2025-12",hasData:true},
 * //   {year:2025,month0:10,key:"2025-11",hasData:true} ]
 * ```
 */
export function availableMonths(min?: string, max?: string): MonthCell[] {
  const now = new Date();
  // 两端都缺失时无法确定范围，返回空数组让调用方自行退化（而不是瞎猜一年）
  if (!min && !max) return [];

  const hi = max ?? `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`;
  const lo = min ?? `${now.getFullYear()}-${pad(now.getMonth() + 1)}-01`;
  const [hy, hm] = hi.split("-").map(Number);
  const [ly, lm] = lo.split("-").map(Number);

  const out: MonthCell[] = [];
  // 用「绝对月序号」做倒序遍历：序号 = 年 * 12 + 月，避免逐年分支处理跨年
  const hiIdx = hy * 12 + (hm - 1);
  const loIdx = ly * 12 + (lm - 1);
  // 上限 240 个月（20 年）兜底，防止 min/max 被传入异常值时死循环
  for (let i = 0; i < 240; i++) {
    const idx = hiIdx - i;
    if (idx < loIdx) break;
    const year = Math.floor(idx / 12);
    const month0 = idx % 12;
    const first = `${year}-${pad(month0 + 1)}-01`;
    const last = `${year}-${pad(month0 + 1)}-${pad(new Date(year, month0 + 1, 0).getDate())}`;
    // 该月与 [min, max] 有交集即视为有数据（日期是定长字符串，字典序即时间序）
    const hasData = (!min || last >= min) && (!max || first <= max);
    out.push({ year, month0, key: `${year}-${pad(month0 + 1)}`, hasData });
  }
  return out;
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
