// 月份对比表纯逻辑验证：把关键算法抄一份独立跑，断言边界值。
// 不用 tsx（环境没装），走esbuild 转 .mjs 后由 node 执行。
import { monthBoundsOf, availableMonths } from "./src/utils/month.ts";

let pass = 0;
let fail = 0;
function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    pass++;
  } else {
    fail++;
    console.log(`  FAIL ${label}\n    期望 ${e}\n    实际 ${a}`);
  }
}

const pad = (n) => String(n).padStart(2, "0");

function dayCountInRange(r) {
  if (!r.start || !r.end) return 0;
  const [y1, m1, d1] = r.start.split("-").map(Number);
  const [y2, m2, d2] = r.end.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000) + 1;
}

console.log("== 1. 天数计算（含闰年 / 跨月 / 单日）==");
eq(dayCountInRange({ start: "2026-09-01", end: "2026-09-30" }), 30, "9月整月30天");
eq(dayCountInRange({ start: "2026-09-01", end: "2026-09-05" }), 5, "9月前5天");
eq(dayCountInRange({ start: "2026-09-05", end: "2026-09-05" }), 1, "单日=1");
eq(dayCountInRange({ start: "2024-02-01", end: "2024-02-29" }), 29, "2024闰年2月29天");
eq(dayCountInRange({ start: "2026-02-01", end: "2026-02-28" }), 28, "2026平年2月28天");
eq(dayCountInRange({ start: "2026-08-25", end: "2026-09-05" }), 12, "跨月区间");
eq(dayCountInRange({ start: "", end: "" }), 0, "空区间=0");

console.log("== 2. monthKeyBack（倒推 n 个月，跨年正确）==");
function monthKeyBack(maxDate, back) {
  if (!maxDate) return "";
  const [y, m] = maxDate.split("-").map(Number);
  const idx = y * 12 + (m - 1) + back;
  const yy = Math.floor(idx / 12);
  const mm = (idx % 12) + 1;
  return `${yy}-${pad(mm)}`;
}
eq(monthKeyBack("2026-10-05", -1), "2026-09", "10月数据 -> back0=2026-10");
eq(monthKeyBack("2026-10-05", -2), "2026-08", "10月数据 -> back-2=2026-08");
eq(monthKeyBack("2026-01-15", -1), "2025-12", "跨年：1月 back-1 = 上年12月");
eq(monthKeyBack("2026-03-31", -13), "2025-02", "跨年：3月 back-13");
eq(monthKeyBack("", -1), "", "无数据日期返回空");

console.log("== 3. 同天数截断（两边都对齐到较短的一方，与左右顺序无关）==");
// 数据区间对齐真实环境：pop_spu_detail = ['2026-08-01', '2026-10-03']
// （10 月只有 1~3 号 = 3 天，正是用户截图里「10 月 1-3 日 vs 9 月没跟着截」的场景）
const MIN = "2026-08-01";
const MAX = "2026-10-03";
function rangeOf(monthKey) {
  if (!monthKey) return { start: "", end: "" };
  const [y, m] = monthKey.split("-").map(Number);
  return monthBoundsOf(y, m - 1, MIN, MAX);
}
const pad2 = (n) => String(n).padStart(2, "0");
/** 与组件 trimToDays 同实现：截到前N 天，越界不反向延长 */
function trimToDays(base, days) {
  if (days <= 0 || !base.start || !base.end) return base;
  const [y, m, d] = base.start.split("-").map(Number);
  const cut = new Date(Date.UTC(y, m - 1, d + (days - 1)));
  const cutStr = `${cut.getUTCFullYear()}-${pad2(cut.getUTCMonth() + 1)}-${pad2(cut.getUTCDate())}`;
  return { start: base.start, end: cutStr < base.end ? cutStr : base.end };
}
/** 与组件 targetDays+ rangeA/rangeB 同实现 */
function align(rawA, rawB, on) {
  let target = 0;
  if (on) {
    const a = dayCountInRange(rawA);
    const b = dayCountInRange(rawB);
    if (a && b) target = Math.min(a, b);
  }
  return { a: trimToDays(rawA, target), b: trimToDays(rawB, target), target };
}
const rSep = rangeOf("2026-09"); // 9 月整月 30 天
const rOct = rangeOf("2026-10"); // 10 月只有 1~3 号 = 3 天
const rAugFull = rangeOf("2026-08"); // 8 月区间内完整月 31 天
eq(rAugFull, { start: "2026-08-01", end: "2026-08-31" }, "8月为区间内完整月 31 天");
eq(rOct, { start: "2026-10-01", end: "2026-10-03" }, "10月被数据上界裁到 1~3 号 = 3 天");
eq(dayCountInRange(rOct), 3, "10 月实际 3 天");

