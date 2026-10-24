// 文字可读性自检：最浅文字令牌必须保持「看得清」的定标，且新增内容不许再手写更浅的灰。
//
// 背景（2026-10-24 用户反馈）：「库存管理模块的最浅字体都快看不清了」，
//   根因是 --color-text-5 (#94a3b8，白底 2.6:1) / --color-text-6 (#c4ceda，1.9:1) 太淡。
//   修复在令牌层（frontend/src/style.css）一次性完成；本脚本负责**防止回退**：
//   以后新增内容若又写了个更浅的灰色、或把文字令牌拿去当边框线，CI/本地 verify 立刻报错。
//
// 运行：npm run verify:tokens
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const SRC = join(ROOT, "src");
const STYLE_CSS = join(ROOT, "src", "style.css");

let pass = 0;
let fail = 0;

function ok(label) {
  pass++;
}

function bad(label, detail) {
  fail++;
  console.log(`FAIL ${label}\n  ${detail}`);
}

function eq(actual, expected, label) {
  if (actual === expected) ok(label);
  else bad(label, `期望 ${expected}\n  实际 ${actual}`);
}

// ---------------------------------------------------------------- 颜色工具

function parseHex(hex) {
  let h = hex.trim().replace(/^#/, "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  if (h.length === 8) h = h.slice(0, 6);
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function channel(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

/** WCAG 相对亮度。 */
function luminance(hex) {
  const rgb = parseHex(hex);
  if (!rgb) return null;
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

/** WCAG 对比度（1–21）。 */
function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  if (la === null || lb === null) return null;
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

// ---------------------------------------------------------------- 读令牌

const css = readFileSync(STYLE_CSS, "utf8");
const tokens = new Map();
for (const m of css.matchAll(/(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
  tokens.set(m[1], m[2].toLowerCase());
}

const TEXT_LADDER = [
  "--color-text-strong",
  "--color-text",
  "--color-text-2",
  "--color-text-3",
  "--color-text-4",
  "--color-text-5",
  "--color-text-6",
];

// 阶梯存在 + 严格单调（越靠后越浅）
for (const name of TEXT_LADDER) {
  if (tokens.has(name)) ok(`令牌存在 ${name}`);
  else bad(`令牌存在 ${name}`, "style.css 里找不到该令牌");
}

for (let i = 1; i < TEXT_LADDER.length; i++) {
  const darker = tokens.get(TEXT_LADDER[i - 1]);
  const lighter = tokens.get(TEXT_LADDER[i]);
  if (!darker || !lighter) continue;
  const ld = luminance(darker);
  const ll = luminance(lighter);
  if (ld < ll) ok(`阶梯单调 ${TEXT_LADDER[i - 1]} → ${TEXT_LADDER[i]}`);
  else bad(`阶梯单调 ${TEXT_LADDER[i - 1]} → ${TEXT_LADDER[i]}`, `亮度未递增（${darker} vs ${lighter}）`);
}

// 对比度下限：白底（卡片）+ 斑马底（--color-surface-2 #f8fafc）
const SURFACES = ["#ffffff", "#f8fafc"];
const MIN_CONTRAST = {
  "--color-text-4": 4.5,
  "--color-text-5": 4.5,
  "--color-text-6": 4.0,
};
for (const [name, min] of Object.entries(MIN_CONTRAST)) {
  const v = tokens.get(name);
  if (!v) continue;
  for (const bg of SURFACES) {
    const c = contrast(v, bg);
    if (c !== null && c >= min - 1e-9) ok(`对比度 ${name} on ${bg} = ${c.toFixed(2)} ≥ ${min}`);
    else bad(`对比度 ${name} on ${bg}`, `实测 ${c === null ? "无法解析" : c.toFixed(2)}，要求 ≥ ${min}（值 ${v}）`);
  }
}

// 分隔线令牌：必须存在，且比最弱文字更浅（线条层级与文字层级解耦）
const divider = tokens.get("--color-divider");
if (divider) {
  ok("令牌存在 --color-divider");
  const ld = luminance(divider);
  const lt = luminance(tokens.get("--color-text-6"));
  if (ld !== null && lt !== null && ld > lt) ok(`分隔线比文字浅 ${divider} > ${tokens.get("--color-text-6")}`);
  else bad("分隔线比文字浅", `${divider} 应比 ${tokens.get("--color-text-6")} 更浅`);
} else {
  bad("令牌存在 --color-divider", "虚线/分隔线必须用独立令牌，不能借文字令牌（文字加深后虚线会过重）");
}

// 分隔线强调档：必须比普通分隔线深（hover / 强调细线）
const dividerStrong = tokens.get("--color-divider-strong");
if (dividerStrong && divider) {
  const ls = luminance(dividerStrong);
  const ld = luminance(divider);
  if (ls !== null && ld !== null && ls < ld) ok(`分隔线强调档更深 ${dividerStrong} < ${divider}`);
  else bad("分隔线强调档更深", `${dividerStrong} 应比 ${divider} 更深`);
} else {
  bad("令牌存在 --color-divider-strong", "强调分隔线（hover 描边等）需要独立令牌");
}

// ---------------------------------------------------------------- 源码扫描

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(vue|ts)$/.test(name)) out.push(p);
  }
  return out;
}

// 装饰性颜色白名单：非文字用途（图标字形 / SVG 连线），不参与文字对比度判定。
// ⚠️ 新增条目必须在注释里写清「为什么不是文字」。
const DECORATIVE_ALLOW = new Set([
  ".card.g-extra .cico", // 卡片右上角琥珀色图标字形（图标，非文字）
  ".mc-arrow .line", // 月度对比箭头连线的兜底色（SVG currentColor，非文字）
]);

function styleBlocks(text) {
  return [...text.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
}

/** 粗粒度切分 CSS 规则：返回 [{ selector, body }]。 */
function cssRules(cssText) {
  const rules = [];
  for (const chunk of cssText.split("}")) {
    const brace = chunk.indexOf("{");
    if (brace < 0) continue;
    rules.push({ selector: chunk.slice(0, brace).split("*/").pop().trim(), body: chunk.slice(brace + 1) });
  }
  return rules;
}

for (const file of walk(SRC)) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  const text = readFileSync(file, "utf8");

  // ① 文字令牌不得用于边框 / 分隔线
  const lines = text.split(/\r?\n/);
  lines.forEach((line, idx) => {
    if (/border/i.test(line) && /var\(--color-text-/.test(line)) {
      bad("文字令牌不得用于边框", `${rel}:${idx + 1} → ${line.trim()}\n  请改用 --color-divider 或 --color-border-*`);
    } else {
      ok("文字令牌未用于边框");
    }
  });

  // ② <style> 里手写的浅色 hex（color: #xxxxxx）必须够深
  for (const block of styleBlocks(text)) {
    for (const rule of cssRules(block)) {
      for (const m of rule.body.matchAll(/(^|[;{\s])color\s*:\s*([^;]+);/g)) {
        const value = m[2].trim();
        const hex = value.match(/^#([0-9a-fA-F]{3,8})$/);
        if (!hex) continue;
        const raw = value.toLowerCase();
        if (raw === "#fff" || raw === "#ffffff") continue; // 白字必然压在不透明底色上
        if (DECORATIVE_ALLOW.has(rule.selector)) continue;
        const c = contrast(value, "#ffffff");
        if (c !== null && c >= 4.5) ok(`手写文字色够深 ${value}（${rule.selector}，${c.toFixed(2)}:1）`);
        else
          bad(
            "手写文字色太浅",
            `${rel} ${rule.selector} → ${value}（白底 ${c === null ? "?" : c.toFixed(2)}:1，要求 ≥ 4.5）\n` +
              `  最浅文字请用 var(--color-text-5) / var(--color-text-6)，别手写浅灰`,
          );
      }
    }
  }

  // ③ SVG <text> 的 fill 必须够深（SVG 读不到 CSS 变量，只能硬编码 → 守住别写太浅）
  for (const m of text.matchAll(/<text[^>]*\bfill="(#[0-9a-fA-F]{3,8})"/g)) {
    const c = contrast(m[1], "#ffffff");
    if (c !== null && c >= 4.5) ok(`SVG 文字色够深 ${m[1]}（${c.toFixed(2)}:1）`);
    else bad("SVG 文字色太浅", `${rel} → fill="${m[1]}"（白底 ${c === null ? "?" : c.toFixed(2)}:1，要求 ≥ 4.5）`);
  }
}

console.log(`\n通过 ${pass} / 失败 ${fail}`);
if (fail > 0) process.exit(1);
