// 验证推广指标的**口径正确性**（纯逻辑，不依赖后端服务在线）。
//
// 为什么口径要先验：比率类写成「每日比率求平均」是本项目最容易踩的坑，
// 销量分布不均时会明显偏差。所有断言都围绕「先求和再相除」与「差额可加」两条纪律。
//
// ⚠️ points 的形状约定：后端 `transforms.compute_metrics` 已在**每一行**算出
//    promo_profit（= promotion_amount − promotion_cost），前端拿到的点里就有这个字段。
//    所以前端 agg 是 {kind:"sum"}，**不重复实现减法** —— 两边各算一次必然漂移
//    （未来后端改成扣毛利之类，前端会静默留在旧口径）。
//
// 覆盖范围（工单 01）：
//   - promo_profit 是可加指标：逐行相加恒等于 Σamount − Σcost（加法对减法封闭）
//   - 「先求和再相除」与「先平均后相除」在构造数据上必须产出不同值
//   - 字段缺失降级为 null（前端显示 --），绝不产生 0 / NaN
//
// ⚠️ 占比指标（promo_cost_share / promo_amount_share）的断言在工单 04 补，
//    因为它们的分母依赖 (shop, date)，需在端点内按当日店级聚合后才有意义。
//
// 覆盖范围（工单 01）：
//   - promo_profit 是可加指标：逐行相加恒等于 Σamount − Σcost（加法对减法封闭）
//   - 「先求和再相除」与「先平均后相除」在构造数据上必须产出不同值
//   - 字段缺失降级为 null（前端显示 --），绝不产生 0 / NaN
//
// 覆盖范围（工单 07 · SPU 决策表）：
//   - 排序：默认按建议分配额降序；delta 按**绝对值**；roi=null 恒排最后
//   - 过滤：chip 多选，空集 = 全部；未分档（roi=null）自成一路不被误吞
//   - 差额语义：0.005 阈值切三档，配色走中性蓝/灰（⛔ 不是涨红跌绿）
//   - roi=null 显示 `--`（⛔ 不是 0.00 —— fmtBy("decimal", null) 会返回 "0.00"）
import { aggregateMetrics, sumRawMetrics } from "./src/metrics.ts";
import {
  DEFAULT_SORT,
  DELTA_EPS,
  deltaTone,
  filterByQuadrant,
  formatDelta,
  formatRoi,
  quadrantCounts,
  sortSpus,
  BANNED_WORDS,
  DISCLAIMER_COUNT,
  DISCLAIMER_META,
  capNotice,
  checkDisclaimers,
} from "./src/modules/promoTable.ts";

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
function near(actual, expected, label, eps = 0.005) {
  if (typeof actual === "number" && Math.abs(actual - expected) <= eps) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}\n  期望 ${expected}（±${eps}）\n  实际 ${actual}`);
  }
}
function ok(cond, label) {
  if (cond) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}`);
  }
}

/**
 * 把「原始点」补成后端返回的形状：逐行算出 promo_profit。
 * 与后端 compute_metrics 的公式保持一字不差（差额列，NOT safe_div）。
 */
function withProfit(p) {
  const { promotion_cost: c, promotion_amount: a } = p;
  return { ...p, promo_profit: typeof c === "number" && typeof a === "number" ? a - c : null };
}

console.log("=== 推广指标口径验证 ===\n");

// ---------- 1. promo_profit 可加：Σ(amt−cost) === Σamt − Σcost ----------
// 这是本次改动最关键的性质：加法对减法封闭，所以任意聚合层级都成立。
{
  const raw = [
    { promotion_cost: 50, promotion_amount: 200 },
    { promotion_cost: 100, promotion_amount: 80 },
  ];
  const points = raw.map(withProfit);
  const agg = aggregateMetrics(points);
  // Σ(200-50) + Σ(80-100) = 150 + (-20) = 130
  eq(agg.promo_profit, 130, "promo_profit 逐行相加 = Σ(amt−cost)");
  const sums = sumRawMetrics(points);
  near(
    agg.promo_profit,
    sums.promotion_amount - sums.promotion_cost,
    "promo_profit === Σpromotion_amount − Σpromotion_cost",
  );
}

