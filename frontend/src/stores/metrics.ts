// 指标配置 store：管理「当前模块勾选了哪些指标」，按 moduleId 分别存 localStorage。
// 模块页（ModuleView）与单品分析页（SpuAnalysisView）共用同一份配置，因此天然同步。
import { defineStore } from "pinia";
import { DEFAULT_METRIC_KEYS, METRICS } from "../metrics";

/** localStorage 存储键；v1 表示结构版本，将来改结构时升 v2 并做迁移 */
const STORAGE_KEY = "jd.metric-config.v1";

/** 存储结构：moduleId -> 勾选的指标 key 数组 */
type Store = Record<string, string[]>;

/**
 * 从 localStorage 读取已保存的勾选配置。
 *
 * @returns {Store} moduleId -> key 数组；没有存档、存档损坏、
 *      或隐私模式下 localStorage 不可用时返回空对象（调用方会回落到默认勾选）。
 * @throws 无——内部已捕获所有异常，保证「存储不可用」不会让页面白屏。
 * @example
 * ```ts
 * load(); // {} 或 { pop_spu_detail: ["amount", "buyers", ...] }
 * ```
 */
function load(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Store;
    // 校验解析结果是对象：存档可能被手改成数组/字符串等非法结构
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // 存储损坏 / 隐私模式下不可用：直接回落到默认配置，不影响页面
    return {};
  }
}

/**
 * 把勾选配置写回 localStorage。
 *
 * @param {Store} data - 待持久化的完整配置对象。
 * @returns {void} 无返回值。
 * @throws 无——写入失败（配额满、隐私模式）被静默忽略，
 *      因为这只是「偏好记忆」，丢失不应影响功能。
 * @example
 * ```ts
 * persist({ pop_spu_detail: ["amount"] });
 * ```
 */
function persist(data: Store): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* 忽略写入失败 */
  }
}

/**
 * 规范化 key 列表：只保留清单中仍然存在的 key，并按清单顺序排序。
 *
 * @remarks
 * 为什么需要它：版本升级后可能删掉/新增指标，旧存档里会残留已下线的 key，
 * 直接拿去渲染会查不到 spec 而报错；另外存档顺序可能乱，需要统一成清单顺序。
 *
 * @param {string[]} keys - 原始 key 数组（可能含无效 key、顺序任意）。
 * @returns {string[]} 过滤并按 METRICS 顺序排序后的 key 数组。
 * @example
 * ```ts
 * normalize(["visitors", "不存在的指标", "amount"]);
 * // ["amount", "visitors"]
 * ```
 */
function normalize(keys: string[]): string[] {
  const set = new Set(keys);
  return METRICS.filter((m) => set.has(m.key as string)).map((m) => m.key as string);
}

/**
 * store 状态：仅一个字段，保存全模块的配置字典。
 */
interface State {
  selected: Store;
}

/**
 * 指标配置 store。
 *
 * @example
 * ```ts
 * const store = useMetricsStore();
 * store.keysFor("pop_spu_detail");            // 读取当前勾选
 * store.toggle("pop_spu_detail", "orders");   // 切换某一项
 * store.reset("pop_spu_detail");              // 恢复默认 8 项
 * ```
 */
export const useMetricsStore = defineStore("metrics", {
  state: (): State => ({ selected: load() }),
  getters: {
    /**
     * 取某模块当前勾选的指标 key（顺序 = 指标清单顺序）。
     *
     * @param {State} s - store 状态（由 Pinia 注入）。
     * @returns {(moduleId: string) => string[]} 接收 moduleId 返回 key 数组的函数；
     *      该模块没有存档时返回默认勾选的副本（用展开复制，避免调用方改到 DEFAULT_METRIC_KEYS）。
     * @example
     * ```ts
     * useMetricsStore().keysFor("pop_spu_detail"); // ["amount", "buyers", ...]
     * ```
     */
    keysFor: (s) => (moduleId: string): string[] => {
      const saved = s.selected[moduleId];
      // 返回副本：防止外部直接 push/splice 污染常量数组
      return saved ? normalize(saved) : [...DEFAULT_METRIC_KEYS];
    },
  },
  actions: {
    /**
     * 整体设置某模块的勾选指标（会立即持久化）。
     *
     * @param {string} moduleId - 模块标识。
     * @param {string[]} keys - 新的 key 数组（会先经 normalize 过滤排序）。
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * store.set("pop_spu_detail", ["amount", "roi"]);
     * ```
     */
    set(moduleId: string, keys: string[]) {
      // 整体替换对象引用（而不是就地改属性），确保 Vue 能侦测到变化
      this.selected = { ...this.selected, [moduleId]: normalize(keys) };
      persist(this.selected);
    },
    /**
     * 切换单个指标的勾选状态（已勾选则取消，未勾选则加上）。
     *
     * @param {string} moduleId - 模块标识。
     * @param {string} key - 要切换的指标 key。
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * store.toggle("pop_spu_detail", "orders"); // 成交单量：勾选 <-> 取消
     * ```
     */
    toggle(moduleId: string, key: string) {
      const cur = new Set(this.keysFor(moduleId));
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      // 复用 set 走同一条持久化路径，避免两处各写一遍 localStorage
      this.set(moduleId, [...cur]);
    },
    /**
     * 恢复某模块的默认勾选（8 项）。
     *
     * @param {string} moduleId - 模块标识。
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * store.reset("pop_spu_detail");
     * ```
     */
    reset(moduleId: string) {
      this.set(moduleId, [...DEFAULT_METRIC_KEYS]);
    },
    /**
     * 勾选全部指标（15 项）。
     *
     * @param {string} moduleId - 模块标识。
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * store.selectAll("pop_spu_detail");
     * ```
     */
    selectAll(moduleId: string) {
      this.set(moduleId, METRICS.map((m) => m.key as string));
    },
    /**
     * 清空某模块的全部勾选（页面会提示「未勾选任何指标」）。
     *
     * @param {string} moduleId - 模块标识。
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * store.clear("pop_spu_detail");
     * ```
     */
    clear(moduleId: string) {
      this.set(moduleId, []);
    },
  },
});