// 场景 1：未勾选 -> 两边都原样，不公平提示出现
const off1 = align(rSep, rOct, false);
eq(off1.a, { start: "2026-09-01", end: "2026-09-30" }, "未勾选：9月原样30 天");
eq(off1.b, { start: "2026-10-01", end: "2026-10-03" }, "未勾选：10月原样 3 天");
eq(off1.target, 0, "未勾选 target=0（不截断）");

// 场景 2（用户截图 bug）：**10 月在左只有 3 天、9 月在右 30 天**
// 旧实现「只截较早月」在这里完全失效（截断点10-01+29=10-30 越过自身终点 10-03，
// 走「不延长」分支原样返回，9 月仍 30 天）。新实现按min 对齐，9 月被截到 3 天。
const bug = align(rOct, rSep, true);
eq(bug.target, 3, "截图场景：目标天数 = min(3, 30) = 3");
eq(bug.a, { start: "2026-10-01", end: "2026-10-03" }, "截图场景：10 月（左，仅 3 天）不被延长");
eq(bug.b, { start: "2026-09-01", end: "2026-09-03" }, "截图场景：9 月（右，30 天）自动截到 1~3 日");
eq(dayCountInRange(bug.b), 3, "截图场景：截断后 9 月也是 3 天");
eq(dayCountInRange(bug.a) === dayCountInRange(bug.b), true, "截图场景：两边天数相等 -> unbalanced 消失");

// 场景 3：左右互换（9 月在左、10 月在右）结果必须完全一致 —— 与顺序无关
const sw = align(rSep, rOct, true);
eq(sw.target, 3, "左右互换：目标天数同样是 3");
eq(sw.a, { start: "2026-09-01", end: "2026-09-03" }, "左右互换：9 月（左）截到 1~3 日");
eq(sw.b, { start: "2026-10-01", end: "2026-10-03" }, "左右互换：10 月（右）原样");

// 场景 4：两个完整月 31 vs 30 -> 截长的那一边到 30
const full = align(rAugFull, rSep, true);
eq(full.target, 30, "31 vs 30 -> 目标 30 天");
eq(full.a, { start: "2026-08-01", end: "2026-08-30" }, "8 月截到 30 天");
eq(full.b, { start: "2026-09-01", end: "2026-09-30" }, "9 月本来就是 30 天，不变");

// 场景 5：绝不反向延长（目标天数大于原区间时原样返回）
eq(trimToDays(rOct, 30), rOct, "目标 30 天> 10月自身 3 天 -> 原样，不凭空造数据");
eq(trimToDays(rSep, 0), rSep, "days<=0 -> 不截断");
eq(trimToDays({ start: "", end: "" }, 5), { start: "", end: "" }, "空区间原样");
// 跨月借位：9-28 起取 5 天，算出的截断点是 10-02（Date.UTC 自动借位），
// 但它已越出自身 end=09-30，「不反向延长」保护把它收敛回 09-30 —— 结果仍只有 3 天
eq(trimToDays({ start: "2026-09-28", end: "2026-09-30" }, 5).end, "2026-09-30", "跨月借位：截断点越界时收敛回自身 end，不越界");
eq(dayCountInRange(trimToDays({ start: "2026-09-28", end: "2026-09-30" }, 5)), 3, "越界后天数仍是原区间 3 天");
// 借位不越界时按借位结果截断（9-28 起 3 天 = 09-30，正好不越界）
eq(trimToDays({ start: "2026-09-28", end: "2026-09-30" }, 3).end, "2026-09-30", "9-28 起 3 天 -> 09-30");
// 起始日在区间中段、截断点仍在区间内时正常截断
eq(trimToDays({ start: "2026-09-20", end: "2026-09-30" }, 5).end, "2026-09-24", "9-20 起 5 天 -> 09-24");
eq(rangeOf("2026-07"), { start: "2026-08-01", end: "2026-08-01" }, "区间外的月份退化为单日端点（既有行为）");

