// 模块数据 store：每个模块页一个实例，离开页面时清理（reset），
// 不在内存同时保留全部模块数据。
import { defineStore } from "pinia";
import { fetchManifest, fetchRows, fetchSummary, fetchStatus, postRefresh } from "../api";
import type {
  BatchStatus,
  Manifest,
  ModuleSummary,
  Row,
} from "../types";

/** 每页行数：300 是「一次请求体积」与「首屏渲染量」的平衡值（后端上限 500） */
const PAGE_SIZE = 300;

/**
 * store 状态结构。
 */
interface State {
  /** 模块清单（含各模块店铺 / 日期区间 / SPU 数） */
  manifest: Manifest | null;
  /** 上次批处理状态 */
  status: BatchStatus | null;
  /** 模块汇总（图表用） */
  summary: ModuleSummary | null;
  /** 当前模块 id；离开页面会被 reset 清空 */
  moduleId: string;
  /** 店铺过滤；空串 = 全部店铺 */
  shop: string;
  /** 区间起点 YYYY-MM-DD（单日模式时 start == end） */
  start: string;
  /** 区间终点 YYYY-MM-DD */
  end: string;
  /** SPU 号 / 名称的搜索关键词 */
  keyword: string;
  /** 排序字段（指标 key 或 spu/shop） */
  sortBy: string;
  /** 排序方向 */
  sortOrder: "asc" | "desc";
  /** 已加载的行（翻页追加） */
  rows: Row[];
  /** 过滤后的总条数 */
  total: number;
  /** 已加载到第几页；0 表示还没加载 */
  page: number;
  /** 是否正在加载行（防重入） */
  loadingRows: boolean;
  /** 是否正在加载汇总 */
  loadingSummary: boolean;
  /** 是否正在跑手动刷新 */
  refreshing: boolean;
  /** 最近一次错误信息 */
  error: string;
}

/**
 * 模块数据 store（模块页专用）。
 *
 * @example
 * ```ts
 * const store = useModuleStore();
 * await store.init("pop_spu_detail");   // 初始化：拉 manifest + 汇总 + 首屏行
 * await store.loadMore();               // 触底加载下一页
 * store.setRange("2026-09-18", "2026-09-24"); // 切区间（自动重新拉行）
 * ```
 */
