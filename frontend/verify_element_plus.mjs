// Element Plus 使用规范校验（EP 使用约定的机器闸门）。
//
// 为什么需要这个脚本：
//   引入 Element Plus 后，最大的新风险是「两套写法并存」——
//   一部分组件用 <el-button>，另一部分用自研 <AppButton>；
//   一部分颜色直接写 var(--el-color-primary)，另一部分写 var(--color-brand)。
//   这正是架构手册 §6.2 反对的「多处各写一份」：改一处漏九处，
//   而且「哪一处才是权威」会随时间漂移，没人说得清。
//
// 本脚本把三条约定变成可执行断言：
//   ① 业务组件不直接写 el-* 组件标签（统一走 components/ui 的语义包装）；
//   ② 业务组件不直接引用 --el-* 变量（颜色只从本项目令牌出）；
//   ③ 令牌映射层是 --el-* 的唯一定义处（style.css 之外不得再定义）。
//
// 运行：npm run verify:ep

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const SRC = join(ROOT, "src");
const STYLE_CSS = join(SRC, "style.css");

let pass = 0;
let fail = 0;

/** 去掉 HTML / JS / CSS 注释，避免注释里的示例被当成真实用法。 */
function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

function ok(label) {
  pass++;
  console.log(`  ✓ ${label}`);
}

function bad(label, detail) {
  fail++;
  console.log(`FAIL ${label}\n  ${detail}`);
}

/** 递归收集 src/ 下的 .vue / .ts 文件（跳过 node_modules 与构建产物）。 */
function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist") continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) out.push(...walk(p));
    else if (/\.(vue|ts)$/.test(name)) out.push(p);
  }
  return out;
}

const files = walk(SRC);

// ─────────────────────────────────────────────────────────────
// ① 业务组件不得直接使用 el-* 组件标签
//    例外：components/ui/ 下的收敛层本身就是「对 EP 的唯一引用点」。
// ─────────────────────────────────────────────────────────────
const EP_TAGS = [
  "el-table",
  "el-table-column",
  "el-pagination",
  "el-tag",
  "el-tooltip",
  "el-popover",
  "el-date-picker",
  "el-button",
  "el-select",
  "el-option",
  "el-icon",
  "el-checkbox",
  "el-config-provider",
  "el-form",
  "el-input",
  "el-input-number",
  "el-dialog",
  "el-drawer",
  "el-switch",
  "el-radio",
  "el-checkbox-group",
  "el-alert",
  "el-card",
  "el-menu",
];

// 收敛层自身：components/ui/ 下的组件是「EP 的唯一合法引用点」
const UI_DIR = join(SRC, "components", "ui");

// 根级 Provider 豁免：App.vue 里的 el-config-provider 是应用根节点的
// locale provider——它必须包住整棵树（否则 EP 组件显示英文），
// 语义上属于「应用装配」而非「某个业务组件里的通用件」，
// 没有对应的语义包装组件。故显式豁免并写明理由。
const ROOT_EXEMPT = new Map([
  ["src/App.vue", new Set(["el-config-provider"])],
]);

for (const file of files) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  // 收敛层目录豁免：它存在的意义就是包装 EP
  if (file.startsWith(UI_DIR)) continue;
  const text = stripComments(readFileSync(file, "utf8"));
  const exempt = ROOT_EXEMPT.get(rel) ?? new Set();
  const hits = [];
  for (const tag of EP_TAGS) {
    if (exempt.has(tag)) continue;
    const re = new RegExp(`<${tag}[\\s/>]`, "g");
    const m = text.match(re);
    if (m) hits.push(`${tag}×${m.length}`);
  }
  if (hits.length === 0) ok(`${rel} 未直接使用 el-* 标签`);
  else
    bad(
      "业务组件直接用了 el-* 标签",
      `${rel} → ${hits.join(", ")}\n` +
        `  请改用 components/ui 的语义包装组件（AppButton / AppNumberInput / AppSelect / AppTag / AppCheckbox）`,
    );
}

