// 字号缩放自检：字号必须走令牌（calc(Npx * var(--font-scale))），且图表内联
// SVG / echarts 标签的字号保持固定（不缩放），否则坐标写死的标注在放大后会重叠。
//
// 背景（2026-10-26）：全局字号缩放（5 档）落地为 P0 令牌化 + P1 联动 + P2 控件。
//   本脚本负责防止回退：
//   ① 新增内容又写死 `font-size: Npx`（必须 calc + var(--font-scale)）；
//   ② 有人把图表 SVG 标签也接上缩放系数（会重叠）；
//   ③ InventoryMatrixTable 的行高公式 BASE_FS 脱离缩放系数（会裁商品名）；
//   ④ 档位数 / 档位名数量不一致（滑杆刻度与标签对不上）；
//   ⑤ 设置齿轮又被塞回 .logo 行内（会挤压「JD 数据平台」文字）。
//
// 运行：npm run verify:font
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const SRC = join(ROOT, "src");
const STYLE_CSS = join(ROOT, "src", "style.css");

let pass = 0;
let fail = 0;

function ok() {
  pass++;
}

function bad(label, detail) {
  fail++;
  console.log(`FAIL ${label}\n  ${detail}`);
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(vue|css)$/.test(name)) out.push(p);
  }
  return out;
}

const files = walk(SRC);

// ① --font-scale 必须在 style.css :root 定义（默认 1）
const css = readFileSync(STYLE_CSS, "utf8");
if (/--font-scale\s*:\s*1\s*;/.test(css)) ok();
else bad("--font-scale 定义", "style.css :root 缺少 --font-scale: 1;");

// ② 无硬编码 font-size: Npx（一律 calc + var）
let hard = 0;
for (const f of files) {
  const s = readFileSync(f, "utf8");
  const re = /font-size\s*:\s*[0-9]+(?:\.[0-9]+)?px/g;
  let m;
  while ((m = re.exec(s))) {
    hard++;
    bad("硬编码字号", `${f} 中 ${m[0]}`);
  }
}
if (hard === 0) ok();

// ③ 所有 calc 字号都必须含 var(--font-scale)
let calcNoVar = 0;
for (const f of files) {
  const s = readFileSync(f, "utf8");
  const re = /font-size\s*:\s*calc\(([^;]*)\)/g;
  let m;
  while ((m = re.exec(s))) {
    if (!m[1].includes("var(--font-scale)")) {
      calcNoVar++;
      bad("calc 缺缩放系数", `${f} 中 ${m[0]}`);
    }
  }
}
if (calcNoVar === 0) ok();

// ④ 图表内联 SVG 标签（:font-size="tl.fs" 等）不得接缩放系数
let svgScaled = 0;
for (const f of files) {
  const s = readFileSync(f, "utf8");
  const re = /:font-size\s*=\s*"[^"]*var\(--font-scale\)[^"]*"/g;
  let m;
  while ((m = re.exec(s))) {
    svgScaled++;
    bad("SVG 标签不应缩放", `${f} 中 ${m[0]}`);
  }
}
if (svgScaled === 0) ok();

// ⑤ JS 行高公式 BASE_FS 必须跟随缩放系数（否则商品名被裁）
let baseOk = 0;
for (const f of files) {
  if (readFileSync(f, "utf8").includes("12.5 * uiPrefs.scale")) baseOk++;
}
if (baseOk >= 1) ok();
else bad("BASE_FS 跟随缩放系数", "InventoryMatrixTable 的 BASE_FS 未接 uiPrefs.scale");

// ⑥ 档位表自洽：系数严格递增、档位名数量一致、默认档必须是 1、档位数 >= 5
const prefs = readFileSync(join(SRC, "stores", "uiPrefs.ts"), "utf8");
const scaleMatch = prefs.match(/FONT_SCALES\s*=\s*\[([^\]]*)\]/);
const labelMatch = prefs.match(/FONT_SCALE_LABELS\s*=\s*\[([^\]]*)\]/);
if (!scaleMatch || !labelMatch) {
  bad("档位表", "uiPrefs.ts 缺少 FONT_SCALES / FONT_SCALE_LABELS 声明");
} else {
  const scales = scaleMatch[1]
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => !Number.isNaN(n));
  const labels = labelMatch[1]
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  const ascending = scales.every((v, i) => i === 0 || v > scales[i - 1]);
  if (ascending) ok();
  else bad("档位递增", `FONT_SCALES 必须严格递增，实际 ${scales.join(", ")}`);
  if (scales.length === labels.length) ok();
  else bad("档位名数量", `FONT_SCALES(${scales.length}) 与 FONT_SCALE_LABELS(${labels.length}) 数量不一致`);
  if (scales.includes(1)) ok();
  else bad("默认档", "FONT_SCALES 必须包含系数 1（标准档）");
  if (scales.length >= 5) ok();
  else bad("档位数", `期望至少 5 档，实际 ${scales.length} 档`);
}

// ⑦ 设置齿轮不得放回 .logo 行内（会挤压「JD 数据平台」文字）
const appVue = readFileSync(join(SRC, "App.vue"), "utf8");
const logoBlock = appVue.match(/<div class="logo">([\s\S]*?)<\/div>/);
if (!logoBlock) {
  bad("logo 结构", 'App.vue 未找到 <div class="logo"> 块');
} else if (logoBlock[1].includes("SettingsControl")) {
  bad("齿轮位置", "SettingsControl 又被放回 .logo 行内，会挤压「JD 数据平台」文字");
} else {
  ok();
}

console.log(`\nverify:font ${pass} 通过 / ${fail} 失败`);
if (fail > 0) process.exit(1);