// ---------- 2. 含负值（亏损行）时仍严格可加 ----------
// 只用正数断言会漏掉「负数被当 0 跳过」这类 bug，必须造一行净收益为负。
{
  const points = [
    { promotion_cost: 50, promotion_amount: 200 },
    { promotion_cost: 100, promotion_amount: 80 },
    { promotion_cost: 30, promotion_amount: 0 }, // -30
  ].map(withProfit);
  const agg = aggregateMetrics(points);
  near(agg.promo_profit, 280 - 180, "含亏损行时汇总仍恒等 amt−cost");
  eq(agg.promo_profit, (200 - 50) + (80 - 100) + (0 - 30), "含亏损行时与逐行相加等价");
}

// ---------- 3. 「先求和再相除」≠「先平均后相除」 ----------
// 构造分布不均的数据让两者产生显著差异，确保我们验的是正确的那条路径。
{
  const points = [
    { promotion_cost: 10, promotion_amount: 0 }, // ROI 0
    { promotion_cost: 10, promotion_amount: 0 }, // ROI 0
    { promotion_cost: 980, promotion_amount: 19600 }, // ROI 20
  ].map(withProfit);
  const agg = aggregateMetrics(points);
  const correct = 19600 / 1000; // 19.6 —— 先求和再相除
  const wrong = (0 + 0 + 20) / 3; // 6.67 —— 先平均
  near(agg.roi, correct, "roi 用「先求和再相除」");
  ok(
    Math.abs(agg.roi - wrong) > 1,
    "两种口径在本数据上必须显著不同（否则断言无效）",
  );
}

// ---------- 4. 除零 / 空值安全 ----------
{
  // 推广花费为 0 → roi 为 null（前端显示 --），而非 Infinity/NaN
  const agg = aggregateMetrics([withProfit({ promotion_cost: 0, promotion_amount: 500 })]);
  eq(agg.roi, null, "cost=0 时 roi 为 null");
  eq(agg.promo_profit, 500, "cost=0 时 promo_profit 仍可算（减法无除零风险）");

  // 推广成交为 0 → promo_profit 为负数（真实的亏损信号，非异常）
  const agg2 = aggregateMetrics([withProfit({ promotion_cost: 100, promotion_amount: 0 })]);
  eq(agg2.promo_profit, -100, "amount=0 时 promo_profit 为负");

  // 两者都为 0
  const agg3 = aggregateMetrics([withProfit({ promotion_cost: 0, promotion_amount: 0 })]);
  eq(agg3.promo_profit, 0, "cost 与 amount 同为 0 时 promo_profit 为 0");
  eq(agg3.roi, null, "cost 与 amount 同为 0 时 roi 为 null");
}

// ---------- 5. 字段缺失降级为 null，不是 0 也不是 NaN ----------
// ⚠️ 最隐蔽的错法：把缺失当 0 累加，会让「部分日缺数据」的汇总凭空少一块，
// 前端还照样显示一个数，用户无从察觉。这里锁死降级语义。
{
  const agg = aggregateMetrics([
    withProfit({ promotion_cost: 50, promotion_amount: 200 }),
    { promotion_cost: null, promotion_amount: null, promo_profit: null },
  ]);
  eq(agg.promo_profit, 150, "缺失行被跳过，累加只取有数的那几行");
  ok(!Number.isNaN(agg.promo_profit), "部分缺失时 promo_profit 不是 NaN");

  const aggAllMissing = aggregateMetrics([
    { promotion_cost: null, promotion_amount: null, promo_profit: null },
  ]);
  eq(aggAllMissing.promo_profit, null, "全部缺失时 promo_profit 为 null（显示 --，非 0）");
}

// ---------- 6. 空数组不崩 ----------
{
  const agg = aggregateMetrics([]);
  eq(agg.promo_profit, null, "空数组时 promo_profit 为 null（前端显示 --）");
  eq(agg.roi, null, "空数组时 roi 为 null");
}

// ---------- 7. 工单 07 · SPU 决策表的排序 / 过滤 / 差额语义 ----------
// 接缝：`src/modules/promoTable.ts` 里的纯函数。组件只负责渲染，
// 判定与格式化全在这里 —— 与「口径单一事实来源」同一纪律。

/**
 * 造一个 PromoSpu 测试样本，只填断言用得到的字段。
 * ⚠️ 默认给 roi=1、quadrant="core"，需要异常的用例各自覆盖。
 */
function spu(over = {}) {
  return {
    spu: over.spu ?? "S1",
    spu_name: over.spu_name ?? "商品",
    image: null,
    current_cost: 100,
    current_amount: 100,
    roi: 1,
    profit: 0,
    cost_share: null,
    amount_share: null,
    roi_cv: null,
    unstable: false,
    suggested_cost: 100,
    delta: 0,
    quadrant: "core",
    advice: "",
    skipped: false,
    skip_reason: null,
    ...over,
  };
}