console.log("== 4. availableMonths（月份清单 + 年份区间）==");
const cells = availableMonths(MIN, MAX);
eq(cells.length, 3, "8/9/10 共 3 个月");
eq(cells[0].key, "2026-10", "倒序：最新在前");
eq(cells[2].key, "2026-08", "倒序：最旧在后");
eq(cells.every((c) => c.hasData), true, "区间内月份全部 hasData");
eq(cells[0].year, 2026, "年份一致");
eq(availableMonths("", "").length, 0, "两端都缺返回空");
// 跨年
const y2 = availableMonths("2025-11-20", "2026-02-03");
eq(y2.map((c) => c.key), ["2026-02", "2026-01", "2025-12", "2025-11"], "跨年 11~2月");

console.log("== 5. 表格计算：按单位决定差额写法 / 变化率全类型都有 / 绿涨红跌方向 ==");
const METRICS = [
  { key: "amount", title: "成交金额", format: "currency", group: "detail", agg: { kind: "sum" } },
  { key: "visitors", title: "商品访客数", format: "number", group: "detail", agg: { kind: "sum" } },
  // 客单价：ratio 聚合但 format 是 currency —— 必须按金额给差额，不能给 pt（曾经的 bug）
  { key: "avg_price", title: "客单价", format: "currency", group: "detail", agg: { kind: "ratio", num: "amount", den: "buyers" } },
  { key: "conversion_rate", title: "成交转化率", format: "percent", group: "detail", agg: { kind: "ratio", num: "buyers", den: "visitors" } },
  { key: "roi", title: "ROI", format: "decimal", group: "promo", agg: { kind: "ratio", num: "promotion_amount", den: "promotion_cost" } },
  { key: "refund_amount", title: "取消及售后退款金额", format: "currency", group: "extra", agg: { kind: "sum" } },
];
/** 与组件 fmtBy 简化版：验证脚本只关心「按单位格式化」这条规则本身 */
function fmtLike(format, n) {
  if (format === "currency") return `¥${n.toFixed(2)}`;
  if (format === "percent") return `${(n * 100).toFixed(2)}%`;
  if (format === "decimal") return n.toFixed(2);
  return String(Math.round(n));
}
/** 与组件 dirClass 一致：绿涨红跌只看数值方向，不做业务好坏判断 */
function dirClass(dir) {
  return dir > 0 ? "up" : dir < 0 ? "down" : "flat";
}
function calcRows(a, b) {
  const out = [];
  for (const m of METRICS) {
    const av = a[m.key];
    const bv = b[m.key];
    // ⚠️ 关键：按format（单位）而非 agg.kind（聚合方式）判断是否用 pt
    const isPercent = m.format === "percent";
    const both = av != null && bv != null;
    const diff = both ? bv - av : null;
    let diffText = "";
    let deltaText = "--";
    let dir = 0;
    if (both && diff !== null) {
      dir = diff === 0 ? 0 : diff > 0 ? 1 : -1;
      const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
      if (isPercent) {
        diffText = `${sign}${(Math.abs(diff) * 100).toFixed(2)}pt`;
      } else {
        diffText = `${sign}${fmtLike(m.format, Math.abs(diff))}`;
      }
      // 变化率：全类型统一给相对变化率（分母 0 时保持 --）
      const base = av;
      if (base !== 0) {
        const r = diff / Math.abs(base);
        deltaText = `${r > 0 ? "+" : r < 0 ? "−" : ""}${(Math.abs(r) * 100).toFixed(1)}%`;
      }
    }
    out.push({ key: m.key, title: m.title, group: m.group, format: m.format, aggKind: m.agg.kind, isPercent, diffText, deltaText, dir });
  }
  return out;
}
const rows = calcRows(
  { amount: 10000, visitors: 1000, avg_price: 85, conversion_rate: 0.0261, roi: 2.5, refund_amount: 500 },
  { amount: 12500, visitors: 800, avg_price: 88, conversion_rate: 0.0188, roi: 2.5, refund_amount: 500 },
);
eq(rows[0].deltaText, "+25.0%", "成交金额 +25.0%");
eq(rows[0].dir, 1, "成交金额上升");
eq(dirClass(rows[0].dir), "up", "上升 -> 绿（up）");
eq(rows[0].diffText, "+¥2500.00", "成交金额差额按 currency 格式化");
eq(rows[1].deltaText, "−20.0%", "访客数-20.0%（用 U+2212）");
eq(rows[1].dir, -1, "访客数下降");
eq(dirClass(rows[1].dir), "down", "下降 -> 红（down）");

