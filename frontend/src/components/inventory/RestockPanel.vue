<script setup lang="ts">
// 补货工作台：销速驱动的采购清单（按城市分组），参数（覆盖天数 / 安全系数 / 销速窗口）可调，
// 支持导出 xlsx（单 sheet，城市作为一列，格式与页面展示一致）。
//
// 数据口径（与后端 backend.jobs.alert_rules 对齐）：
//   建议量 = 窗口出库 ÷ 窗口有货天数 × 覆盖天数 × (1+安全系数) − 现货 − 在途
// 只展示建议量 > 0 的行；下柜 SKU 完全排除。
// 参数变化 / 父级 shop/date 变化时防抖 300ms 重拉（销速窗口切换立即生效）。
import { onMounted, ref, watch } from "vue";
import { fetchRestockList } from "../../api";
import type { RestockGroup, RestockRow, RestockResponse } from "../../types";

const props = defineProps<{
  /** 店铺过滤（空串 = 全部店铺，与库存矩阵 tab 一致） */
  shop: string;
  /** 快照日期（空串 = 最新快照） */
  date: string;
}>();

/** 算法参数草稿（安全系数用百分比编辑，提交时换算回小数）。 */
const days = ref(9);
const safetyPct = ref(20);
const windowDays = ref(14);

/** 清单响应。 */
const data = ref<RestockResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

/** 防抖计时句柄。 */
let timer: number | undefined;

/** 拉取补货清单（参数与 shop/date 一起下发）。 */
async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchRestockList({
      shop: props.shop || undefined,
      date: props.date || undefined,
      params: {
        days: days.value,
        safety: safetyPct.value / 100,
        window: windowDays.value,
      },
    });
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

/** 任意输入变化：防抖 300ms 重拉（避免连续击键打爆接口）。 */
function scheduleLoad(): void {
  if (timer !== undefined) clearTimeout(timer);
  timer = window.setTimeout(() => {
    timer = undefined;
    void load();
  }, 300);
}

// shop/date 由父级传入：变化立即重拉（切店铺不该有 300ms 迟滞）
watch(
  () => [props.shop, props.date],
  () => void load(),
);
// 参数变化：防抖重拉
watch([days, safetyPct, windowDays], scheduleLoad);

onMounted(() => void load());

/** 数值格式化：整数带千分位；空 → "—"。 */
function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Math.round(n * 100) / 100 === Math.round(n)
    ? Math.round(n).toLocaleString("zh-CN")
    : (Math.round(n * 100) / 100).toLocaleString("zh-CN");
}

/** 预警档位徽章文案与样式。 */
function statusMeta(s: RestockRow["status"]): { text: string; cls: string } {
  if (s === "alert") return { text: "预警", cls: "st-alert" };
  if (s === "incoming") return { text: "在途", cls: "st-incoming" };
  return { text: "健康", cls: "st-healthy" };
}

/** 缺口进度条填充比例：(现货+在途) ÷ 目标库存，0-100。 */
function gapFill(r: RestockRow): number {
  if (!r.target) return 0;
  return Math.max(0, Math.min(100, Math.round(((r.stock + r.purchase) / r.target) * 100)));
}

/** 当前是否没有任何建议行（空态提示）。 */
const empty = ref(false);
watch(data, (d) => {
  empty.value = !!d && d.groups.length === 0;
});

/** 导出中 / 导出提示。 */
const exporting = ref(false);
const exportMsg = ref<string | null>(null);

/**
 * 导出 xlsx：单 sheet、城市作为一列，行结构 = 页面展示（城市分组头 → 数据行 → 小计 → 总计）。
 *
 * @remarks
 * xlsx 库动态 import（仅首次点导出才加载，不进主包）。表内数据格式与前端展示一致
 * （2026-10 grilling 拍板 D11：不按城市分 sheet，集中一个 sheet）。
 */
