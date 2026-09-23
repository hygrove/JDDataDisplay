// 月份快捷筛选辅助：给定「相对当月的偏移」（0=当月，-1=上月），返回该月的日期区间，
// 并与数据可用区间 [min, max] 取交集——月份内日期不全时“有多少天算多少天”。
// 若整月落在数据区间之外（如数据偏旧），退化到最近的数据端点，保证能筛出数据。
//
// recentDaysRange：以数据最新日 max 为终点往前推 N 天（含终点当天），用于「近一周」等快捷区间。
//
// 用法：
//   - 日期区间筛选（单品分析页）：直接用返回的 { start, end }；
//   - 单日筛选（模块页）：取 end 即该月“最新有数据的一天”。

const pad = (n: number) => String(n).padStart(2, "0");

export interface MonthRange {
  start: string;
  end: string;
}

export function monthBounds(offset: number, min?: string, max?: string): MonthRange {
  const now = new Date();
  let y = now.getFullYear();
  let m0 = now.getMonth() + offset; // 0-indexed
  while (m0 < 0) {
    m0 += 12;
    y -= 1;
  }
  while (m0 > 11) {
    m0 -= 12;
    y += 1;
  }
  const start = `${y}-${pad(m0 + 1)}-01`;
  const lastDay = new Date(y, m0 + 1, 0).getDate();
  const end = `${y}-${pad(m0 + 1)}-${pad(lastDay)}`;

  // 整月落在数据区间之外：退化到最近的数据端点（保证有数据可选）
  if (max && start > max) return { start: max, end: max };
  if (min && end < min) return { start: min, end: min };

  const cs = min && start < min ? min : start;
  const ce = max && end > max ? max : end;
  return { start: cs, end: ce };
}

/**
 * 最近 N 天区间：以数据最新日 max 为终点往前推 N 天（含终点当天，故 start = max - (N-1) 天）。
 * 例：max=2026-09-17、days=7 -> 2026-09-10 ~ 2026-09-17。
 * 数据不足 N 天时被 min 截断；两端都没有就不返回任何区间（交给调用方按全区间处理）。
 */
export function recentDaysRange(days: number, min?: string, max?: string): MonthRange {
  if (!max) return { start: "", end: "" };
  const span = Math.max(days, 1);
  const [y, m, d] = max.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() - (span - 1));
  const raw = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
  const cs = min && raw < min ? min : raw;
  const ce = max;
  return { start: cs, end: ce };
}