export const useModuleStore = defineStore("module", {
  state: (): State => ({
    manifest: null,
    status: null,
    summary: null,
    moduleId: "",
    shop: "",
    start: "",
    end: "",
    keyword: "",
    // 默认按成交金额倒序：运营最关心「卖得最好的排前面」
    sortBy: "amount",
    sortOrder: "desc",
    rows: [],
    total: 0,
    page: 0,
    loadingRows: false,
    loadingSummary: false,
    refreshing: false,
    error: "",
  }),
  getters: {
    /**
     * 是否还有下一页（用于无限滚动的触底判断）。
     *
     * @param {State} s - store 状态。
     * @returns {boolean} 已加载行数 < 总数时为 true。
     * @example
     * ```ts
     * if (store.hasMore) await store.loadMore();
     * ```
     */
    hasMore: (s) => s.rows.length < s.total,
    /**
     * 当前模块在 manifest 里的条目（含 date_range、shops 等元信息）。
     *
     * @param {State} s - store 状态。
     * @returns {import("../types").ModuleManifest | null} 当前模块条目；未找到时为 null。
     * @example
     * ```ts
     * store.currentModule?.date_range; // ["2026-08-14", "2026-09-17"]
     * ```
     */
    currentModule(s) {
      return s.manifest?.modules.find((m) => m.module_id === s.moduleId) ?? null;
    },
    /**
     * 当前模块的店铺列表（供店铺下拉框使用）。
     *
     * @param {State} s - store 状态。
     * @returns {string[]} 店铺名数组；模块不存在时为空数组。
     * @example
     * ```ts
     * store.shops; // ["钻芯旗舰店", "卡求旗舰店"]
     * ```
     */
    shops(s): string[] {
      const mod = s.manifest?.modules.find((m) => m.module_id === s.moduleId);
      return mod?.shops ?? [];
    },
  },
  actions: {
    /**
     * 初始化模块页：清状态 -> 拉 manifest -> 定位默认日期 -> 并行拉汇总与首屏行。
     *
     * @param {string} moduleId - 模块标识。
     * @returns {Promise<void>} 汇总与首屏行都加载完成后 resolve。
     * @throws 不抛出——接口错误写进 state.error 由页面展示；
     *      只有 manifest 拉取失败会 reject（拿不到模块就没法继续）。
     * @example
     * ```ts
     * onMounted(() => store.init(props.moduleId));
     * ```
     */
    async init(moduleId: string) {
      // 先 reset：从别的模块切回来时不能残留上一个模块的过滤条件与行数据
      this.reset();
      this.moduleId = moduleId;
      // manifest 可能已在别的页面拉过（如单品分析页），有就不重复请求
      if (!this.manifest) {
        this.manifest = await fetchManifest();
      }
      const mod = this.currentModule;
      if (mod?.date_range?.[1]) {
        // 默认展示最新一天的数据（单日模式：start == end）
        this.start = mod.date_range[1];
        this.end = mod.date_range[1];
      }
      // 状态是「锦上添花」的信息，不 await：即使 /status 慢也不该拖慢首屏
      fetchStatus().then((st) => (this.status = st)).catch(() => {});
      // 汇总与行数据互不依赖，并行拉取
      await Promise.all([this.loadSummary(), this.reloadRows()]);
    },
    /**
     * 拉取模块汇总（图表数据）。
     *
     * @returns {Promise<void>} 完成后 resolve。
     * @throws 不抛出——失败时把错误写进 state.error。
     * @example
     * ```ts
     * await store.loadSummary();
     * ```
     */
    async loadSummary() {
      this.loadingSummary = true;
      try {
        this.summary = await fetchSummary(this.moduleId);
      } catch (e) {
        this.error = String(e);
      } finally {
        this.loadingSummary = false;
      }
    },
    /**
     * 重新加载行数据（清空已加载分页，从第 1 页开始）。
     *
     * @returns {Promise<void>} 首屏行加载完成后 resolve。
     * @example
     * ```ts
     * await store.reloadRows();
     * ```
     */
    async reloadRows() {
      this.page = 0;
      this.rows = [];
      this.total = 0;
      await this.loadMore();
    },
    /**
     * 加载下一页并追加到 rows（无限滚动用）。
     *
     * @returns {Promise<void>} 完成后 resolve。
     * @throws 不抛出——失败时把错误写进 state.error。
     * @example
     * ```ts
     * // IntersectionObserver 触底时：
     * if (store.hasMore && !store.loadingRows) await store.loadMore();
     * ```
     */
    async loadMore() {
      // 防重入：正在加载时直接返回，避免快速滚动触发并发重复请求；
      // 已加载到最后一页（page>0 且 !hasMore）时也不再请求
      if (this.loadingRows || (this.page > 0 && !this.hasMore)) return;
      this.loadingRows = true;
      try {
        const next = this.page + 1;
        const res = await fetchRows(this.moduleId, {
          // 空串转 undefined：不把空查询参数拼进 URL
          shop: this.shop || undefined,
          start: this.start || undefined,
          end: this.end || undefined,
          keyword: this.keyword || undefined,
          sort_by: this.sortBy || undefined,
          sort_order: this.sortOrder,
          page: next,
          page_size: PAGE_SIZE,
        });
        // 第 1 页直接替换（防止切条件后残留旧行），后续页才 concat
        this.rows = next === 1 ? res.rows : this.rows.concat(res.rows);
        this.total = res.total;
        this.page = next;
      } catch (e) {
        this.error = String(e);
      } finally {
        this.loadingRows = false;
      }
    },
    /**
     * 切换店铺过滤并重新加载行。
     *
     * @param {string} shop - 店铺名；传空串表示全部店铺。
     * @returns {void} 无返回值（内部异步重载，不阻塞调用方）。
     * @example
     * ```ts
     * store.setShop("钻芯旗舰店");
     * ```
     */
    setShop(shop: string) {
      this.shop = shop;
      void this.reloadRows();
    },
    /**
     * 设置日期范围并重新加载行（start == end 即单日模式；两者都空即全区间）。
     *
     * @param {string} start - 区间起点 YYYY-MM-DD；空串表示不限。
     * @param {string} end - 区间终点 YYYY-MM-DD；空串表示不限。
     * @returns {void} 无返回值（内部异步重载）。
     * @example
     * ```ts
     * store.setRange("2026-09-18", "2026-09-24"); // 区间模式
     * store.setRange("2026-09-24", "2026-09-24"); // 单日模式
     * ```
     */
    setRange(start: string, end: string) {
      this.start = start;
      this.end = end;
      void this.reloadRows();
    },
    /**
     * 设置搜索关键词并重新加载行。
     *
     * @param {string} kw - SPU 号或商品名称的模糊关键词；空串表示不搜索。
     * @returns {void} 无返回值（内部异步重载）。
     * @example
     * ```ts
     * store.setKeyword("保温杯");
     * ```
     */
    setKeyword(kw: string) {
      this.keyword = kw;
      void this.reloadRows();
    },
    /**
     * 设置排序字段与方向，并重新加载行。
     *
     * @param {string} key - 排序字段（指标 key 或 "spu" / "shop"）。
     * @param {"asc" | "desc"} order - 排序方向。
     * @returns {void} 无返回值（内部异步重载）。
     * @example
     * ```ts
     * store.setSort("amount", "desc"); // 按成交金额倒序
     * ```
     */
    setSort(key: string, order: "asc" | "desc") {
      this.sortBy = key;
      this.sortOrder = order;
      void this.reloadRows();
    },
    /**
     * 手动触发后端重跑批处理，完成后刷新全部数据。
     *
     * @returns {Promise<void>} 刷新完成后 resolve。
     * @throws 不抛出——失败时把错误写进 state.error。
     * @example
     * ```ts
     * await store.refresh(); // 页面「手动刷新」按钮
     * ```
     */
    async refresh() {
      this.refreshing = true;
      try {
        // 重跑是同步接口，耗时可能数十秒
        this.status = await postRefresh();
        // 批处理可能新增/删除模块，manifest 必须重新拉
        this.manifest = await fetchManifest();
        await Promise.all([this.loadSummary(), this.reloadRows()]);
      } catch (e) {
        this.error = String(e);
      } finally {
        this.refreshing = false;
      }
    },
    /**
     * 清空模块相关状态（切换模块 / 离开页面时调用）。
     *
     * @remarks
     * 刻意**保留** manifest / status / loading 标志：manifest 换模块后仍能复用，
     * 不需要每次重新请求；只清与「当前模块数据」相关的部分。
     *
     * @returns {void} 无返回值。
     * @example
     * ```ts
     * onUnmounted(() => store.reset());
     * ```
     */
    reset() {
      this.summary = null;
      this.moduleId = "";
      this.shop = "";
      this.start = "";
      this.end = "";
      this.keyword = "";
      this.sortBy = "amount";
      this.sortOrder = "desc";
      this.rows = [];
      this.total = 0;
      this.page = 0;
      this.error = "";
    },
  },
});
