// 指标配置 store：管理「当前模块勾选了哪些指标」，按 moduleId 分别存 localStorage。
// 模块页（ModuleView）与单品分析页（SpuAnalysisView）共用同一份配置，因此天然同步。
import { defineStore } from "pinia";
import { DEFAULT_METRIC_KEYS, METRICS } from "../metrics";

const STORAGE_KEY = "jd.metric-config.v1";

type Store = Record<string, string[]>;

function load(): Store {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Store;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // 存储损坏 / 隐私模式下不可用：直接回落到默认配置，不影响页面
    return {};
  }
}

function persist(data: Store): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* 忽略写入失败 */
  }
}

/** 只保留清单中仍然存在的 key，并按清单顺序排序（兼容版本升级后字段变更）。 */
function normalize(keys: string[]): string[] {
  const set = new Set(keys);
  return METRICS.filter((m) => set.has(m.key as string)).map((m) => m.key as string);
}

interface State {
  selected: Store;
}

export const useMetricsStore = defineStore("metrics", {
  state: (): State => ({ selected: load() }),
  getters: {
    /** 某模块当前勾选的指标 key（顺序 = 指标清单顺序）。 */
    keysFor: (s) => (moduleId: string): string[] => {
      const saved = s.selected[moduleId];
      return saved ? normalize(saved) : [...DEFAULT_METRIC_KEYS];
    },
  },
  actions: {
    set(moduleId: string, keys: string[]) {
      this.selected = { ...this.selected, [moduleId]: normalize(keys) };
      persist(this.selected);
    },
    toggle(moduleId: string, key: string) {
      const cur = new Set(this.keysFor(moduleId));
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      this.set(moduleId, [...cur]);
    },
    reset(moduleId: string) {
      this.set(moduleId, [...DEFAULT_METRIC_KEYS]);
    },
    selectAll(moduleId: string) {
      this.set(moduleId, METRICS.map((m) => m.key as string));
    },
    clear(moduleId: string) {
      this.set(moduleId, []);
    },
  },
});