async function exportExcel(): Promise<void> {
  if (!data.value || exporting.value) return;
  exporting.value = true;
  exportMsg.value = null;
  try {
    const XLSX = await import("xlsx");
    const aoa: (string | number)[][] = [
      ["城市", "SKU", "商品名称", "店铺", "现货", "在途", "日均销速", "目标库存", "建议补货量", "状态"],
    ];
    const d = data.value;
    for (const g of d.groups as RestockGroup[]) {
      for (const r of g.rows) {
        aoa.push([
          g.city,
          String(r.sku ?? ""),
          String(r.sku_name ?? ""),
          String(r.shop ?? ""),
          r.stock,
          r.purchase,
          r.velocity,
          r.target,
          r.qty,
          statusMeta(r.status).text,
        ]);
      }
      aoa.push([`${g.city} 小计（${g.rows.length} SKU）`, "", "", "", "", "", "", "", g.subtotal, ""]);
    }
    aoa.push(["8 城总计", "", "", "", "", "", "", "", d.grand_total, ""]);
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    // 数字列在 Excel 里右对齐更易扫读；建议补货量列宽给足
    ws["!cols"] = [{ wch: 14 }, { wch: 16 }, { wch: 28 }, { wch: 12 }, { wch: 8 }, { wch: 8 }, { wch: 10 }, { wch: 10 }, { wch: 12 }, { wch: 8 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "补货建议");
    const name = `补货建议_${d.date || "latest"}.xlsx`;
    XLSX.writeFile(wb, name);
    exportMsg.value = "已导出 " + name;
  } catch (e) {
    exportMsg.value = "导出失败：" + String(e);
  } finally {
    exporting.value = false;
    if (exportMsg.value && exportMsg.value.startsWith("已导出")) {
      window.setTimeout(() => (exportMsg.value = null), 2400);
    }
  }
}
</script>

<template>
  <div class="rp" data-testid="restock-panel">
    <!-- 参数条：覆盖天数 / 安全系数 / 销速窗口 + 公式说明 + 导出 -->
    <div class="rp-head">
      <div class="rp-params">
        <label class="rp-param" title="补的货要撑到下次采购到货：7 天周期 + 到货延迟缓冲">
          <span>覆盖天数</span>
          <input v-model.number="days" type="number" min="1" max="60" data-testid="restock-days">
        </label>
        <label class="rp-param" title="应对销速波动的缓冲（20% = 多备 2 成）">
          <span>安全系数</span>
          <input v-model.number="safetyPct" type="number" min="0" max="500" step="5" data-testid="restock-safety">%
        </label>
        <label class="rp-param" title="用哪个周期的出库量估算日均出库件数（有货天数修正缺货期低估）">
          <span>计算平均出库件数/天 周期选择</span>
          <select v-model.number="windowDays" data-testid="restock-window">
            <option :value="7">7 日</option>
            <option :value="14">14 日</option>
            <option :value="30">30 日</option>
          </select>
        </label>
        <span class="rp-formula">
          建议量 = 周期内平均每天出库件数 ÷ 周期内有货天数 × 期望库存覆盖天数 × (1 + 安全系数) − 现货库存 − 在途库存
        </span>
        <span class="rp-formula">
          举例：近14日出库件数10件，近14日有货天数5天(平均每天出库10÷5=2件)，期望库存覆盖天数7天，安全系数20%，现货库存5件， 在途库存2件，则建议补货量 = 10 ÷ 5 × 7 × (1 + 20%) − 5 − 2 = 9.8件
        </span>
      </div>
      <div class="rp-actions">
        <span v-if="exportMsg" class="rp-export-msg" data-testid="restock-export-msg">{{ exportMsg }}</span>
        <button
          class="rp-btn primary"
          :disabled="!data || empty || exporting"
          data-testid="restock-export-btn"
          @click="exportExcel"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>
          {{ exporting ? "导出中…" : "导出 Excel" }}
        </button>
      </div>
    </div>

    <div v-if="error" class="rp-error" data-testid="restock-error">{{ error }}</div>
    <div v-if="loading && !data" class="rp-loading">加载中…</div>

    <!-- 空态：参数下没有任何需要补的行 -->
    <div v-if="empty" class="rp-empty" data-testid="restock-empty">
      当前筛选与参数下没有需要补货的 SKU（现货 + 在途已覆盖目标库存）。
    </div>

    <div v-if="data && data.groups.length" class="rp-wrap" data-testid="restock-table">
      <table class="rp-table">
        <thead>
          <tr>
            <th>城市</th>
            <th>商品名称 / SKU</th>
            <th>店铺</th>
            <th class="num">现货</th>
            <th class="num">在途</th>
            <th class="num">日均销速</th>
            <th class="num">目标库存</th>
            <th class="num">建议补货量</th>
            <th>状态</th>
            <th>缺口</th>
          </tr>
        </thead>
        <template v-for="g in data.groups" :key="g.code">
          <tbody>
            <tr class="group-row">
              <td :colspan="10">
                {{ g.city }}
                <span class="group-sub">阈值 {{ g.threshold }} · {{ g.rows.length }} SKU · 小计 <b>{{ g.subtotal }}</b> 件</span>
              </td>
            </tr>
            <tr v-for="r in g.rows" :key="g.code + '-' + r.sku" class="data-row">
              <td></td>
              <td>
                <span class="name" :title="r.sku_name ?? ''">{{ r.sku_name ?? "—" }}</span>
                <span class="sku">{{ r.sku ?? "" }}</span>
              </td>
              <td>{{ r.shop ?? "—" }}</td>
              <td class="num">{{ fmt(r.stock) }}</td>
              <td class="num">{{ fmt(r.purchase) }}</td>
              <td class="num">{{ fmt(r.velocity) }} 件/天</td>
              <td class="num">{{ fmt(r.target) }}</td>
              <td class="num qty">+{{ fmt(r.qty) }}</td>
              <td><span class="st" :class="statusMeta(r.status).cls">{{ statusMeta(r.status).text }}</span></td>
              <td>
                <div class="gapbar" :title="'现货+在途 已覆盖目标 ' + gapFill(r) + '%'">
                  <i :style="{ width: gapFill(r) + '%' }"></i>
                </div>
              </td>
            </tr>
          </tbody>
        </template>
        <tfoot>
          <tr class="grand">
            <td colspan="7">8 城总计（{{ data.params.days }} 天 × {{ Math.round(data.params.safety * 100) }}% × {{ data.params.window }} 日窗口）</td>
            <td class="num qty" data-testid="restock-grand-total">+{{ fmt(data.grand_total) }}</td>
            <td colspan="2"><span class="grand-sub">本周建议采购总量</span></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.rp-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-lg, 14px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  margin-bottom: 12px;
}
.rp-params {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--color-text-4);
}
.rp-param {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.rp-param input,
.rp-param select {
  height: 26px;
  padding: 0 7px;
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  font-size: 12.5px;
  color: var(--color-text-2);
  background: var(--color-surface);
  font-variant-numeric: tabular-nums;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.rp-param input {
  width: 54px;
  text-align: center;
}
.rp-param input:focus,
.rp-param select:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.rp-formula {
  font-size: 11px;
  color: var(--color-text-5);
  background: var(--color-surface-2);
  border: 1px dashed var(--color-border-2);
  padding: 4px 10px;
  border-radius: 6px;
}
.rp-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.rp-export-msg {
  font-size: 12px;
  color: var(--color-text-3);
}
.rp-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: var(--radius-md, 8px);
  border: 1px solid var(--color-border-2);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.rp-btn svg {
  width: 14px;
  height: 14px;
}
.rp-btn:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.rp-btn.primary {
  background: var(--color-brand);
  border-color: var(--color-brand);
  color: #fff;
  font-weight: 600;
}
.rp-btn.primary:hover:not(:disabled) {
  background: var(--color-brand-dark);
  color: #fff;
}
.rp-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.rp-error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  border-radius: var(--radius-md, 8px);
  padding: 10px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}
.rp-loading,
.rp-empty {
  padding: 26px 0;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-5);
  background: var(--color-surface);
  border: 1px dashed var(--color-border-2);
  border-radius: var(--radius-lg, 14px);
}
.rp-wrap {
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-lg, 14px);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  overflow: auto;
  max-height: calc(100vh - 320px);
  min-height: 200px;
}
.rp-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12.5px;
}
.rp-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--color-brand-tint, #eff6ff);
  color: var(--color-brand-dark, #1d4ed8);
  font-size: 12px;
  font-weight: 700;
  padding: 9px 10px;
  border-bottom: 2px solid var(--color-brand-border-2, #dbeafe);
  text-align: left;
  white-space: nowrap;
}
.rp-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--color-border-2);
  color: var(--color-text-2);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  vertical-align: middle;
}
.rp-table .num {
  text-align: right;
}
.data-row:hover td {
  background: var(--color-surface-2);
}
.name {
  display: inline-block;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--color-text-strong);
  vertical-align: bottom;
}
.sku {
  display: block;
  font-size: 11.5px;
  color: var(--color-text-3);
}
.qty {
  font-weight: 700;
  color: var(--color-brand-dark, #1d4ed8);
  font-size: 13.5px;
}
.group-row td {
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.group-sub {
  margin-left: 10px;
  font-weight: 400;
  color: var(--color-text-5);
}
.group-sub b {
  color: var(--color-brand-dark, #1d4ed8);
}
.st {
  display: inline-block;
  padding: 1px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.st-alert {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
}
.st-incoming {
  color: #d97706;
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.st-healthy {
  color: #16a34a;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}
.gapbar {
  width: 84px;
  height: 7px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  overflow: hidden;
}
.gapbar i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #93c5fd, var(--color-brand));
}
.grand td {
  position: sticky;
  bottom: 0;
  background: var(--color-brand);
  color: #fff;
  font-weight: 700;
  border-bottom: none;
}
.grand .qty {
  color: #fff;
  font-size: 14px;
}
.grand-sub {
  font-weight: 400;
  font-size: 11px;
  opacity: 0.85;
}
</style>
