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

const PAGE_SIZE = 300;

interface State {
  manifest: Manifest | null;
  status: BatchStatus | null;
  summary: ModuleSummary | null;
  moduleId: string;
  shop: string; // 空 = 全部店铺
  start: string; // 区间起点 YYYY-MM-DD（单日模式 start==end）
  end: string; // 区间终点 YYYY-MM-DD
  keyword: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
  rows: Row[];
  total: number;
  page: number;
  loadingRows: boolean;
  loadingSummary: boolean;
  refreshing: boolean;
  error: string;
}

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
    hasMore: (s) => s.rows.length < s.total,
    currentModule(s) {
      return s.manifest?.modules.find((m) => m.module_id === s.moduleId) ?? null;
    },
    shops(s): string[] {
      const mod = s.manifest?.modules.find((m) => m.module_id === s.moduleId);
      return mod?.shops ?? [];
    },
  },
  actions: {
    async init(moduleId: string) {
      this.reset();
      this.moduleId = moduleId;
      if (!this.manifest) {
        this.manifest = await fetchManifest();
      }
      const mod = this.currentModule;
      if (mod?.date_range?.[1]) {
        // 默认展示最新一天的数据（单日模式：start==end）
        this.start = mod.date_range[1];
        this.end = mod.date_range[1];
      }
      fetchStatus().then((st) => (this.status = st)).catch(() => {});
      await Promise.all([this.loadSummary(), this.reloadRows()]);
    },
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
    async reloadRows() {
      this.page = 0;
      this.rows = [];
      this.total = 0;
      await this.loadMore();
    },
    async loadMore() {
      if (this.loadingRows || (this.page > 0 && !this.hasMore)) return;
      this.loadingRows = true;
      try {
        const next = this.page + 1;
        const res = await fetchRows(this.moduleId, {
          shop: this.shop || undefined,
          start: this.start || undefined,
          end: this.end || undefined,
          keyword: this.keyword || undefined,
          sort_by: this.sortBy || undefined,
          sort_order: this.sortOrder,
          page: next,
          page_size: PAGE_SIZE,
        });
        this.rows = next === 1 ? res.rows : this.rows.concat(res.rows);
        this.total = res.total;
        this.page = next;
      } catch (e) {
        this.error = String(e);
      } finally {
        this.loadingRows = false;
      }
    },
    setShop(shop: string) {
      this.shop = shop;
      void this.reloadRows();
    },
    // 设置日期范围（start==end 即单日模式；空 start+空 end 即全区间）
    setRange(start: string, end: string) {
      this.start = start;
      this.end = end;
      void this.reloadRows();
    },
    setKeyword(kw: string) {
      this.keyword = kw;
      void this.reloadRows();
    },
    setSort(key: string, order: "asc" | "desc") {
      this.sortBy = key;
      this.sortOrder = order;
      void this.reloadRows();
    },
    async refresh() {
      this.refreshing = true;
      try {
        this.status = await postRefresh();
        this.manifest = await fetchManifest();
        await Promise.all([this.loadSummary(), this.reloadRows()]);
      } catch (e) {
        this.error = String(e);
      } finally {
        this.refreshing = false;
      }
    },
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