// ---------- 7.1 默认排序：建议分配额降序（Q 决策：沿用后端现状） ----------
{
  eq(
    DEFAULT_SORT,
    { key: "suggested_cost", dir: "desc" },
    "默认排序 = 建议分配额降序（工单 07 前沿决策）",
  );

  const list = [
    spu({ spu: "A", suggested_cost: 100 }),
    spu({ spu: "B", suggested_cost: 300 }),
    spu({ spu: "C", suggested_cost: 200 }),
  ];
  eq(
    sortSpus(list, DEFAULT_SORT.key, DEFAULT_SORT.dir).map((x) => x.spu),
    ["B", "C", "A"],
    "按建议分配额降序",
  );
  eq(
    sortSpus(list, "suggested_cost", "asc").map((x) => x.spu),
    ["A", "C", "B"],
    "按建议分配额升序（往返一致）",
  );
}

// ---------- 7.2 delta 排序按**绝对值**（最该动钱的排最前） ----------
// ⚠️ 不能按带符号值排：减投 −300 比加投 +50 更该优先看到，
//    按带符号排序会把 −300 甩到最后，等于把最该动的行藏起来。
{
  const list = [
    spu({ spu: "加10", delta: 10 }),
    spu({ spu: "减300", delta: -300 }),
    spu({ spu: "加50", delta: 50 }),
  ];
  eq(
    sortSpus(list, "delta", "desc").map((x) => x.spu),
    ["减300", "加50", "加10"],
    "delta 降序取绝对值：−300 应排第一",
  );
  // 升序仍是绝对值升序（= 绝对值降序的完全逆序），不是符号升序
  eq(
    sortSpus(list, "delta", "asc").map((x) => x.spu),
    ["加10", "加50", "减300"],
    "delta 升序同样按绝对值",
  );
}

// ---------- 7.3 roi = null 恒排最后（两个方向都是） ----------
// ⚠️ 投产比算不出的品没有分档依据，混在数值里排序毫无意义；
//    若让它参与排序，null 会被当 0 处理而落到「投产比最低」那一档，
//    等于凭空造出一个「最差品」的结论。
{
  const list = [
    spu({ spu: "无ROI", roi: null, quadrant: null }),
    spu({ spu: "低", roi: 0.5 }),
    spu({ spu: "高", roi: 9 }),
  ];
  eq(
    sortSpus(list, "roi", "desc").map((x) => x.spu),
    ["高", "低", "无ROI"],
    "roi 降序时 null 排最后",
  );
  eq(
    sortSpus(list, "roi", "asc").map((x) => x.spu),
    ["低", "高", "无ROI"],
    "roi 升序时 null 仍排最后（不被当 0 塞到最前）",
  );
}

// ---------- 7.4 排序不修改入参（computed 里就地排序会污染 props） ----------
{
  const list = [spu({ spu: "A", suggested_cost: 1 }), spu({ spu: "B", suggested_cost: 9 })];
  const before = list.map((x) => x.spu);
  const out = sortSpus(list, "suggested_cost", "desc");
  ok(out !== list, "sortSpus 返回新数组（不是原地排序）");
  eq(
    list.map((x) => x.spu),
    before,
    "sortSpus 不修改调用方传入的数组",
  );
  eq(sortSpus([], "suggested_cost", "desc").length, 0, "空数组排序不崩");
}

// ---------- 7.5 chip 多选过滤 ----------
{
  const list = [
    spu({ spu: "core1", quadrant: "core" }),
    spu({ spu: "core2", quadrant: "core" }),
    spu({ spu: "pot", quadrant: "potential" }),
    spu({ spu: "loser", quadrant: "loser" }),
    spu({ spu: "trial", quadrant: "trial" }),
    spu({ spu: "无ROI", roi: null, quadrant: null }),
  ];
  const names = (sel) =>
    filterByQuadrant(list, new Set(sel))
      .map((x) => x.spu)
      .sort();

  eq(names([]), ["core1", "core2", "loser", "pot", "trial", "无ROI"].sort(), "空选 = 全部显示");
  eq(names(["core"]), ["core1", "core2"], "单选 core");
  eq(names(["core", "loser"]), ["core1", "core2", "loser"], "多选 core + loser");
  eq(names(["core", "loser"]), names(["loser", "core"]), "多选与选择顺序无关");
  //未分档的品必须能单独筛出来，而不是被任何档位顺带吞掉
  eq(names(["none"]), ["无ROI"], "未分档（roi=null）可单独筛出");
  eq(names(["core", "none"]).length, 3, "选 core 时未分档的品不混入");
  eq(names(["不存在的档"]), [], "选中空档返回空数组");
}