// ===== 客单价：ratio 聚合但单位是金额，参照成交金额的写法 =====
eq(rows[2].aggKind, "ratio", "客单价确实是 ratio 聚合");
eq(rows[2].isPercent, false, "但客单价不是百分比类（format=currency）");
eq(rows[2].diffText, "+¥3.00", "客单价差额是金额 +¥3.00，不是 pt");
eq(rows[2].diffText.includes("pt"), false, "客单价差额绝不含 pt");
eq(rows[2].deltaText, "+3.5%", "客单价变化率 85->88 = +3.5%");
eq(dirClass(rows[2].dir), "up", "客单价上升 -> 绿");

// ===== 百分比类：差额 pt + 变化率（两列都有值）=====
eq(rows[3].isPercent, true, "转化率是百分比类");
eq(rows[3].diffText, "−0.73pt", "转化率 2.61%->1.88% 差额记百分点 -0.73pt");
eq(rows[3].deltaText, "−28.0%", "转化率变化率也有值 -28.0%（不再是 --）");
eq(rows[3].deltaText === "--", false, "百分比类变化列不再是 --（用户要求补上）");
// pt 与% 并存不冲突：同一件事的绝对差与相对表述
eq(rows[3].diffText, "−0.73pt", "转化率：绝对差用 pt");
eq(rows[3].deltaText, "−28.0%", "转化率：相对变化用 %");
// ⚠️ 浮点数直接 ===/eq 比较会因二进制误差 FAIL（0.73 实际是 0.7300000000000001），用容差比较
function near(actual, expected, label) {
  if (Math.abs(actual - expected) < 1e-9) {
    pass++;
  } else {
    fail++;
    console.log(`  FAIL ${label}\n    期望≈ ${expected}\n    实际 ${actual}`);
  }
}
near(Math.abs(0.0261 - 0.0188) * 100, 0.73, "pt 差 = 绝对差 0.73 个百分点");
// ⚠️ 这里期望值是「四舍五入到两位」的结果，容差要按四舍五入的粒度给（0.005），不能给 1e-9
near(Number(((0.0188 - 0.0261) / 0.0261 * 100).toFixed(2)), -27.97, "% 变化 = 相对变化 -27.97%");
eq(rows[3].deltaText, "−28.0%", "UI 里显示的是四舍五入后的 −28.0%");
// 反向印证：客单价若误用 pt 会得到什么（说明判据必须是 format 而非 agg.kind）
near(Math.abs(88 - 85) * 100, 300, "客单价若错按 pt 会得到 300pt（离谱，正确是 +¥3.00）");

// ===== ROI：decimal 单位，差额不是 pt =====
eq(rows[4].isPercent, false, "ROI 不是百分比类（format=decimal）");
eq(rows[4].diffText, "0.00", "ROI 持平差额 0.00（无 pt、无正负号）");
eq(rows[4].dir, 0, "ROI 持平 dir=0");
eq(dirClass(rows[4].dir), "flat", "持平 -> flat 中性色");
eq(rows[5].deltaText, "0.0%", "持平求和类 0.0%");

// 基准为 0：不给变化率（避免 Infinity），但差额仍给出
const zeroBase = calcRows(
  { amount: 0, visitors: 0, avg_price: 0, conversion_rate: 0, roi: 0, refund_amount: 0 },
  { amount: 500, visitors: 10, avg_price: 120, conversion_rate: 0.1, roi: 1, refund_amount: 1 });
eq(zeroBase[0].deltaText, "--", "基准为0 时不给变化率（避免 Infinity）");
eq(zeroBase[0].diffText, "+¥500.00", "基准为 0 仍有差额（金额）");
// 比率类分母为 0 时后端给 null -> 前端显示 --，不编造 0% 变化
const nullish = calcRows(
  { amount: 100, visitors: 10, avg_price: null, conversion_rate: 0.02, roi: 2, refund_amount: 0 },
  { amount: 150, visitors: 20, avg_price: 88, conversion_rate: 0.03, roi: 2, refund_amount: 0 });
eq(nullish[2].deltaText, "--", "客单价任一为 null -> 变化率 --");
eq(nullish[2].diffText, "", "客单价任一为 null -> 差额留空（模板渲染为 —）");
eq(nullish[2].dir, 0, "任一为 null -> dir=0（不着色）");
eq(nullish[0].deltaText, "+50.0%", "同组内其它指标仍正常计算");

