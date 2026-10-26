// 用户偏好 store：字号缩放档位。
//
// 为什么独立成 store 而不是塞进 stores/ui.ts：
// ui.ts 承载「路由级加载遮罩」这类瞬时态；字号档位是**持久化偏好**，
// 且需要被纯展示组件（如 InventoryMatrixTable 的行高公式）响应式读取，
// 与遮罩语义不同，故独立。
//
// 缩放系数的落点：style.css 的 :root --font-scale（所有 font-size 都是
// calc(Npx * var(--font-scale))）。这里只负责「读档 → 写 CSS 变量 → 持久化」。
import { defineStore } from "pinia";
import { ref } from "vue";

/** localStorage 存储键；v1 表示结构版本，将来改结构时升 v2 并做迁移。 */
export const FONT_SCALE_KEY = "jd.font-scale.v1";

/**
 * 五档预设（紧凑 / 标准 / 大 / 超大 / 特大），缩放系数。
 *
 * 档位间距统一为 0.1，最大档 1.3（比标准大 30%），供高分辨率/小屏下
 * 看不清正文的场景使用。系数越大文字越大，故数组必须严格递增。
 */
export const FONT_SCALES = [0.9, 1, 1.1, 1.2, 1.3] as const;

/** 各档位的中文名（与 FONT_SCALES 下标一一对应）。 */
export const FONT_SCALE_LABELS = ["紧凑", "标准", "大", "超大", "特大"] as const;

/** 默认档（标准，系数 1 = 不改任何现状）。 */
export const FONT_SCALE_DEFAULT = 1;

/**
 * 从 localStorage 读取已保存的档位，无记录或非法值时回退默认。
 *
 * @returns {number} 缩放系数，保证落在 FONT_SCALES 内。
 * @example
 * ```ts
 * readStoredScale(); // 1.1（用户上次选「大」）
 * ```
 */
export function readStoredScale(): number {
  try {
    const raw = localStorage.getItem(FONT_SCALE_KEY);
    const v = raw === null ? FONT_SCALE_DEFAULT : Number(raw);
    return (FONT_SCALES as readonly number[]).includes(v) ? v : FONT_SCALE_DEFAULT;
  } catch {
    // 隐私模式 / localStorage 被禁用时抛异常，回退默认档。
    return FONT_SCALE_DEFAULT;
  }
}

/**
 * 全局字号偏好 store（组合式 / Setup Store）。
 *
 * @example
 * ```ts
 * const uiPrefs = useUiPrefsStore();
 * uiPrefs.scale;      // 当前缩放系数
 * uiPrefs.step(1);    // 放大一档（写 CSS 变量 + 持久化）
 * uiPrefs.reset();    // 回到标准档
 * ```
 */
export const useUiPrefsStore = defineStore("uiPrefs", () => {
  /** 当前缩放系数（响应式）；组件依赖它重算行高等派生值。 */
  const scale = ref<number>(readStoredScale());

  /** 当前档位下标（0..4），供控件高亮/禁用边界。 */
  function index(): number {
    const i = FONT_SCALES.indexOf(scale.value as (typeof FONT_SCALES)[number]);
    return i < 0 ? 1 : i;
  }

  /**
   * 应用档位：写 CSS 变量（全站字号即时生效）+ 持久化。
   *
   * @param {number} v - 目标缩放系数，须在 FONT_SCALES 内。
   * @returns {void}
   */
  function apply(v: number): void {
    scale.value = v;
    document.documentElement.style.setProperty("--font-scale", String(v));
    try {
      localStorage.setItem(FONT_SCALE_KEY, String(v));
    } catch {
      // 持久化失败不阻断使用（仅本次会话生效）。
    }
  }

  /**
   * 按档位步进（-1 缩小、+1 放大），到边界后停住不循环。
   *
   * @param {number} dir - 步进方向：-1 或 +1。
   * @returns {void}
   * @example
   * ```ts
   * uiPrefs.step(1); // 标准 → 大
   * uiPrefs.step(-1); // 大 → 标准
   * ```
   */
  function step(dir: 1 | -1): void {
    const next = index() + dir;
    if (next < 0 || next >= FONT_SCALES.length) return;
    apply(FONT_SCALES[next]);
  }

  /** 回到标准档（系数 1）。 */
  function reset(): void {
    apply(FONT_SCALE_DEFAULT);
  }

  return { scale, index, apply, step, reset };
});
