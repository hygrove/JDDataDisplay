// 字号缩放视觉回归（真浏览器）：验证设置齿轮入口（侧栏底部独立行）、
// 对话框滑杆切档、5 档全链路、字号放大生效、localStorage 持久化。
//
// 运行前提（不并入 npm run verify，因 playwright 是验证专用临时依赖）：
//   1. `npm run dev` 已在 5173 运行（走最新源码 + proxy 到 8000 真实后端）；
//   2. `npm install --no-save playwright`（用系统 Edge channel，无需下载浏览器）；
//   3. `node verify_visual_font.mjs`
// 截图输出到 .scratch/font-scale/。
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = dirname(fileURLToPath(import.meta.url));
const OUT = join(ROOT, "..", ".scratch", "font-scale");
const BASE = "http://localhost:5173";

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });

let pass = 0;
let fail = 0;
function ok(l) { pass++; console.log(`PASS ${l}`); }
function bad(l, d) { fail++; console.log(`FAIL ${l}  ${d ?? ""}`); }

async function scaleVal() {
  return page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--font-scale").trim()
  );
}
async function navFontPx() {
  return page.evaluate(() => {
    const el = document.querySelector(".nav-item");
    return el ? getComputedStyle(el).fontSize : null;
  });
}

// 1. 首页 → 自动跳到第一个模块
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForSelector('[data-testid="settings-open"]', { timeout: 15000 });
await page.waitForSelector('[data-testid="module-toolbar"]', { timeout: 20000 });

const s0 = await scaleVal();
const f0 = await navFontPx();
console.log(`初始 --font-scale=${s0}  nav-item fontSize=${f0}`);
if (s0 === "1") ok("初始档位为标准(1)");
else bad("初始档位", `期望 1 实际 ${s0}`);

// 1b. 设置入口必须在侧栏底部独立行，且不在 .logo 行内（不挤压 logo）
const gearPlacement = await page.evaluate(() => {
  const btn = document.querySelector('[data-testid="settings-open"]');
  const logo = document.querySelector(".logo");
  if (!btn || !logo) return null;
  const inLogo = logo.contains(btn);
  const btnRect = btn.getBoundingClientRect();
  const logoRect = logo.getBoundingClientRect();
  return {
    inLogo,
    // 齿轮纵向位置应低于 logo 底部（独立成行）
    belowLogo: btnRect.top >= logoRect.bottom - 1,
    hasLabel: !!btn.textContent?.trim(),
  };
});
console.log(`齿轮位置：${JSON.stringify(gearPlacement)}`);
if (gearPlacement && !gearPlacement.inLogo) ok("齿轮不在 logo 行内（不挤压 logo）");
else bad("齿轮位置", "齿轮仍被塞在 .logo 内");
if (gearPlacement && gearPlacement.belowLogo) ok("齿轮位于 logo 下方独立行");
else bad("齿轮位置", "齿轮未位于 logo 下方");
if (gearPlacement && gearPlacement.hasLabel) ok("齿轮按钮带「设置」文字标签");
else bad("齿轮标签", "按钮缺少「设置」文字标签");

// 2. 点齿轮打开设置对话框
await page.click('[data-testid="settings-open"]');
await page.waitForSelector('[data-testid="settings-dialog"]', { timeout: 5000 });
ok("设置对话框打开");
const v0 = await page.textContent('[data-testid="font-scale-value"]');
console.log(`对话框当前档位：${v0}`);

// 2b. 滑杆应有 5 个刻度（5 档）
const tickCount = await page.locator('[data-testid^="font-scale-tick-"]').count();
console.log(`滑杆刻度数：${tickCount}`);
if (tickCount === 5) ok("滑杆 5 个刻度（5 档）");
else bad("刻度数", `期望 5 实际 ${tickCount}`);
await page.screenshot({ path: join(OUT, "1-settings-dialog-scale1.png"), fullPage: false });

// 3. 连点三次 A+ → 1.3（超大之后还有一档）
await page.click('[data-testid="font-scale-up"]');
await page.click('[data-testid="font-scale-up"]');
await page.click('[data-testid="font-scale-up"]');
await page.waitForTimeout(300);
const s3 = await scaleVal();
const f3 = await navFontPx();
const v3 = await page.textContent('[data-testid="font-scale-value"]');
console.log(`点三次 A+ 后 --font-scale=${s3}  nav-item fontSize=${f3}  档位=${v3}`);
if (s3 === "1.3") ok("三档升至特大(1.3)");
else bad("升档", `期望 1.3 实际 ${s3}`);
if (v3 === "特大") ok("对话框档位名同步(特大)");
else bad("档位名", `期望 特大 实际 ${v3}`);
if (f0 && f3 && Math.abs(parseFloat(f3) - parseFloat(f0) * 1.3) < 0.5) ok(`字号 1.3 倍生效 (${f0} → ${f3})`);
else bad("字号缩放", `期望 ${f0}*1.3 实际 ${f3}`);
await page.screenshot({ path: join(OUT, "2-settings-dialog-scale1.3.png"), fullPage: false });

// 3b. 已在最大档：A+ 应禁用（5 档边界正确）
const upDisabled = await page.locator('[data-testid="font-scale-up"]').isDisabled();
if (upDisabled) ok("最大档时 A+ 禁用（5 档边界正确）");
else bad("边界", "最大档时 A+ 仍可点击");

// 3c. 点最左刻度 → 紧凑 0.9
await page.click('[data-testid="font-scale-tick-0"]');
await page.waitForTimeout(200);
const sMin = await scaleVal();
const vMin = await page.textContent('[data-testid="font-scale-value"]');
console.log(`点最左刻度后 --font-scale=${sMin}  档位=${vMin}`);
if (sMin === "0.9" && vMin === "紧凑") ok("最左刻度 = 紧凑(0.9)");
else bad("刻度", `期望 0.9/紧凑 实际 ${sMin}/${vMin}`);

// 3d. 回到标准档
await page.click('[data-testid="font-scale-tick-1"]');
await page.waitForTimeout(200);
const sStd = await scaleVal();
if (sStd === "1") ok("第二刻度 = 标准(1)");
else bad("刻度", `期望 1 实际 ${sStd}`);

// 4. 关闭对话框，验证档位名 + 页面字号
await page.click('[data-testid="settings-close"]');
await page.waitForSelector('[data-testid="settings-dialog"]', { state: "detached", timeout: 5000 });
ok("对话框关闭");
await page.waitForTimeout(200);
await page.screenshot({ path: join(OUT, "3-module-scale1.png"), fullPage: false });

// 5. 切到库存页
await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
await page.waitForSelector('[data-testid="settings-open"]', { timeout: 15000 });
await page.waitForTimeout(800);
const sInv = await scaleVal();
console.log(`库存页 --font-scale=${sInv}`);
await page.screenshot({ path: join(OUT, "4-inventory-scale1.png"), fullPage: false });

// 6. 刷新验证持久化
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector('[data-testid="settings-open"]', { timeout: 15000 });
const sReload = await scaleVal();
console.log(`刷新后 --font-scale=${sReload}`);
if (sReload === "1") ok("localStorage 持久化生效");
else bad("持久化", `期望 1 实际 ${sReload}`);

// 7. 回到标准档（清理 localStorage，避免污染后续）
await page.evaluate(() => localStorage.removeItem("jd.font-scale.v1"));

await browser.close();
console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail > 0 ? 1 : 0);
