// 纯逻辑断言：环比 compareDelta 的单位、方向色、反转、降级、epsilon 是否正确；
// 顺带断言 kpiValueOf（含退货率派生）。compareDelta/kpiValueOf 都是纯函数，无需后端。
// 运行：在 frontend/ 下 `node verify_compare_delta.mjs`（Node 24 原生剥离 .ts 类型）。
import { compareDelta, kpiValueOf } from "./src/metrics.ts";

let pass = 0;
let fail = 0;
// 按 key 排序后比较，避免对象字段书写顺序造成的假失败；标量/ null 直接比
function stable(o) {
  if (o === null || typeof o !== "object") return JSON.stringify(o);
  return JSON.stringify(Object.keys(o).sort().map((k) => [k, o[k]]));
}
function eq(actual, expected, label) {
  if (stable(actual) === stable(expected)) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}\n  期望 ${stable(expected)}\n  实际 ${stable(actual)}`);
  }
}
function check(cond, label) {
  if (cond) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}`);
  }
}

const hidden = (label) => ({ present: false, arrow: "", tone: "flat", text: "", label });
const flat = (label) => ({ present: true, arrow: "", tone: "flat", text: "持平", label });

// ---------- 求和类：相对百分比，涨=好(绿) 跌=坏(红) ----------
eq(
  compareDelta("amount", "currency", 110, 100, false),
  { present: true, arrow: "▲", tone: "good", text: "+10.0%", label: "较上周期" },
  "amount 涨 → 好绿 ▲ +10.0%",
);
eq(
  compareDelta("amount", "currency", 90, 100, false),
  { present: true, arrow: "▼", tone: "bad", text: "-10.0%", label: "较上周期" },
  "amount 跌 → 坏红 ▼ -10.0%",
);

// ---------- 单日文案 ----------
check(
  compareDelta("amount", "currency", 110, 100, true).label === "较前一天",
  "单日模式 label=较前一天",
);

// ---------- 比率类：百分点差 pp，退货率反转（涨=劣=红） ----------
eq(
  compareDelta("return_rate", "percent", 0.06, 0.05, false),
  { present: true, arrow: "▲", tone: "bad", text: "+1.00pp", label: "较上周期" },
  "退货率涨 → 反转=劣红 ▲ +1.00pp（pp 而非相对%）",
);
eq(
  compareDelta("return_rate", "percent", 0.04, 0.05, false),
  { present: true, arrow: "▼", tone: "good", text: "-1.00pp", label: "较上周期" },
  "退货率跌 → 反转=好绿 ▼ -1.00pp",
);

// ---------- ROI：decimal 走相对 % 而非 pp ----------
eq(
  compareDelta("roi", "decimal", 6, 4, false),
  { present: true, arrow: "▲", tone: "good", text: "+50.0%", label: "较上周期" },
  "ROI(decimal) 涨 → 相对 % +50.0%",
);

// ---------- 降级：null / 分母 0 ----------
eq(compareDelta("roi", "decimal", 3, null, false), hidden("较上周期"), "prev null → 隐藏");
eq(compareDelta("amount", "currency", null, 100, false), hidden("较上周期"), "cur null → 隐藏");
eq(
  compareDelta("amount", "currency", 100, 0, false),
  hidden("较上周期"),
  "相对变化分母 0 → 隐藏（不显示 Infinity）",
);
check(
  compareDelta("return_rate", "percent", 0.05, 0, false).present === true,
  "pp 类 prev=0 仍可显示（0→5pp）",
);

// ---------- epsilon：差值小到会显示成 0.0/0.00 → 持平，不误导 ----------
eq(compareDelta("amount", "currency", 100, 100, false), flat("较上周期"), "完全相等 → 持平");
eq(
  compareDelta("amount", "currency", 100.02, 100, false),
  flat("较上周期"),
  "相对变化 <0.05%（会显示 ±0.0%）→ 持平",
);
eq(
  compareDelta("return_rate", "percent", 0.05002, 0.05, false),
  flat("较上周期"),
  "pp <0.005（会显示 0.00pp）→ 持平",
);

// ---------- kpiValueOf（含退货率派生）----------
eq(kpiValueOf({ amount: 100 }, "amount"), 100, "kpiValueOf amount");
eq(kpiValueOf({ refund_orders: 3, orders: 100 }, "return_rate"), 0.03, "kpiValueOf return_rate 派生");
eq(kpiValueOf({ refund_orders: 3, orders: 0 }, "return_rate"), null, "kpiValueOf return_rate 分母0→null");
eq(kpiValueOf(null, "amount"), null, "kpiValueOf null 记录 → null");
eq(kpiValueOf({ visitors: null }, "visitors"), null, "kpiValueOf null 字段 → null");

console.log(`\n通过 ${pass}，失败 ${fail}`);
if (fail > 0) process.exit(1);
