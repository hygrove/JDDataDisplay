// 模块数据 store：每个模块页一个实例，离开页面时清理（reset），
// 不在内存同时保留全部模块数据。
//
// 组合式写法：每个状态字段用 ref 承载，派生量用 computed 承载，
// 所有动作写成闭包内的普通函数（互相调用时直接写函数名，不再用 this），
// 最后一并 return 出去。
import { defineStore } from "pinia";
import { computed, ref } from "vue";
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
 * 模块数据 store（组合式 / Setup Store，模块页专用）。
 *
 * @example
 * ```ts
 * const store = useModuleStore();
 * await store.init("pop_spu_detail");   // 初始化：拉 manifest + 汇总 + 首屏行
 * await store.loadMore();               // 触底加载下一页
 * store.setRange("2026-09-18", "2026-09-24"); // 切区间（自动重新拉行）
 * ```
 */
export const useModuleStore = defineStore("module", () => {
  /** 模块清单（含各模块店铺 / 日期区间 / SPU 数） */
  const manifest = ref<Manifest | null>(null);
  /** 上次批处理状态 */
  const status = ref<BatchStatus | null>(null);
  /** 模块汇总（图表用） */
  const summary = ref<ModuleSummary | null>(null);
  /** 当前模块 id；离开页面会被 reset 清空 */
  const moduleId = ref("");
  /** 店铺过滤；空串 = 全部店铺 */
  const shop = ref("");
  /** 区间起点 YYYY-MM-DD（单日模式时 start == end） */
  const start = ref("");
  /** 区间终点 YYYY-MM-DD */
  const end = ref("");
  /** SPU 号 / 名称的搜索关键词 */
  const keyword = ref("");
  /** 排序字段（指标 key 或 spu/shop） */
  const sortBy = ref("amount");
  /** 排序方向 */
  const sortOrder = ref<"asc" | "desc">("desc");
  /** 已加载的行（翻页追加） */
  const rows = ref<Row[]>([]);
  /** 过滤后的总条数 */
  const total = ref(0);
  /** 已加载到第几页；0 表示还没加载 */
  const page = ref(0);
  /** 是否正在加载行（防重入） */
  const loadingRows = ref(false);
  /** 是否正在加载汇总 */
  const loadingSummary = ref(false);
  /** 是否正在跑手动刷新 */
  const refreshing = ref(false);
  /** 最近一次错误信息 */
  const error = ref("");

  /**
   * 是否还有下一页（用于无限滚动的触底判断）。
   *
   * @returns {boolean} 已加载行数 < 总数时为 true。
   * @example
   * ```ts
   * if (store.hasMore) await store.loadMore();
   * ```
   */
  const hasMore = computed(() => rows.value.length < total.value);

  /**
   * 当前模块在 manifest 里的条目（含 date_range、shops 等元信息）。
   *
   * @returns {import("../types").ModuleManifest | null} 当前模块条目；未找到时为 null。
   * @example
   * ```ts
   * store.currentModule?.date_range; // ["2026-08-14", "2026-09-17"]
   * ```
   */
  const currentModule = computed(
    () => manifest.value?.modules.find((m) => m.module_id === moduleId.value) ?? null,
  );

  /**
   * 当前模块的店铺列表（供店铺下拉框使用）。
   *
   * @returns {string[]} 店铺名数组；模块不存在时为空数组。
   * @example
   * ```ts
   * store.shops; // ["钻芯旗舰店", "卡求旗舰店"]
   * ```
   */
  const shops = computed(() => {
    const mod = manifest.value?.modules.find((m) => m.module_id === moduleId.value);
    return mod?.shops ?? [];
  });

  /**
   * 初始化模块页：清状态 -> 拉 manifest -> 定位默认日期 -> 并行拉汇总与首屏行。
   *
   * @param {string} moduleIdParam - 模块标识。
   * @returns {Promise<void>} 汇总与首屏行都加载完成后 resolve。
   * @throws 不抛出——接口错误写进 error 由页面展示；
   *      只有 manifest 拉取失败会 reject（拿不到模块就没法继续）。
   * @example
   * ```ts
   * onMounted(() => store.init(props.moduleId));
   * ```
   */
  async function init(moduleIdParam: string): Promise<void> {
    // 先 reset：从别的模块切回来时不能残留上一个模块的过滤条件与行数据
    reset();
    moduleId.value = moduleIdParam;
    // manifest 可能已在别的页面拉过（如单品分析页），有就不重复请求
    if (!manifest.value) {
      manifest.value = await fetchManifest();
    }
    const mod = currentModule.value;
    if (mod?.date_range?.[1]) {
      // 默认展示最新一天的数据（单日模式：start == end）
      start.value = mod.date_range[1];
      end.value = mod.date_range[1];
    }
    // 状态是「锦上添花」的信息，不 await：即使 /status 慢也不该拖慢首屏
    fetchStatus().then((st) => (status.value = st)).catch(() => {});
    // 汇总与行数据互不依赖，并行拉取
    await Promise.all([loadSummary(), reloadRows()]);
  }

  /**
   * 拉取模块汇总（图表数据）。
   *
   * @returns {Promise<void>} 完成后 resolve。
   * @throws 不抛出——失败时把错误写进 error。
   * @example
   * ```ts
   * await store.loadSummary();
   * ```
   */
  async function loadSummary(): Promise<void> {
    loadingSummary.value = true;
    try {
      summary.value = await fetchSummary(moduleId.value);
    } catch (e) {
      error.value = String(e);
    } finally {
      loadingSummary.value = false;
    }
  }

  /**
   * 重新加载行数据（清空已加载分页，从第 1 页开始）。
   *
   * @returns {Promise<void>} 首屏行加载完成后 resolve。
   * @example
   * ```ts
   * await store.reloadRows();
   * ```
   */
  async function reloadRows(): Promise<void> {
    page.value = 0;
    rows.value = [];
    total.value = 0;
    await loadMore();
  }

  /**
   * 加载下一页并追加到 rows（无限滚动用）。
   *
   * @returns {Promise<void>} 完成后 resolve。
   * @throws 不抛出——失败时把错误写进 error。
   * @example
   * ```ts
   * // IntersectionObserver 触底时：
   * if (store.hasMore && !store.loadingRows) await store.loadMore();
   * ```
   */
  async function loadMore(): Promise<void> {
    // 防重入：正在加载时直接返回，避免快速滚动触发并发重复请求；
    // 已加载到最后一页（page>0 且 !hasMore）时也不再请求
    if (loadingRows.value || (page.value > 0 && !hasMore.value)) return;
    loadingRows.value = true;
    try {
      const next = page.value + 1;
      const res = await fetchRows(moduleId.value, {
        // 空串转 undefined：不把空查询参数拼进 URL
        shop: shop.value || undefined,
        start: start.value || undefined,
        end: end.value || undefined,
        keyword: keyword.value || undefined,
        sort_by: sortBy.value || undefined,
        sort_order: sortOrder.value,
        page: next,
        page_size: PAGE_SIZE,
      });
      // 第 1 页直接替换（防止切条件后残留旧行），后续页才 concat
      rows.value = next === 1 ? res.rows : rows.value.concat(res.rows);
      total.value = res.total;
      page.value = next;
    } catch (e) {
      error.value = String(e);
    } finally {
      loadingRows.value = false;
    }
  }

  /**
   * 切换店铺过滤并重新加载行。
   *
   * @param {string} shopParam - 店铺名；传空串表示全部店铺。
   * @returns {void} 无返回值（内部异步重载，不阻塞调用方）。
   * @example
   * ```ts
   * store.setShop("钻芯旗舰店");
   * ```
   */
  function setShop(shopParam: string): void {
    shop.value = shopParam;
    void reloadRows();
  }

  /**
   * 设置日期范围并重新加载行（start == end 即单日模式；两者都空即全区间）。
   *
   * @param {string} startParam - 区间起点 YYYY-MM-DD；空串表示不限。
   * @param {string} endParam - 区间终点 YYYY-MM-DD；空串表示不限。
   * @returns {void} 无返回值（内部异步重载）。
   * @example
   * ```ts
   * store.setRange("2026-09-18", "2026-09-24"); // 区间模式
   * store.setRange("2026-09-24", "2026-09-24"); // 单日模式
   * ```
   */
  function setRange(startParam: string, endParam: string): void {
    start.value = startParam;
    end.value = endParam;
    void reloadRows();
  }

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
  function setKeyword(kw: string): void {
    keyword.value = kw;
    void reloadRows();
  }

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
  function setSort(key: string, order: "asc" | "desc"): void {
    sortBy.value = key;
    sortOrder.value = order;
    void reloadRows();
  }

  /**
   * 手动触发后端重跑批处理，完成后刷新全部数据。
   *
   * @returns {Promise<void>} 刷新完成后 resolve。
   * @throws 不抛出——失败时把错误写进 error。
   * @example
   * ```ts
   * await store.refresh(); // 页面「手动刷新」按钮
   * ```
   */
  async function refresh(): Promise<void> {
    refreshing.value = true;
    try {
      // 重跑是同步接口，耗时可能数十秒
      status.value = await postRefresh();
      // 批处理可能新增/删除模块，manifest 必须重新拉
      manifest.value = await fetchManifest();
      await Promise.all([loadSummary(), reloadRows()]);
    } catch (e) {
      error.value = String(e);
    } finally {
      refreshing.value = false;
    }
  }

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
  function reset(): void {
    summary.value = null;
    moduleId.value = "";
    shop.value = "";
    start.value = "";
    end.value = "";
    keyword.value = "";
    sortBy.value = "amount";
    sortOrder.value = "desc";
    rows.value = [];
    total.value = 0;
    page.value = 0;
    error.value = "";
  }

  return {
    // 状态
    manifest,
    status,
    summary,
    moduleId,
    shop,
    start,
    end,
    keyword,
    sortBy,
    sortOrder,
    rows,
    total,
    page,
    loadingRows,
    loadingSummary,
    refreshing,
    error,
    // 派生（getter）
    hasMore,
    currentModule,
    shops,
    // 动作
    init,
    loadSummary,
    reloadRows,
    loadMore,
    setShop,
    setRange,
    setKeyword,
    setSort,
    refresh,
    reset,
  };
});