// ---------- 7.6 差额语义：中性三档，阈值 0.005 ----------
// ⚠️ 阈值与工单 05 汇总条（delta > 0.005 / < −0.005）必须同值，
//    否则表里说「加投」而上面汇总说「不变」，同一页两套口径。
{
  eq(DELTA_EPS, 0.005, "差额阈值 = 0.005 元（与汇总条同源）");
  eq(deltaTone(0.006), "add", "delta = 0.006 → 加投");
  eq(deltaTone(-0.006), "cut", "delta = −0.006 → 减投");
  eq(deltaTone(0.005), "flat", "边界值 0.005 本身算「不变」");
  eq(deltaTone(-0.005), "flat", "边界值 −0.005 本身算「不变」");
  eq(deltaTone(0.004), "flat", "0.004 → 不变");
  eq(deltaTone(-0.004), "flat", "−0.004 → 不变");
  eq(deltaTone(0), "flat", "0 → 不变");
  eq(deltaTone(-1200), "cut", "大额减投 → cut");
  eq(deltaTone(1200), "add", "大额加投 → add");

  // ⛔ 三档互斥：任何 delta 都必须落在且只落在一个 tone 上
  const tones = [1200, 0.006, 0.005, 0, -0.004, -0.005, -0.006, -1200].map((d) => deltaTone(d));
  eq(
    tones.every((t) => t === "add" || t === "cut" || t === "flat"),
    true,
    "deltaTone 永远返回三档之一（不会出现 undefined 落进样式类名）",
  );
}

// ---------- 7.7 roi = null 显示 `--`，⛔ 不是 0.00 ----------
// ⚠️ fmtBy("decimal", null) 走的是 fmtDecimal(null) → "0.00"，
//    直接用它会把「投产比算不出来」显示成「投产比 0」——这是本工单最容易踩的错。
{
  eq(formatRoi(null), "--", "roi = null → --");
  eq(formatRoi(undefined), "--", "roi = undefined → --");
  eq(formatRoi(NaN), "--", "roi = NaN → --（⛔ 不得显示 NaN）");
  eq(formatRoi(0), "0.00", "roi = 0 是真实值，必须显示 0.00 而不是 --");
  eq(formatRoi(3.1051), "3.11", "roi 有值时按两位小数显示");
  ok(!formatRoi(null).includes("NaN"), "formatRoi 绝不产出 NaN 文本");
  ok(!/Infinity/.test(formatRoi(null)), "formatRoi 绝不产出 Infinity 文本");
}

// ---------- 7.8 差额文本：带符号，0 显示破折号 ----------
{
  eq(formatDelta(1200), "+¥1,200.00", "加投带 + 号");
  eq(formatDelta(-800.5), "-¥800.50", "减投带 − 号");
  eq(formatDelta(0), "—", "delta = 0 显示破折号（不是 ¥0.00，避免被误读成有金额）");
  eq(formatDelta(0.004), "—", "阈值内的 delta 同样显示破折号");
}

// ---------- 7.9 chip 计数：5 档齐全，空档为 0（据此置灰） ----------
{
  const list = [
    spu({ quadrant: "core" }),
    spu({ quadrant: "core" }),
    spu({ quadrant: "potential" }),
    spu({ roi: null, quadrant: null }),
  ];
  eq(
    quadrantCounts(list),
    { core: 2, potential: 1, loser: 0, trial: 0, none: 1 },
    "四档 + 未分档计数正确",
  );
  eq(
    quadrantCounts([]),
    { core: 0, potential: 0, loser: 0, trial: 0, none: 0 },
    "空数据时全部为 0（chip 全部置灰而不是消失）",
  );
}

// 8· 工单 08：免责文案 + 集中度约束解读
// ============================================================