// ─────────────────────────────────────────────────────────────
// ② 任何文件都不得直接引用 --el-* 变量
//    颜色只从本项目令牌出：var(--color-brand) 等，由 style.css 映射到 EP。
//    收敛层也不例外——包装组件应引用项目令牌，而不是 EP 变量。
// ─────────────────────────────────────────────────────────────
for (const file of files) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  if (file === STYLE_CSS) continue; // 映射层自身当然要写 --el-*
  const text = stripComments(readFileSync(file, "utf8"));
  const hits = [];
  // 匹配 var(--el-xxx) 形式
  for (const m of text.matchAll(/var\(\s*(--el-[a-z0-9-]+)\s*\)/g)) {
    hits.push(m[1]);
  }
  if (hits.length === 0) ok(`${rel} 未直引 --el-* 变量`);
  else
    bad(
      "直引了 --el-* 变量",
      `${rel} → ${[...new Set(hits)].join(", ")}\n` +
        `  颜色请用本项目令牌（var(--color-brand) 等），由 style.css 单向映射到 EP`,
    );
}

// ─────────────────────────────────────────────────────────────
// ③ --el-* 的唯一定义处是 style.css
//    散落的 --el-* 定义 = 第二套颜色源，映射层会失效。
// ─────────────────────────────────────────────────────────────
for (const file of files) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  if (file === STYLE_CSS) continue;
  const text = stripComments(readFileSync(file, "utf8"));
  // 只看「定义」而非「使用」：--el-xxx: 值;
  const hits = [];
  for (const m of text.matchAll(/^\s*(--el-[a-z0-9-]+)\s*:/gm)) {
    hits.push(m[1]);
  }
  if (hits.length === 0) ok(`${rel} 未定义 --el-* 变量`);
  else
    bad(
      "在收敛层之外定义了 --el-* 变量",
      `${rel} → ${[...new Set(hits)].join(", ")}\n` +
        `  --el-* 的唯一定义处是 src/style.css 的映射层`,
    );
}

// ─────────────────────────────────────────────────────────────
// ④ 收敛层自身必须存在且导出齐五个组件
//    少了任何一个，业务组件就会开始直接写 el-*，回到 ① 的问题。
// ─────────────────────────────────────────────────────────────
const uiIndex = join(UI_DIR, "index.ts");
let uiText = "";
try {
  uiText = readFileSync(uiIndex, "utf8");
} catch {
  bad("收敛层缺失", "src/components/ui/index.ts 不存在");
}

if (uiText) {
  for (const name of ["AppButton", "AppNumberInput", "AppSelect", "AppTag", "AppCheckbox"]) {
    if (uiText.includes(name)) ok(`收敛层导出 ${name}`);
    else bad("收敛层导出缺失", `src/components/ui/index.ts 未导出 ${name}`);
  }
}

// ─────────────────────────────────────────────────────────────
// ⑤ 用到的每个 el-* 都必须在 main.ts 全局注册且有样式引入
//    Vue 对未注册的全局组件不会在构建期报错，只会在运行时渲染成未知元素；
//    AppNumberInput 曾因漏注册 ElInputNumber 而静默失效，静态校验全绿也发现不了。
//    扫描范围：收敛层 + App.vue 的根 Provider。
// ─────────────────────────────────────────────────────────────
const mainText = readFileSync(join(SRC, "main.ts"), "utf8");

/** el-input-number → ElInputNumber */
function toPascal(tag) {
  return tag
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}

/** 去掉注释后再收集 el-* 标签，避免注释里的示例被当成真实使用。 */
function usedEpTags(file) {
  const text = stripComments(readFileSync(file, "utf8"));
  return [...new Set([...text.matchAll(/<el-([a-z-]+)[\s/>]/g)].map((m) => `el-${m[1]}`))];
}

for (const file of files) {
  const rel = relative(ROOT, file).replace(/\\/g, "/");
  if (!file.startsWith(UI_DIR) && rel !== "src/App.vue") continue;
  for (const tag of usedEpTags(file)) {
    const pascal = toPascal(tag);
    const stylePath = `components/${tag.replace(/^el-/, "")}/style/css`;
    if (mainText.includes(`component("${pascal}"`)) ok(`${rel} 使用的 ${tag} 已注册`);
    else
      bad(
        "使用了未注册的 el-* 组件",
        `${rel} → <${tag}>，但 main.ts 没有 component("${pascal}", ...)\n` +
          `  Vue 只会在运行时把它渲染成未知元素，构建期不报错`,
      );
    if (mainText.includes(stylePath)) ok(`${rel} 使用的 ${tag} 已引入样式`);
    else
      bad(
        "缺少 EP 组件样式引入",
        `${rel} → <${tag}>，但 main.ts 没有 import "element-plus/es/${stylePath}"\n` +
          `  组件会带默认外观渲染，脱离本项目的字号/圆角收敛`,
      );
  }
}

console.log(`\nverify:ep 通过 ${pass} / 失败 ${fail}`);
if (fail > 0) process.exit(1);
