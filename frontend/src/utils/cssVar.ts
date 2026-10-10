/**
 * 从 CSS 自定义属性（design token）读取真实色值，供 **canvas / SVG 绘制库**使用。
 *
 * ⚠️ 为什么需要这个工具，而不是直接在图表配置里写 `var(--color-brand)`：
 *   CSS 变量只对**CSS 属性**生效。echarts 这类库把颜色当成普通字符串，
 *   最终写进 SVG 的 `fill="..."` 属性或 canvas 的样式里 —— 属性值不是 CSS 样式表，
 *   浏览器不会在那里做 `var()` 替换，结果是颜色静默失效（元素变透明 / 变黑）。
 *   所以必须先把 token 解析成真实色值再交给图表库。
 *
 * ⚠️ 为什么单一事实来源仍然是 `style.css` 的 `:root`：
 *   本工具**不新增任何色值常量**，只做「token 名 → 当前生效色值」的读取。
 *   将来改主题只需改 `:root`，所有图表自动跟随—— token 仍是唯一事实来源。
 *
 * @remarks
 * - 必须在 DOM 已存在、`style.css` 已加载后调用（组件内 `computed` 天然满足：
 *   首次渲染发生在挂载之后）。读不到时返回空串，调用方应给兜底色。
 * - 变量名需带前导 `--`。
 *
 * @example
 * ```ts
 * cssVar("--color-brand");        // "#e1251b"
 * cssVar("--color-nope", "#ccc"); // 不存在 → "#ccc"
 * ```
 */

/**
 * 读取一个 CSS 自定义属性的当前值。
 *
 * @param {string} name - CSS 变量名，须带前导 `--`（如 `--color-brand`）。
 * @param {string} [fallback] - 读取失败或值为空时的兜底色，默认空串。
 * @returns {string} 去掉首尾空白的色值字符串；失败时返回 `fallback`。
 * @throws 不抛出。`document` 不存在（SSR）时同样返回 `fallback`。
 * @example
 * ```ts
 * cssVar("--color-danger", "#ef4444"); // "#ef4444"
 * ```
 */
export function cssVar(name: string, fallback = ""): string {
  if (typeof document === "undefined" || !name.startsWith("--")) return fallback;
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v.trim() || fallback;
  } catch {
    return fallback;
  }
}

/**
 * 把 `#rrggbb` / `#rgb` 形式的色值转成带透明度的 `rgba()`。
 *
 * ⚠️ 为什么要转而不是直接把 token 传给图表库：
 *   象限底色需要「同色但几乎透明」（4% 不透明度），
 *   那样底色能暗示分区又不压过数据点。若为每档另写一个 `--core-bg` token，
 *   主题一改就要同步改两套色，徒增漂移风险。转一次透明度更省事也更可控。
 *
 * @param {string} hex - 色值，需为 `#rgb` 或 `#rrggbb` 形式。
 * @param {number} alpha - 透明度，取值 [0, 1]。
 * @returns {string} `rgba(r, g, b, alpha)`；入参不是 hex 时原样返回。
 * @throws 不抛出。无法解析时返回原字符串，让调用方的兜底逻辑生效。
 * @example
 * ```ts
 * withAlpha("#e1251b", 0.05); // "rgba(225, 37, 27, 0.05)"
 * withAlpha("red", 0.5);      // "red"（非 hex，原样返回）
 * ```
 */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  let h = m[1];
  // 三位简写展开成六位（#abc → #aabbcc），否则后面按 2 位切片会取错
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