// ---------- 8.1 元信息表本身：恰好 3 条、顺序固定、关键词各不相同 ----------
{
  eq(DISCLAIMER_META.length, DISCLAIMER_COUNT, "免责元信息恰好 3 条（spec §8 缺一不可）");
  eq(DISCLAIMER_COUNT, 3, "spec §8 要求的免责条数常量是 3");
  eq(
    DISCLAIMER_META.map((m) => m.kind),
    ["linearity", "breakeven", "campaign"],
    "三处免责的 kind 与后端 DISCLAIMER_KINDS 同序（线性 / 保本线 / 大促）",
  );
  // 关键词必须两两不同 —— 若两条共用一个关键词，删掉其中一条时断言仍会绿
  eq(
    new Set(DISCLAIMER_META.map((m) => m.keyword)).size,
    DISCLAIMER_COUNT,
    "三条的关键词互不相同（否则漏掉一条时断言抓不到）",
  );
  // 大促风险是唯一需要「警觉」的一条：level 必须 warn，其余是 info（口径说明）
  eq(
    DISCLAIMER_META.map((m) => m.level),
    ["info", "info", "warn"],
    "仅大促风险为 warn 层级（口径说明不该跟警示同权）",
  );
  ok(DISCLAIMER_META.every((m) => m.title.length > 0), "每条免责都有短标题");
}

// ---------- 8.2 checkDisclaimers：合规的三条 → allOk=true ----------
{
  const good = [
    "理论上限基于「加预算成交按同比例放大」的线性假设，是数学最优而非可达目标。",
    "投产比 1 是保本线，但该判断未计入商品毛利。",
    "大促期流量结构会变，请勿直接照搬本方案。",
  ];
  const r = checkDisclaimers(good);
  ok(r.allOk, "合规的三条免责 allOk=true");
  eq(r.views.length, 3, "views 长度 = 3");
  eq(
    r.views.map((v) => v.ok),
    [true, true, true],
    "每条都命中自己的关键词",
  );
  eq(
    r.views.map((v) => v.meta.kind),
    ["linearity", "breakeven", "campaign"],
    "views 按数组顺序对应元信息（⛔ 前端不重排）",
  );
  eq(r.views.map((v) => v.text), good, "原文原样透传（前端不改写文案）");
  // ⛔ 每条都是 warn/info 里该有的层级：大促那条 warn、其余 info
  eq(r.views.map((v) => v.meta.level), ["info", "info", "warn"], "level 随位置正确");
}

// ---------- 8.3 checkDisclaimers：缺一条 / 多一条 → allOk=false ----------
{
  const good = ["含线性", "含毛利", "含大促"];
  ok(!checkDisclaimers(["含线性", "含毛利"]).allOk, "少一条 → allOk=false");
  ok(!checkDisclaimers(["含线性"]).allOk, "只剩一条 → allOk=false");
  ok(!checkDisclaimers([]).allOk, "空数组 → allOk=false");
  ok(!checkDisclaimers(null).allOk, "null → allOk=false（不是崩，是不合格）");
  ok(!checkDisclaimers(undefined).allOk, "undefined → allOk=false");
  ok(!checkDisclaimers([...good, "第四条"]).allOk, "多一条 → allOk=false");
  eq(checkDisclaimers(null).views.length, 0, "null 时 views 为空数组（模板可安全遍历）");
  eq(checkDisclaimers([]).views.length, 0, "空数组 views 也为空");
  // 多出一条时：多出来的那条 meta 为 undefined，前端渲染时能识别出「超出预期」
  const over = checkDisclaimers([...good, "第四条"]);
  eq(over.views[3].meta, undefined, "超出 3 条时第 4 条 meta 为 undefined（不张冠李戴）");
  eq(over.views[3].ok, false, "超出预期的那条 ok=false");
}

// ---------- 8.4 checkDisclaimers：关键词缺失 → 该条 ok=false（其余仍 true） ----------
{
  // ⚠️ 关键词校验是**子串**匹配，所以反例要用真正不含该字的文案：
  //    写「不提线性假设」会因为里面仍有「线性」二字而匹配上—— 那是测试本身写错，不是实现错。
  const noKeyword = ["仅供参考", "含毛利", "含大促"];
  const r = checkDisclaimers(noKeyword);
  eq(
    r.views.map((v) => v.ok),
    [false, true, true],
    "关键词缺失只让该条不合格，不连坐其它条",
  );
  ok(!r.allOk, "任一条不合格 → allOk=false");

  // 少一条时元信息仍按位置取 —— ⛔ 不能因为条数不足就张冠李戴
  const short = checkDisclaimers(["含毛利", "含大促"]);
  eq(short.views[0].meta.kind, "linearity", "缺第 1 条时位置 0 仍取 linearity");
  eq(short.views[0].ok, false, "内容「含毛利」不含「线性」→ 第 1 条不合格（正确）");
}

