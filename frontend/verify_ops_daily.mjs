// 验证店铺运营数据页的展示判定纯函数（frontend/src/modules/opsDaily.ts）。
//
// 判定与渲染分离（ai-rules §5）：表格 / KPI 卡 / CSV 导出共用同一份格式化与判定，
// 这里把行为锁死，避免「页面上正常、导出里把 null 写成 0.00」这类静默漂移。
// 运行：npm run verify:ops
import {
  DEFAULT_KEY_COUNT,
  EMPTY,
  EXPORT_BATCH,
  EXPORT_CONCURRENCY,
  KPI_CARDS,
  MONTHLY_CHART,
  PAGE_SIZE,
  buildMonthlyChart,
  buildOpsCsv,
  classifyOpsError,
  csvCell,
  shopTypeLabel,
  defaultRange,
  defaultSelectedKeys,
  exportPages,
  formatTrend,
  formatValue,
  formatWan,
  isMissing,
  momSymbol,
  monthMom,
  previousRange,
  queryString,
  spanMom,
  trendClass,
} from "./src/modules/opsDaily.ts";

let pass = 0;
let fail = 0;

function eq(actual, expected, label) {
  const A = JSON.stringify(actual);
  const E = JSON.stringify(expected);
  if (A === E) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}\n  期望 ${E}\n  实际 ${A}`);
  }
}

// ---- NULL 与 0 分离：这条错了会把「没抓到」伪装成「没生意」 ----
eq(isMissing(null), true, "null 是缺失");
eq(isMissing(undefined), true, "undefined 是缺失");
eq(isMissing(NaN), true, "NaN 是缺失");
eq(isMissing(0), false, "0 不是缺失");
eq(formatValue(null, { unit: "元", precision: 2 }), EMPTY, "null 显示 --");
eq(formatValue(undefined, { unit: "元", precision: 2 }), EMPTY, "undefined 显示 --");
eq(formatValue(0, { unit: "元", precision: 2 }), "0", "真实 0 显示 0 而非 0.00");
eq(formatValue(2178.9, { unit: "元", precision: 2 }), "2178.90", "金额两位小数");
eq(formatValue(80, { unit: "人", precision: 0 }), "80", "计数零位小数");
eq(formatValue(0.075, { unit: "%", precision: 2 }), "7.50%", "比率小数转百分比");
eq(formatValue(0, { unit: "%", precision: 2 }), "0", "零比率仍显示 0");

// ---- 查询串：from/to 必填，默认分页锁死 50 行 ----
const qs = queryString({ from: "2026-08-01", to: "2026-10-31" });
eq(qs.includes("from=2026-08-01"), true, "查询串含 from");
eq(qs.includes("to=2026-10-31"), true, "查询串含 to");
eq(qs.includes("page=1"), true, "查询串默认第 1 页");
eq(qs.includes(`page_size=${PAGE_SIZE}`), true, "查询串默认每页 50 行");
// ⛔ 用脱敏占位店名（§6.1 推前扫描④），编码值现算而不是写死中文 URL 串。
eq(queryString({ from: "2026-08-01", to: "2026-08-31", shop: "示例店", shop_type: "pop", page: 3 })
  .includes(`shop=${encodeURIComponent("示例店")}`), true, "中文店铺名正确编码");
eq(PAGE_SIZE, 50, "每页 50 行（性能契约 §4.1）");
eq(EXPORT_BATCH, 500, "导出每批 500 行");
eq(EXPORT_CONCURRENCY, 4, "导出并发上限 4");

// ---- 分批：1564 行 = 4 批，单次响应恒定 ----
eq(exportPages(1564), [1, 2, 3, 4], "1564 行分 4 批");
eq(exportPages(0), [], "0 行不产生请求");
eq(exportPages(500), [1], "恰好一批");
eq(exportPages(501), [1, 2], "多 1 行即多一批");

// ---- CSV：列序、-- 保留、转义 ----
eq(csvCell("含,逗号"), '"含,逗号"', "逗号转义");
eq(csvCell('含"引号'), '"含""引号"', "引号翻倍");
eq(csvCell("普通"), "普通", "普通文本不包裹");
const metric = (key, cn, unit = "元") => ({
  metric_key: key, cn_name: cn, source_name: cn, kind: "atomic",
  db_column: key, formula: "", unit, is_baseline: true, sort_order: 1, precision: 2,
});
const cols = [metric("sales_amount", "销售额"), metric("visitors", "访客数", "人")];
const csvRows = [
  { date: "2026-08-01", shop: "A店", shop_type: "pop", values: { sales_amount: 100, visitors: 0 } },
  { date: "2026-08-02", shop: "B店", shop_type: "self", values: { sales_amount: null, visitors: 12 } },
];
const csv = buildOpsCsv(cols, csvRows);
const lines = csv.split("\r\n");
eq(lines.length, 3, "CSV 行数 = 表头 + 2 行");
eq(lines[0], "日期,店铺,类型,销售额,访客数", "CSV 表头列序 = 勾选列序");
eq(lines[1], "2026-08-01,A店,pop,100.00,0", "真实 0 导出为 0 不是 --");
eq(lines[2].includes(`${EMPTY},12`), true, "缺失值导出 -- 原样保留");
eq(buildOpsCsv(cols, []).split("\r\n").length, 1, "空数据只出表头");

// ---- 区间：默认近 30 天、上期等长 ----
eq(defaultRange("2026-10-31", 30), { from: "2026-10-02", to: "2026-10-31" }, "默认近 30 天");
// 10/01~10/31 共 31 天 ⇒ 等长上期是 08/31~09/30（跨月，别按自然月想当然写成 09/01）
eq(previousRange("2026-10-01", "2026-10-31"), { from: "2026-08-31", to: "2026-09-30" }, "上期等长区间");
eq(previousRange("2026-08-01", "2026-08-01"), { from: "2026-07-31", to: "2026-07-31" }, "单日区间的上期也是单日");

// ---- D3 绿涨红跌：up 对应 --color-up，down 对应 --color-down ----
eq(trendClass(120, 100), "up", "上涨 → up（绿）");
eq(trendClass(80, 100), "down", "下跌 → down（红）");
eq(trendClass(100, 100), "flat", "持平 → flat（中性）");
eq(trendClass(100, null), "flat", "上期缺失不染色");
eq(trendClass(null, 100), "flat", "本期缺失不染色");
eq(trendClass(100, 0), "flat", "上期为 0 不做除法");
eq(trendClass(100.0001, 100), "flat", "浮点噪声不染色");
eq(formatTrend(120, 100), "+20.00%", "环比文案带正号");
eq(formatTrend(80, 100), "-20.00%", "环比文案带负号");
eq(formatTrend(100, null), "", "上期缺失无环比文案");

// ---- KPI 卡：4 张、后两张派生；默认勾选 10 列 ----
eq(KPI_CARDS.length, 4, "4 张 KPI 卡");
eq(KPI_CARDS.filter((c) => c.derived).length, 2, "2 张派生卡");
eq(KPI_CARDS.map((c) => c.key), ["sales_amount", "visitors", "conversion_rate", "roi"], "KPI 卡 key");
eq(DEFAULT_KEY_COUNT, 10, "默认勾选 10 列");

// ---- 默认列：8 原子 + 2 派生，保证单行表头首屏就能看到派生重算值 ----
const fakeDict = [
  ...Array.from({ length: 19 }, (_, i) => metric(`a${i}`, `原子${i}`)),
  ...Array.from({ length: 5 }, (_, i) => ({
    ...metric(`d${i}`, `派生${i}`), kind: "derived", formula: "a0/a1",
  })),
];
const picked = defaultSelectedKeys(fakeDict);
eq(picked.length, 10, "默认勾选 10 列");
eq(picked.filter((k) => k.startsWith("d")).length, 2, "其中 2 个派生指标");
eq(picked.slice(0, 8).every((k) => k.startsWith("a")), true, "其余 8 个是原子指标");
eq(defaultSelectedKeys([metric("x", "X")]).length, 1, "字典不足 10 项时按实际数量");

// ---- 方案 C：月份之间的环比徽章（真实 8/9/10 月数据，与 DB 汇总一致） ----
eq(monthMom(1784381.57, 1684635.96), { text: "+5.92%", cls: "up" }, "自营 8→9 环比 +5.92%（绿涨）");
eq(monthMom(1857021.66, 1784381.57), { text: "+4.07%", cls: "up" }, "自营 9→10 环比 +4.07%");
eq(monthMom(887928.02, 969031.55), { text: "-8.37%", cls: "down" }, "POP 8→9 环比 -8.37%（红跌）");
eq(monthMom(824654.28, 887928.02), { text: "-7.13%", cls: "down" }, "POP 9→10 环比 -7.13%");
eq(monthMom(100, null), { text: "--", cls: "flat" }, "上期缺失 ⇒ -- 且不染色");
eq(monthMom(null, 100), { text: "--", cls: "flat" }, "本期缺失 ⇒ -- 且不染色");
eq(monthMom(100, 0), { text: "--", cls: "flat" }, "上期为 0 不做除法");
eq(monthMom(100, 100), { text: "0.00%", cls: "flat" }, "持平显示 0.00% 且用中性色");
eq(formatWan(1684635.96), "168.46", "元转万两位小数");
eq(formatWan(1980905.76, 1), "198.1", "元转万支持 1 位小数（Y 轴刻度用）");
eq(formatWan(0), "0.00", "零值转万仍是 0.00");
eq(formatWan(null), EMPTY, "缺失转万显示 --");
eq(momSymbol("up"), "▲", "涨用 ▲（纯文字徽章的冗余编码）");
eq(momSymbol("down"), "▼", "跌用 ▼");
eq(momSymbol("flat"), "—", "持平 / 无可比用 —");

const monthly = [
  { month: "2026-08", sales_amount: 2653667.51, visitors: 225876, by_type: { pop: 969031.55, self: 1684635.96 } },
  { month: "2026-09", sales_amount: 2672309.59, visitors: 219940, by_type: { pop: 887928.02, self: 1784381.57 } },
  { month: "2026-10", sales_amount: 2681675.94, visitors: 260545, by_type: { pop: 824654.28, self: 1857021.66 } },
];
const chart = buildMonthlyChart(monthly);
const mid = (MONTHLY_CHART.left + MONTHLY_CHART.right) / 2;
eq(chart.months, ["2026-08", "2026-09", "2026-10"], "月份升序");
eq(chart.lines.map((l) => l.key), ["self", "pop"], "两条折线，自营在前 POP 在后");
eq(chart.lines[0].points.map((p) => p.x), [MONTHLY_CHART.left, mid, MONTHLY_CHART.right], "3 个月 X 轴均分");
eq(chart.lines[0].points.map((p) => p.label), ["168.46", "178.44", "185.70"], "自营数值标签");

// 徽章：每条线 n-1 个，位于相邻两月的中点，文案与颜色都由纯函数判定
eq(chart.lines[0].badges.length, 2, "每条线 2 个「月份之间」徽章");
eq(chart.lines[0].badges.map((b) => b.text), ["+5.92%", "+4.07%"], "自营徽章文案");
eq(chart.lines[1].badges.map((b) => b.text), ["-8.37%", "-7.13%"], "POP 徽章文案");
eq(chart.lines[0].badges.map((b) => b.cls), ["up", "up"], "自营徽章绿涨");
eq(chart.lines[1].badges.map((b) => b.cls), ["down", "down"], "POP 徽章红跌");
eq(chart.lines[0].badges[0].x, (chart.lines[0].points[0].x + chart.lines[0].points[1].x) / 2, "徽章 X 在两月之间");

// 2026-11-06 视觉优化：徽章统一移到折线上方（用户追加要求「自营徽章移到线条上方」）
const selfMidY = (chart.lines[0].points[0].y + chart.lines[0].points[1].y) / 2;
const popMidY = (chart.lines[1].points[0].y + chart.lines[1].points[1].y) / 2;
eq(chart.lines[0].badges[0].y < selfMidY, true, "自营徽章在线段上方（从线下方移上来）");
eq(chart.lines[1].badges[0].y < popMidY, true, "POP 徽章也在上方，两条线位置一致");
eq(MONTHLY_CHART.badgeDy < 0, true, "badgeDy 为负 = 统一放线上方");
eq(chart.lines[0].badges.map((b) => b.symbol), ["▲", "▲"], "自营徽章带 ▲");
eq(chart.lines[1].badges.map((b) => b.symbol), ["▼", "▼"], "POP 徽章带 ▼");

// 刻度：1 位小数 + 去「万」后缀 + 左边距 50→82，保证右对齐刻度不被裁切
eq(chart.ticks.length, 3, "3 条 Y 轴刻度（非零起点已在副标题标注）");
eq(chart.ticks.map((t) => t.label), ["198.1", "134.1", "70.1"], "Y 轴刻度 1 位小数、不带「万」");
eq(chart.ticks.every((t) => !t.label.includes("万")), true, "刻度不含「万」（单位统一放左上轴标题）");
const tickLeftEdge = MONTHLY_CHART.left - 12 - Math.max(...chart.ticks.map((t) => t.label.length)) * 6;
eq(tickLeftEdge > 0, true, `刻度左边界 ${tickLeftEdge.toFixed(1)} > 0，不会越出 viewBox 被裁`);

// 线型区分（方案 A）：不依赖颜色，色弱 / 黑白打印也能分辨
eq(chart.lines[0].dash, "", "自营实线");
eq(chart.lines[0].hollow, false, "自营实心点");
eq(chart.lines[1].dash, "6 4", "POP 虚线（段长随线宽 3→2 收细）");
eq(chart.lines[1].hollow, true, "POP 空心点");
eq(chart.totalMoms.map((t) => t.mom.text), ["+0.70%", "+0.35%"], "合计月环比 +0.70% / +0.35%");
eq(spanMom([1684635.96, 1784381.57, 1857021.66]), { text: "+10.23%", cls: "up" }, "自营首末区间 +10.23%");
eq(spanMom([969031.55, 887928.02, 824654.28]), { text: "-14.90%", cls: "down" }, "POP 首末区间 -14.90%");
eq(spanMom([1]), { text: "--", cls: "flat" }, "不足 2 点无首尾对比");

eq(buildMonthlyChart([]).lines.length, 0, "空月度点不画线");
const oneMonth = buildMonthlyChart([monthly[0]]);
eq(oneMonth.lines[0].badges.length, 0, "只有 1 个月不产生环比徽章");
eq(oneMonth.lines[0].points[0].x, mid, "单月 X 居中");

// 缺失值：徽章 --、不染色、折线断开而不是用 0 伪装
const gap = buildMonthlyChart([
  { month: "2026-08", sales_amount: 100, visitors: 10, by_type: { self: 100, pop: null } },
  { month: "2026-09", sales_amount: 200, visitors: 20, by_type: { self: null, pop: 50 } },
  { month: "2026-10", sales_amount: 300, visitors: 30, by_type: { self: 300, pop: 60 } },
]);
eq(gap.lines[0].badges.map((b) => b.text), ["--", "--"], "缺失点两侧都不算环比");
eq(gap.lines[0].badges.map((b) => b.cls), ["flat", "flat"], "缺失点徽章不染色");
eq(gap.lines[0].badges.map((b) => b.symbol), ["—", "—"], "缺失徽章用 — 破折号");
eq(gap.lines[0].path.split("M").length - 1, 2, "缺失月份把折线断成 2 段");

// 类型集合取自数据：新增店铺类型自动多一条线，前端 0 改码
const extra = buildMonthlyChart([
  { month: "2026-08", sales_amount: 6, visitors: 1, by_type: { self: 1, pop: 2, other: 3 } },
  { month: "2026-09", sales_amount: 9, visitors: 1, by_type: { self: 2, pop: 3, other: 4 } },
]);
eq(extra.lines.map((l) => l.key), ["self", "pop", "other"], "新类型自动追加第三条线");
eq(extra.lines[2].color, "var(--color-brand)", "未知类型回退到局部主蓝令牌");
eq(extra.lines[2].dash !== "", true, "新类型自动分配不同线型，不写死类型枚举");
eq(extra.lines[2].hollow, true, "新类型用空心点，与实心自营区分");

// ---- 判定与渲染分离：错误分类与类型显示名都在纯函数层（ai-rules §3 / 手册 §1.3）----
eq(classifyOpsError(new Error("401 Unauthorized")).state, "denied", "401 → 无权限态");
eq(classifyOpsError(new Error("403 Forbidden")).state, "denied", "403 → 无权限态");
eq(classifyOpsError(new Error("无权限访问")).state, "denied", "中文无权限文案 → 无权限态");
eq(classifyOpsError("500 网关超时").state, "error", "普通 5xx → 错误态");
eq(classifyOpsError(new DOMException("AbortError")).state, "error", "取消的请求按普通错误处理");
eq(classifyOpsError(undefined).state, "error", "非 Error 抛出值不崩溃");
eq(classifyOpsError(new Error("401 Unauthorized")).message, "401 Unauthorized", "denied 仍保留原始错误文案");
eq(classifyOpsError("boom").message, "boom", "error 态带回原始错误文案");
eq(shopTypeLabel("self"), "自营", "self 显示「自营」");
eq(shopTypeLabel("pop"), "POP", "pop 显示「POP」");
eq(shopTypeLabel("other"), "other", "未知类型原样显示，不误标成自营");

console.log(`\n合计：PASS ${pass} / FAIL ${fail}`);
if (fail) {
  console.log("验证失败 ❌");
  process.exit(1);
}
console.log("全部通过 ✅");
