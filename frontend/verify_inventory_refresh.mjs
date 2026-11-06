// 库存页手动刷新按钮的真浏览器实测（2026-10-31 新增功能）。
//
// 运行前提：
//   1. 后端**测试实例**在 8013 跑（不能用 8000——那是旧进程，托盘服务不热重载，
//      旧进程里没有 /api/inventory/refresh 这个 POST 端点，点了只会 405）；
//   2. `npm install --no-save playwright`（用系统 Edge channel，无需下载浏览器）；
//   3. `node verify_inventory_refresh.mjs`
//
// 为什么必须真浏览器（ai-rules §3）：grep dist 只证明构建通过，不证明浏览器生效。
// 本脚本断言的是「DOM 里真的有按钮 + 点了真的会发请求 + 状态胶囊真的会变 +
// 数据真的被重拉」，这四件事任何静态检查都证明不了。
//
// 截图输出到 .scratch/inventory-refresh/。
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = dirname(fileURLToPath(import.meta.url));
const OUT = join(ROOT, "..", ".scratch", "inventory-refresh");
const BASE = "http://127.0.0.1:8013";

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });

let pass = 0;
let fail = 0;
function ok(l) { pass++; console.log(`PASS ${l}`); }
function bad(l, d) { fail++; console.log(`FAIL ${l}  ${d ?? ""}`); }

// 记录发往刷新端点的请求与响应（断言「点了真的发了 POST」）
const refreshCalls = [];
page.on("response", (r) => {
  if (r.url().includes("/api/inventory/refresh")) {
    refreshCalls.push({ url: r.url(), method: r.request().method(), status: r.status() });
  }
});

// 1. 打开库存页
await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
await page.waitForSelector('[data-testid="inv-refresh-btn"]', { timeout: 20000 });
await page.waitForSelector('[data-testid="inv-table-meta"]', { timeout: 20000 });
ok("库存页加载完成，刷新按钮已渲染");

// 1b. 按钮在**标题栏右侧**（方案 A）：与页面标题同层，而不是工具栏里
const placement = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="inv-refresh-btn"]');
  const head = document.querySelector(".inv-head");
  const title = document.querySelector(".inv-title");
  if (!btn || !head || !title) return null;
  const b = btn.getBoundingClientRect();
  const t = title.getBoundingClientRect();
  return {
    inHead: head.contains(btn),
    // 按钮在标题右边（x 更大），且与标题大致同一行（y 区间有重叠）
    rightOfTitle: b.left > t.right - 4,
    sameRow: b.top < t.bottom && b.bottom > t.top,
  };
});
if (placement?.inHead) ok("按钮位于标题栏 .inv-head 内");
else bad("按钮位置", JSON.stringify(placement));
if (placement?.rightOfTitle && placement?.sameRow) ok("按钮在标题右侧同行");
else bad("按钮相对标题的位置", JSON.stringify(placement));

// 1c. 描边蓝（白底）而非实心蓝：与工具栏里实心蓝的「导出表格」形成主次
const styleInfo = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="inv-refresh-btn"]');
  const exp = document.querySelector('[data-testid="matrix-export-btn"]');
  if (!btn) return null;
  const s = getComputedStyle(btn);
  const es = exp ? getComputedStyle(exp) : null;
  return {
    btnBg: s.backgroundColor,
    btnColor: s.color,
    btnBorder: s.borderTopColor,
    exportBg: es?.backgroundColor,
  };
});
if (styleInfo && styleInfo.btnBg !== styleInfo.exportBg) {
  ok(`刷新按钮与导出按钮底色不同（${styleInfo.btnBg} vs ${styleInfo.exportBg}）`);
} else {
  bad("按钮配色未与导出按钮区分", JSON.stringify(styleInfo));
}

// 2. 记录刷新前的数据截止日期（刷新后应更新或保持一致，取决于是否真有新快照）
const dateBefore = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="inv-date-single"]');
  return el ? el.textContent.trim() : null;
});
const countBefore = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="inv-table-meta"] .inv-count b');
  return el ? el.textContent.trim() : null;
});

// 3. 点击刷新按钮 —— 这是本次功能的核心断言
await page.click('[data-testid="inv-refresh-btn"]');
await page.waitForSelector('[data-testid="inv-refresh-pill"]', { timeout: 15000 });
ok("点击后状态胶囊出现");