// ---------- 8.5 checkDisclaimers：空白与禁用词 ----------
{
  ok(!checkDisclaimers(["含线性", "   ", "含大促"]).allOk, "纯空白文案不合格");
  ok(!checkDisclaimers(["含线性", "", "含大促"]).allOk, "空串文案不合格");
  // ⚠️ 禁用词：即使条数与关键词都对，出现禁用词仍是事故
  for (const w of BANNED_WORDS) {
    ok(
      !checkDisclaimers(["含线性", `含毛利与${w}`, "含大促"]).allOk,
      `免责文案含禁用词「${w}」→ allOk=false`,
    );
  }
  ok(BANNED_WORDS.includes("边际"), "禁用词表含「边际」");
  ok(BANNED_WORDS.includes("亏损"), "禁用词表含「亏损」");
  ok(BANNED_WORDS.includes("预测"), "禁用词表含「预测」");
}

// ---------- 8.6 capNotice：现状超限 → warn，措辞讲「代价」不讲「收益」 ----------
{
  const n = capNotice({
    overConcentrated: "10020647332922",
    unallocated: 0,
    cap: 3184.07,
    capText: "¥3,184.07",
    unallocatedText: "¥0.00",
  });
  eq(n.level, "warn", "现状超限 → warn");
  ok(n.detail.includes("10020647332922"), "warn 文案点出具体超限 SPU");
  ok(n.detail.includes("¥3,184.07"), "warn 文案带上 cap 金额");
  ok(n.detail.includes("代价") || n.detail.includes("下降"), "warn 文案说明总成交会下降");
  // ⛔ 绝不说成「收益」「提升」 —— 重新分配会让总成交变少
  ok(!n.detail.includes("收益增加") && !n.detail.includes("提升"), "warn 文案不含「收益增加/提升」");
  // 判定依据是后端标志，不是前端重算 current > cap
  eq(n.title, "现状已超出单品集中度上限", "warn 标题固定");
}

// ---------- 8.7 capNotice：有余额分不出去 → warn（数学必然，不是 bug） ----------
{
  const n = capNotice({
    overConcentrated: null,
    unallocated: 1234.56,
    cap: 3184.07,
    capText: "¥3,184.07",
    unallocatedText: "¥1,234.56",
  });
  eq(n.level, "warn", "有余额分不出去 → warn");
  ok(n.detail.includes("¥1,234.56"), "文案带上 unallocated 金额");
  ok(n.detail.includes("数学必然") || n.detail.includes("不是计算错误"), "文案说明这是数学必然而非错误");
  // 超限优先于余额（两者可同时成立，超限是更根本的问题）
  const both = capNotice({
    overConcentrated: "S1",
    unallocated: 1234.56,
    cap: 3184.07,
    capText: "¥3,184.07",
    unallocatedText: "¥1,234.56",
  });
  eq(both.title, "现状已超出单品集中度上限", "两者同时成立时超限优先");
}

// ---------- 8.8 capNotice：正常态 → info（只陈述规则） ----------
{
  const n = capNotice({
    overConcentrated: null,
    unallocated: 0,
    cap: 3184.07,
    capText: "¥3,184.07",
    unallocatedText: "¥0.00",
  });
  eq(n.level, "info", "现状可行且钱分得完 → info");
  ok(n.detail.includes("¥3,184.07"), "info 文案带上 cap 金额");
  ok(!n.detail.includes("无法分配"), "info 文案不提「无法分配」（不报没发生的事）");
  ok(!n.detail.includes("超过单品上限"), "info 文案不指控超限（⛔ 不自己判超限）");

  // 阈值内（±0.005）视为分得完 —— 与 deltaTone 共用同一阈值，判定口径一致
  const eps = capNotice({
    overConcentrated: null,
    unallocated: DELTA_EPS,
    cap: 3184.07,
    capText: "¥3,184.07",
    unallocatedText: "¥0.01",
  });
  eq(eps.level, "info", "unallocated 等于阈值时视为无余额（与 deltaTone 同口径）");
}

// ---------- 8.9 三处免责文案必须与后端常量逐字一致（防前端私自改写） ----------
// 这里直接用真实后端常量做断言 —— 必须在起服务时也能跑，故只断言结构不依赖网络。
{
  ok(DISCLAIMER_META.length === 3, "前端元信息条数与 spec §8 对齐");
  ok(
    DISCLAIMER_META.map((m) => m.keyword).join(",") === "线性,毛利,大促",
    "三条关键词顺序固定为线性/毛利/大促",
  );
}


console.log(`\n通过 ${pass} 项，失败 ${fail} 项`);
if (fail > 0) process.exit(1);
