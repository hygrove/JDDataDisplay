// 库存指标勾选 store：管理「库存矩阵展示哪些 per_city / national 指标」，持久化到 localStorage。
//
// 与 stores/metrics.ts 刻意区分：
// - 原 metrics store 是按 moduleId 存的「POP 商品明细」指标；库存是完全独立的指标体系，
//   且库存没有 moduleId 维度（店铺/日期是筛选条件，不是模块），因此单独一个全局勾选集即可。
// - 身份列（identity）恒可见、不可勾选，不进本 store；本 store 只管非锁定的 per_city / national。
//
// 组合式写法，与 metrics store 保持一致：selected 用 ref 承载，动作是闭包内普通函数。
import { defineStore } from "pinia";
import { ref } from "vue";

/** localStorage 存储键；v1 表示结构版本。 */
const STORAGE_KEY = "jd.inventory-metric-config.v1";

/**
 * 从 localStorage 读取已保存的勾选配置。
 *
 * @returns {string[] | null} 勾选的库存指标 key 数组；无存档、损坏或不可用返回 null
 *      （调用方会回落到后端 default_visible）。
 * @throws 无——内部捕获所有异常，保证「存储不可用」不会白屏。
 */
function load(): string[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : null;
  } catch {
    return null;
  }
}

/**
 * 把勾选配置写回 localStorage。
 *
 * @param {string[]} keys - 待持久化的 key 数组。
 * @returns {void} 无返回值；写入失败被静默忽略（偏好记忆丢失不影响功能）。
 */
function persist(keys: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
  } catch {
    /* 忽略写入失败 */
  }
}

/**
 * 库存指标勾选 store（组合式 / Setup Store）。
 *
 * @example
 * ```ts
 * const store = useInventoryMetricsStore();
 * store.toggle("city_stock");   // 切换「现货库存」勾选
 * store.reset(["city_stock"]);  // 恢复为指定默认
 * ```
 */
export const useInventoryMetricsStore = defineStore("inv-metrics", () => {
  /** 用户显式勾选的非锁定指标 key（null 表示「尚未触碰、用后端默认」） */
  const selected = ref<string[] | null>(load());

  /**
   * 取当前勾选（顺序无关，视图层会按 meta 顺序重排）。
   *
   * @returns {string[] | null} 已勾选 key 数组；从未触碰过返回 null。
   * @example
   * ```ts
   * useInventoryMetricsStore().keys(); // null 或 ["city_stock", "city_out_y"]
   * ```
   */
  function keys(): string[] | null {
    return selected.value;
  }

  /**
   * 整体设置勾选（会立即持久化）。
   *
   * @param {string[]} ks - 新的 key 数组。
   * @returns {void} 无返回值。
   */
  function set(ks: string[]): void {
    selected.value = [...ks];
    persist(selected.value);
  }

  /**
   * 切换单个指标勾选状态（已勾选则取消，未勾选则加上）。
   *
   * @param {string} key - 要切换的指标 key。
   * @returns {void} 无返回值。
   */
  function toggle(key: string): void {
    const cur = new Set(selected.value ?? []);
    if (cur.has(key)) cur.delete(key);
    else cur.add(key);
    set([...cur]);
  }

  /**
   * 恢复默认（清空显式勾选，让视图回落到后端 default_visible）。
   *
   * @returns {void} 无返回值。
   */
  function reset(): void {
    selected.value = null;
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* 忽略 */
    }
  }

  return { selected, keys, set, toggle, reset };
});
