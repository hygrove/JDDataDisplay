// 虚拟滚动纯函数的断言（配合 `npm run verify` 跑）。
//
// 覆盖 utils/virtual-list.ts 的两个纯函数：
// - pickRowHeight：从行高样本里挑代表性行高（众数）；
// - tableSpacers：库的虚拟项区间 → 表格模式两个占位 <tr> 的高度。
//
// 为什么要单独测：占位高度算错 → 滚动时行与位置的映射错位（用户看到错行的
// 数据），这种 bug 表现为「数据不对」，肉眼极难定位，必须有断言兜住。
import { pickRowHeight, tableSpacers } from "./src/utils/virtual-list.ts";

let passed = 0;
let failed = 0;

function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    passed += 1;
    console.log(`  ✓ ${name}`);
  } else {
    failed += 1;
    console.log(`  ✗ ${name}\n      期望 ${e}\n      实际 ${a}`);
  }
}

console.log("— pickRowHeight —");
check("众数：多数一致时取众数", pickRowHeight([69, 69, 70, 69]), 69);
check("全一样：直接取该值", pickRowHeight([69, 69, 69]), 69);
check("忽略非法样本", pickRowHeight([69, 0, -1, NaN, 69]), 69);
check("空样本：返回 0", pickRowHeight([]), 0);
check("取整到 0.1px", pickRowHeight([69.04, 69.02, 70]), 69);
check("偶发高行不拉高整体（众数而非最大值）", pickRowHeight([69, 69, 69, 84]), 69);

console.log("— tableSpacers —");

// 顶部：第一个虚拟项从 0 开始 → 上占位 0
check(
  "顶部：上占位为 0",
  tableSpacers([{ start: 0, end: 69 }, { start: 69, end: 138 }], 107778),
  { top: 0, bottom: 107640 },
);

// 中部：上占位 = 首项 start，下占位 = 总高 − 末项 end
check(
  "中部：上下占位按首末项切分",
  tableSpacers([{ start: 49044, end: 49113 }, { start: 49113, end: 49182 }], 107778),
  { top: 49044, bottom: 107778 - 49182 },
);

// 关键不变量：上占位 + 窗口高度 + 下占位 = 总高度
for (const total of [107778, 1000, 300]) {
  const items = [{ start: 138, end: 207 }, { start: 207, end: 276 }];
  const s = tableSpacers(items, total);
  const windowH = items[items.length - 1].end - items[0].start;
  if (s.top + windowH + s.bottom !== total) {
    failed += 1;
    console.log(`  ✗ 占位不闭合：${JSON.stringify(s)} + 窗口 ${windowH} ≠ 总高 ${total}`);
  } else {
    passed += 1;
  }
}
console.log("  ✓ 占位闭合：上占位 + 窗口 + 下占位 = 总高度（3 组）");

// 末项超出总高时（极端情况）：下占位夹到 0，不出负数
check(
  "末项超出总高：下占位夹到 0",
  tableSpacers([{ start: 0, end: 999 }], 100).bottom,
  0,
);

// 空窗口（数据为空 / 容器高度为 0）：不产生占位，否则会凭空撑出滚动条
check(
  "空窗口：不产生占位",
  tableSpacers([], 107778),
  { top: 0, bottom: 0 },
);

// 下占位不能为负（末项 end 超过总高时）
check(
  "下占位不为负",
  tableSpacers([{ start: 0, end: 999 }], 100).bottom >= 0,
  true,
);

console.log(`\n通过 ${passed} / 失败 ${failed}`);
if (failed > 0) process.exit(1);
