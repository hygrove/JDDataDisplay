// 用真实接口数据验证：前端 aggregateMetrics 的聚合口径是否与后端一致。
// 重点验比率类「先求和再相除」—— 这是项目里最容易写错的地方
// （写成「每日比率求平均」在销量分布不均时会明显偏差）。
import { aggregateMetrics } from "./src/metrics.ts";

const SPU = "10023068330393";
const BASE = "http://127.0.0.1:8000/api/module/pop_spu_detail/spu";

let pass = 0;
let fail = 0;
function eq(a, e, l) {
  const A = JSON.stringify(a);
  const E = JSON.stringify(e);
  if (A === E) pass++;
  else {
    fail++;
    console.log(`FAIL ${l}\n  期望 ${E}\n  实际 ${A}`);
  }
}
function close(a, e, l, eps = 1e-6) {
  if (typeof a === "number" && Math.abs(a - e) <= eps) pass++;
  else {
    fail++;
    console.log(`FAIL ${l}\n  期望 ${e}\n  实际 ${a}`);
  }
}

/** 手工独立实现「先分别累加分子分母再相除」 */
function manual(points, num, den) {
  let n = 0;
  let d = 0;
  for (const p of points) {
    n += p[num] ?? 0;
    d += p[den] ?? 0;
  }
  return d ? n / d : null;
}

const months = [
  ["2026-08", "2026-08-01", "2026-08-31"],
  ["2026-09", "2026-09-01", "2026-09-30"],
  ["2026-10", "2026-10-01", "2026-10-05"],
];

for (const [label, start, end] of months) {
  const res = await fetch(`${BASE}/${SPU}/analysis?start=${start}&end=${end}`);
  if (!res.ok) {
    console.log(`跳过 ${label}：HTTP ${res.status}`);
    continue;
  }
  const data = await res.json();
  const pts = data.daily.map((x) => x.metrics);
  const agg = aggregateMetrics(pts);
  console.log(`\n-- ${label}（${pts.length} 天，后端 date_range=${data.date_range.join("~")}）--`);

  // 求和类：与手工累加一致
  const sum = (k) => pts.reduce((s, p) => s + (p[k] ?? 0), 0);
  close(agg.amount, sum("amount"), `${label} 成交金额求和`);
  close(agg.visitors, sum("visitors"), `${label} 访客数求和`);
  close(agg.buyers, sum("buyers"), `${label} 成交客户数求和`);
  close(agg.promotion_cost, sum("promotion_cost"), `${label} 推广花费求和`);

  // 比率类：手工「先求和再相除」
  close(agg.conversion_rate, manual(pts, "buyers", "visitors"), `${label} 转化率=客户/访客`);
  close(agg.avg_price, manual(pts, "amount", "buyers"), `${label} 客单价=金额/客户`);
  close(agg.roi, manual(pts, "promotion_amount", "promotion_cost"), `${label} ROI=推广成交/推广花费`);
  close(agg.promotion_ratio, manual(pts, "promotion_cost", "amount"), `${label} 推广占比=花费/金额`);
  const cr = manual(pts, "search_clicks", "search_impressions");
  if (cr === null) eq(agg.search_click_rate, null, `${label} 搜索点击率分母为 0 -> null`);
  else close(agg.search_click_rate, cr, `${label} 搜索点击率=点击/曝光`);

  // 关键反例检测：「先求和再除」必须 ≠「每日比率求平均」
  const validDaily = pts.filter((p) => p.visitors > 0);
  if (validDaily.length > 1) {
    const dailyAvg = validDaily.reduce((s, p) => s + p.buyers / p.visitors, 0) / validDaily.length;
    if (Math.abs(dailyAvg - agg.conversion_rate) > 1e-9) pass++;
    else {
      fail++;
      console.log(`FAIL ${label} 口径混淆检测失效：结果等于「每日比率平均」`);
    }
    console.log(
      `  转化率：区间先求和再除=${(agg.conversion_rate * 100).toFixed(3)}%` +
        `  每日比率平均=${(dailyAvg * 100).toFixed(3)}%  （两者应不同）`,
    );
  }
  console.log(`  成交金额=${agg.amount.toFixed(2)}  访客数=${agg.visitors}  ROI=${agg.roi?.toFixed(3)}`);
}

// 两个月并排时：变化应按 右−左，且比率类用 pt
const a = await (await fetch(`${BASE}/${SPU}/analysis?start=2026-08-01&end=2026-08-31`)).json();
const b = await (await fetch(`${BASE}/${SPU}/analysis?start=2026-09-01&end=2026-09-30`)).json();
const aggA = aggregateMetrics(a.daily.map((x) => x.metrics));
const aggB = aggregateMetrics(b.daily.map((x) => x.metrics));
console.log("\n-- 8月 vs 9月对比 --");
const amountDiff = aggB.amount - aggA.amount;
const rate = amountDiff / Math.abs(aggA.amount);
console.log(`  成交金额 ${aggA.amount.toFixed(2)} -> ${aggB.amount.toFixed(2)}  ${rate >= 0 ? "+" : "-"}${(Math.abs(rate) * 100).toFixed(1)}%`);
const crA = aggA.conversion_rate;
const crB = aggB.conversion_rate;
const pt = (crB - crA) * 100;
console.log(`  转化率 ${(crA * 100).toFixed(2)}% -> ${(crB * 100).toFixed(2)}%  ${pt >= 0 ? "+" : "−"}${Math.abs(pt).toFixed(2)}pt`);
// pt 与相对变化率必须不同（否则说明用错了口径）
const relRate = ((crB - crA) / crA) * 100;
if (Math.abs(relRate - pt) > 1e-9) pass++;
else {
  fail++;
  console.log("FAIL 百分点与相对变化率不应相等");
}
console.log(`  （若按相对变化率算会是 ${relRate.toFixed(1)}%，与 pt 语义不同，已改用 pt）`);

console.log(`\n通过 ${pass} 项，失败 ${fail} 项`);
if (fail > 0) process.exit(1);
