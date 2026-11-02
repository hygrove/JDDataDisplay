// 纯逻辑断言：结构区共用纯函数 withBarWidth（横向条宽）。
// 只断言 metrics.ts 里的 withBarWidth：metrics.ts 无扩展名的运行时依赖，Node 可原生加载
// （与 verify_compare_delta.mjs 同理）。thumb / fmtShare 在 modules/index.ts —— 该文件对
// ../metrics 是无扩展名运行时 import，裸 Node ESM 解析不了（Vite 能），且此文件所有格式化/
// 路径函数历来由 `npm run build`（vue-tsc）+ 浏览器实测验证，不建 node 断言，此处保持一致。
// 运行：在 frontend/ 下 `node verify_structure_helpers.mjs`（Node 24 原生剥离 .ts 类型）。
import { withBarWidth } from "./src/metrics.ts";

let pass = 0;
let fail = 0;
function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) pass++;
  else {
    fail++;
    console.log(`FAIL ${label}\n  期望 ${e}\n  实际 ${a}`);
  }
}

// ---------- withBarWidth ----------
eq(withBarWidth([]), [], "空列表 → 空数组");
eq(
  withBarWidth([{ amount: 100 }, { amount: 50 }]),
  [
    { amount: 100, width: 100 },
    { amount: 50, width: 50 },
  ],
  "100/50 → 100%/50%（相对榜内最大值）",
);
eq(
  withBarWidth([{ amount: 100 }, { amount: 1 }]),
  [
    { amount: 100, width: 100 },
    { amount: 1, width: 4 },
  ],
  "1% 太矮 → 保底 4%（矮条仍可见）",
);
eq(withBarWidth([{ amount: 0 }]), [{ amount: 0, width: 0 }], "全零 → 宽度 0（不除零）");
eq(
  withBarWidth([{ amount: 30, spu: "A" }]),
  [{ amount: 30, spu: "A", width: 100 }],
  "保留原字段 + 附加 width",
);

console.log(`\n通过 ${pass}，失败 ${fail}`);
if (fail > 0) process.exit(1);