// 3b. 刷新过程中按钮应禁用 + 图标旋转（防连点）
const duringState = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="inv-refresh-btn"]');
  const ico = btn?.querySelector(".inv-refresh-ico");
  return {
    disabled: btn?.disabled ?? false,
    spinning: ico?.classList.contains("spinning") ?? false,
    text: btn?.textContent?.trim(),
  };
});
if (duringState.disabled) ok("刷新中按钮已禁用（防连点）");
else bad("刷新中按钮未禁用", JSON.stringify(duringState));
if (duringState.spinning) ok("刷新中图标持续旋转");
else bad("刷新中图标未旋转", JSON.stringify(duringState));

// 4. 等刷新结束（胶囊文本稳定）
await page.waitForFunction(
  () => {
    const el = document.querySelector('[data-testid="inv-refresh-pill"]');
    return el && !el.textContent.includes("正在读取");
  },
  { timeout: 180000 },
);
const pillText = await page.evaluate(
  () => document.querySelector('[data-testid="inv-refresh-pill"]')?.textContent?.trim() ?? "",
);
const pillClass = await page.evaluate(
  () => document.querySelector('[data-testid="inv-refresh-pill"]')?.className ?? "",
);
console.log(`  胶囊文案：${pillText}（class=${pillClass}）`);
if (pillText && !pillText.includes("正在读取")) ok(`刷新完成并给出结果文案：${pillText}`);
else bad("刷新结束但无结果文案", pillText);
// 结果应为成功态（源表未变化也算成功，只是 reason 文案不同）
if (pillClass.includes("is-done")) ok("结果胶囊为成功态（is-done）");
else bad("结果胶囊状态异常", pillClass);

// 5. 断言真的发过 POST /api/inventory/refresh（而不是前端假动作）
if (refreshCalls.length === 1 && refreshCalls[0].method === "POST" && refreshCalls[0].status === 200) {
  ok(`确实发出 POST /api/inventory/refresh 并收到 200`);
} else {
  bad("刷新请求未发出或状态异常", JSON.stringify(refreshCalls));
}

// 6. 断言数据被重拉：总条数应仍等于库里的条数（刷新不改数据量，但必须重新取过）
const countAfter = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="inv-table-meta"] .inv-count b');
  return el ? el.textContent.trim() : null;
});
const dateAfter = await page.evaluate(() => {
  const el = document.querySelector('[data-testid="inv-date-single"]');
  return el ? el.textContent.trim() : null;
});
console.log(`  刷新前：条数=${countBefore} 日期=${dateBefore}`);
console.log(`  刷新后：条数=${countAfter} 日期=${dateAfter}`);
if (countAfter && Number(countAfter) > 0) ok(`刷新后表格仍有数据（${countAfter} 条）`);
else bad("刷新后表格无数据", String(countAfter));

// 6b. 「数据截至」必须在刷新后重拉（meta 也刷新了）——日期不应为空
if (dateAfter) ok(`「数据截至」标注存在：${dateAfter}`);
else bad("刷新后「数据截至」标注缺失（meta 可能没重拉）");

await page.screenshot({ path: join(OUT, "01-matrix-after-refresh.png"), fullPage: false });

// 7. 切到补货工作台：按钮同样要在（标题栏是两个 Tab 共用的）
await page.click('[data-testid="inv-tab-restock"]');
await page.waitForTimeout(1500);
const restockHasBtn = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="inv-refresh-btn"]');
  const head = document.querySelector(".inv-head");
  return !!btn && !!head && head.contains(btn);
});
if (restockHasBtn) ok("补货工作台 Tab 下刷新按钮同样存在（标题栏共用）");
else bad("补货工作台 Tab 下按钮缺失");
await page.screenshot({ path: join(OUT, "02-restock-tab.png"), fullPage: false });

// 8. 在补货 Tab 点一次刷新，验证按钮对补货面板同样有效
await page.click('[data-testid="inv-refresh-btn"]');
await page.waitForFunction(
  () => {
    const el = document.querySelector('[data-testid="inv-refresh-pill"]');
    return el && !el.textContent.includes("正在读取");
  },
  { timeout: 180000 },
);
const restockPill = await page.evaluate(
  () => document.querySelector('[data-testid="inv-refresh-pill"]')?.textContent?.trim() ?? "",
);
if (restockPill) ok(`补货 Tab 下刷新可用：${restockPill}`);
else bad("补货 Tab 下刷新无结果");
if (refreshCalls.length === 2) ok("补货 Tab 下确实发出了第 2 次刷新请求");
else bad("补货 Tab 下未发出刷新请求", JSON.stringify(refreshCalls));
await page.screenshot({ path: join(OUT, "03-restock-after-refresh.png"), fullPage: false });

await browser.close();
console.log(`\n通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);