console.log("== 6. tableRows（分组伪行；斑马纹已移除）==");
const GROUP_TITLE = { detail: "商品明细表", promo: "推广数据表", extra: "退款 / 其他" };
/** 与组件 tableRows 一致：只在 group 变化时插一条伪行，不再计算组内序号 */
function buildTableRows(rs) {
  const out = [];
  let lastGroup = "";
  for (const r of rs) {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      out.push({ key: `g:${r.group}`, kind: "group", title: GROUP_TITLE[r.group] });
    }
    out.push({ ...r, kind: "row" });
  }
  return out;
}
const tr = buildTableRows(rows);
eq(tr.length, 9, "6 指标 + 3 分组标题 = 9 行");
eq(tr.filter((r) => r.kind === "group").length, 3, "3 个分组标题行");
eq(tr.map((r) => r.kind), ["group", "row", "row", "row", "row", "group", "row", "group", "row"], "行序");
eq(tr[0].title, "商品明细表", "第 1 组标题");
eq(tr[5].title, "推广数据表", "第 2 组标题");
eq(tr[7].title, "退款 / 其他", "第 3 组标题");
eq(new Set(tr.map((r) => r.key)).size, tr.length, "全部行 key 无重复");
// 分组行不携带数据字段（模板里用 colspan=5 整行渲染）
eq(tr.filter((r) => r.kind === "group").every((r) => r.dir === undefined), true, "分组行不带 dir");
// 每一行的变化列都有值（无"--" 占位），除法分母为 0 的 null 场景
const noDash = rows.every((r) => r.deltaText !== "--" || r.key === "avg_price" || r.key === "amount");
eq(noDash, true, "正常数据下变化列全部有值（用户要求：不能因为是比率就留空）");
// 空数据
eq(buildTableRows([]).length, 0, "无数据时 0 行");

console.log("== 7. 多月份对比（相邻 diff / 全局最短天数对齐 / 空月份短路）==");
// 全局最短天数：多选下所有月都对齐到最短月，与具体哪个月最短无关
function globalTarget(daysArr, on) {
  if (!on) return 0;
  const ds = daysArr.filter((d) => d > 0);
  return ds.length ? Math.min(...ds) : 0;
}
eq(globalTarget([3, 30, 31], true), 3, "多选同天数：全局最短 = min(3,30,31) = 3");
eq(globalTarget([30, 30, 31], true), 30, "三个里最短 30");
eq(globalTarget([3, 30, 31], false), 0, "未勾选同天数 -> 0");
eq(globalTarget([], true), 0, "无月 -> 0");

// 原序相邻 diff（表格用）：每个相邻对独立算，任一缺失则该段 ---，但其它段照常
function tableSegments(vals, isPercent) {
  const segs = [];
  for (let i = 0; i < vals.length - 1; i++) {
    const a = vals[i];
    const b = vals[i + 1];
    if (a != null && b != null) {
      const diff = b - a;
      const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
      segs.push(isPercent ? `${sign}${(Math.abs(diff) * 100).toFixed(2)}pt` : `${sign}${Math.abs(diff)}`);
    } else {
      segs.push("—");
    }
  }
  return segs;
}
eq(tableSegments([10, 12, 15], false), ["+2", "+3"], "三_month 相邻差 原序");
eq(tableSegments([10, null, 15], false), ["—", "—"], "中间月缺失 -> 两段都 ---（原序相邻，不跳月）");
eq(tableSegments([0.0261, 0.0188], true), ["−0.73pt"], "百分比类相邻差记 pt");

// 时间轴 diff（仅取有值的相邻月，跳过空月后相连）
function timelineSegments(vals, isPercent) {
  const present = vals.filter((v) => v != null);
  const segs = [];
  for (let i = 0; i < present.length - 1; i++) {
    const a = present[i];
    const b = present[i + 1];
    const diff = b - a;
    const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
    segs.push(isPercent ? `${sign}${(Math.abs(diff) * 100).toFixed(2)}pt` : `${sign}${Math.abs(diff)}`);
  }
  return segs;
}
eq(timelineSegments([10, null, 15], false), ["+5"], "时间轴跳过空月：10->15 差 +5（非两段 ---）");
eq(timelineSegments([0.0261, null, 0.02], true), ["−0.61pt"], "时间轴百分比跳过空月记 pt");
eq(timelineSegments([8], false), [], "仅 1 个有值月 -> 无段（时间轴退化无数据）");

// 月份排序：YYYY-MM 字典序即时间序
const sel = ["2026-10", "2026-08", "2026-09"];
eq([...sel].sort(), ["2026-08", "2026-09", "2026-10"], "选中月份排序 旧->新");

console.log(`\n通过 ${pass} 项，失败 ${fail} 项`);
if (fail > 0) process.exit(1);
