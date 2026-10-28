<script setup lang="ts">
// 补货工作台：销速驱动的采购清单（按城市分组），参数（覆盖天数 / 安全系数 / 销速窗口）可调，
// 支持导出 xlsx（单 sheet，城市作为一列，格式与页面展示一致）。
//
// 数据口径（与后端 backend.jobs.alert_rules 对齐）：
//   建议量 = 可信销速 × 覆盖天数 × (1+安全系数) − 现货 − 在途；
//   可信销速：有货天数 ≥ 可信门槛（params.cred_min，默认 6）→ 窗口出库÷有货天数；
//   0 < 有货天数 < 可信门槛 → 按 30 日参照收缩估算（velocity_kind=shrunk，
//   销速前标 ≈、有货天数琥珀色悬浮说明；点「销速算法说明」看推导）
// 只展示建议量 > 0 的行；下柜 SKU 完全排除。
// 参数变化 / 父级 shop/date/keyword 变化时重拉（销速窗口切换立即生效；
// 关键词的防抖在父级做，面板拿到的已是提交后的值）。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { fetchRestockList } from "../../api";
import {
  THUMB_SIZE,
  availCellClass,
  availTitle,
  erpText,
  gapFlagMeta,
  gapFlagTitle,
  invThumbChain,
  invThumbSrcSet,
  orEmpty,
  sellableDaysText,
  shelfText,
} from "../../modules/inventory";
import type { RestockGroup, RestockListResponse, RestockRow } from "../../types";

const props = defineProps<{
  /** 店铺过滤（空串 = 全部店铺，与库存矩阵 tab 一致） */
  shop: string;
  /**
   * 关键词过滤（空串 = 不过滤；SKU / 商品名称 / SPU，口径与矩阵表一致）
   */
  keyword: string;
}>();

/** 算法参数草稿（安全系数用百分比编辑，提交时换算回小数）。 */
const days = ref(9);
const safetyPct = ref(20);
const windowDays = ref(14);

/** 清单响应（两个视图的联合类型：按 view 判别）。 */
const data = ref<RestockListResponse | null>(null);
/** 视图：city=按城市分组 / sku=平铺长表（同一份计算的两种装配）。 */
const view = ref<"city" | "sku">("city");
const loading = ref(false);
const error = ref<string | null>(null);

/** 防抖计时句柄。 */
let timer: number | undefined;

/**
 * 拉取补货清单（参数与 shop/keyword 一起下发）。
 *
 * @remarks
 * ⛔ **不传 date**：库存按「每店保留自己最新一份」淘汰（ADR-0013），各店日期可能
 * 不同，跨店单一日期会把落后店筛掉。留空 = 后端按 per-shop 口径各取各的最新。
 */
async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchRestockList({
      shop: props.shop || undefined,
      keyword: props.keyword || undefined,
      view: view.value,
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

// shop/keyword 由父级传入：变化立即重拉（切店铺不该有 300ms 迟滞；
// 关键词的 300ms 防抖在父级做，这里拿到的已是提交后的值）
watch(
  () => [props.shop, props.keyword],
  () => void load(),
);
// 参数变化：防抖重拉
watch([days, safetyPct, windowDays], scheduleLoad);
// 视图切换：立即重拉（换的是装配方式，不是口径）
watch(view, () => void load());

onMounted(() => void load());

/**
 * 商品图降级档位：0=avif 缩略图 / 1=webp 缩略图 / 2=原图 / 3=占位。
 *
 * @remarks
 * 降级链与缩略图路径换算都在 `src/modules/inventory.ts`（与矩阵表共用），
 * 这里只负责记住"这一行退到第几档"。
 */
const thumbStage = ref<Record<string, number>>({});

/** 行的图片档位键（同一 SKU 可能出现在多个城市行里）。 */
function rowKey(r: RestockRow): string {
  return `${r.sku}|${r.shop ?? ""}`;
}

/** 当前应显示的图片地址；没有图或已退到尽头返回 null（显示占位）。 */
function thumbSrc(r: RestockRow): string | null {
  const chain = invThumbChain(r.sku_pic, THUMB_SIZE);
  if (!chain.length) return null;
  const stage = thumbStage.value[rowKey(r)] ?? 0;
  return chain[stage] ?? null;
}

/** 图片加载失败：沿降级链退一档；退到尽头后显示占位。 */
function onThumbError(r: RestockRow): void {
  const key = rowKey(r);
  const stage = thumbStage.value[key] ?? 0;
  thumbStage.value = { ...thumbStage.value, [key]: stage + 1 };
}

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

/** 销速算法说明对话框是否打开。 */
const showAlgo = ref(false);

/** 对话框用的参数与演示算例（数字全由后端真实函数算出，这里只渲染不计算）。 */
const algo = computed(() => {
  const p = data.value?.params;
  return {
    credMin: p?.cred_min ?? 0,
    burstCap: p?.burst_cap ?? 0,
    refWindow: p?.ref_window ?? 0,
    days: p?.days ?? 0,
    safety: p?.safety ?? 0,
    windowDays: p?.window ?? 0,
    ex: (p?.algo_example ?? {}) as Record<string, number>,
  };
});

/** 取算例里的一个数字（缺失显示占位符）。 */
function ex(key: string): string {
  const v = algo.value.ex[key];
  return v === undefined ? "—" : fmt(v);
}

/** Esc 关闭对话框。 */
function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape") showAlgo.value = false;
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));

/** 当前是否没有任何行（空态提示）。 */
const empty = ref(false);
watch(data, (d) => {
  empty.value = !!d && (d.view === "city" ? d.groups.length === 0 : d.rows.length === 0);
});

/** 导出中 / 导出提示。 */
const exporting = ref(false);
const exportMsg = ref<string | null>(null);

/**
 * 导出 xlsx：交给**浏览器原生下载**（后端生成工作簿）。
 *
 * @remarks
 * 导出搬到后端是因为要嵌商品图（SheetJS 不能在 xlsx 里嵌图），
 * 同时保证「导出 = 页面所见」：后端用的是与清单接口同一套装配逻辑。
 * 工作簿含两个 sheet：「按城市」（分组头 + 小计 + 总计）与「按SKU」（明细长表）。
 *
 * 为什么用 `<a href>` 而不是 fetch + blob：
 * - 文件名由后端 Content-Disposition 决定（前端未必知道最新快照日期，猜不出来）；
 * - 几 MB 的工作簿不必整个进 JS 内存，浏览器原生下载还能显示进度；
 * - 后端保证任何情况下都返回附件（失败时是错误说明 txt），所以浏览器只会下载，
 *   绝不会把当前工作台页面导航走——用户的筛选状态不会丢。
 */
function exportExcel(): void {
  if (exporting.value) return;
  exporting.value = true;
  exportMsg.value = null;
  try {
    const params = new URLSearchParams();
    if (props.shop) params.set("shop", props.shop);
    if (props.keyword) params.set("keyword", props.keyword);
    params.set("days", String(days.value));
    params.set("safety", String(safetyPct.value / 100));
    params.set("window", String(windowDays.value));
    const a = document.createElement("a");
    a.href = `/api/inventory/restock/export?${params.toString()}`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    exportMsg.value = "已发起导出：浏览器将下载「补货清单_*.xlsx」（含「按城市」与「按SKU」两个 sheet + 商品图）";
  } catch (e) {
    exportMsg.value = "导出失败：" + String(e);
  } finally {
    // 原生下载是异步的（浏览器自己下载），按钮很快恢复；留一小段防连点。
    window.setTimeout(() => (exporting.value = false), 800);
    if (exportMsg.value && exportMsg.value.startsWith("已发起导出")) {
      window.setTimeout(() => (exportMsg.value = null), 4000);
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
        <button
          class="rp-btn rp-btn-info"
          data-testid="restock-algo-btn"
          :disabled="!data"
          title="查看销速算法说明（为什么会出现 ≈）"
          @click="showAlgo = true"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/></svg>
          销速算法说明
        </button>
        <span class="rp-formula">
          建议量 = 周期内出库件数 ÷ 周期内有货天数 × 期望库存覆盖天数 × (1 + 安全系数) − 现货库存 − 在途库存；
          有货天数不足 {{ algo.credMin }} 天时销速按 {{ algo.refWindow }} 日参照周期收缩估算（销速前标 ≈）
        </span>
        <span class="rp-formula">
          举例：近14日出库件数70件，近14日有货天数7天(平均每天出库70÷7=10件)，期望库存覆盖天数7天，安全系数20%，现货库存5件， 在途库存2件，则建议补货量 = 70 ÷ 7 × 7 × (1 + 20%) − 5 − 2 = 77件
        </span>
      </div>
      <div class="rp-actions">
        <div class="rp-views" role="tablist" aria-label="清单视图">
          <button
            class="rp-view-btn"
            :class="{ active: view === 'city' }"
            data-testid="restock-view-city"
            role="tab"
            :aria-selected="view === 'city'"
            @click="view = 'city'"
          >
            城市视图
          </button>
          <button
            class="rp-view-btn"
            :class="{ active: view === 'sku' }"
            data-testid="restock-view-sku"
            role="tab"
            :aria-selected="view === 'sku'"
            @click="view = 'sku'"
          >
            SKU 视图
          </button>
        </div>
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
      <template v-if="view === 'city'">
        当前筛选与参数下没有需要补货的 SKU（现货 + 在途已覆盖目标库存）。
      </template>
      <template v-else>当前筛选与参数下没有匹配的行。</template>
    </div>

    <div v-if="data && data.view === 'city' && data.groups.length" class="rp-wrap" data-testid="restock-table">
      <table class="rp-table">
        <thead>
          <tr>
            <th>城市</th>
            <th>商品信息</th>
            <th>店铺</th>
            <th data-testid="restock-erp-head">ERP 编码</th>
            <th class="num">现货</th>
            <th class="num">在途</th>
            <th class="num">日均销速</th>
            <th class="num" data-testid="restock-avail-head">周期有货天数</th>
            <th class="num">目标库存</th>
            <th class="num">建议补货量</th>
            <th class="num">预计可售天数</th>
            <th>状态</th>
          </tr>
        </thead>
        <template v-for="g in data.groups" :key="g.code">
          <tbody>
            <tr class="group-row">
              <td :colspan="12">
                {{ g.city }}
                <span class="group-sub">阈值 {{ g.threshold }} · {{ g.rows.length }} SKU · 小计 <b>{{ g.subtotal }}</b> 件</span>
              </td>
            </tr>
            <tr v-for="r in g.rows" :key="g.code + '-' + r.sku" class="data-row">
              <td></td>
              <td class="cell-product">
                <img
                  v-if="thumbSrc(r)"
                  class="thumb"
                  :src="thumbSrc(r) ?? undefined"
                  :srcset="invThumbSrcSet(r.sku_pic)"
                  :alt="r.sku_name ?? ''"
                  data-testid="restock-thumb"
                  @error="onThumbError(r)"
                />
                <div v-else class="thumb thumb-empty" data-testid="restock-thumb-empty">无图</div>
                <div class="product">
                  <span class="name" :title="r.sku_name ?? ''">{{ orEmpty(r.sku_name) }}</span>
                  <span class="sku">{{ r.sku ?? "" }}</span>
                  <span class="ident">
                    <span v-if="r.category">{{ r.category }}</span>
                    <span v-if="r.spu">SPU {{ r.spu }}</span>
                    <span v-if="r.seller">{{ r.seller }}</span>
                    <span class="shelf">{{ shelfText(r.shelf_status) }}</span>
                  </span>
                </div>
              </td>
              <td>{{ orEmpty(r.shop) }}</td>
              <td class="erp" data-testid="restock-erp-code">{{ erpText(r.erp_code) }}</td>
              <td class="num">{{ fmt(r.stock) }}</td>
              <td class="num">{{ fmt(r.purchase) }}</td>
              <td class="num">{{ (r.velocity_kind === "shrunk" ? "≈" : "") + fmt(r.velocity) }} 件/天</td>
              <td
                class="num"
                :class="availCellClass(r.velocity_kind)"
                :title="availTitle(r.velocity_kind, r.avail, algo.credMin, algo.refWindow)"
                data-testid="restock-avail-days"
              >{{ fmt(r.avail) }}</td>
              <td class="num">{{ fmt(r.target) }}</td>
              <td class="num qty">+{{ fmt(r.qty) }}</td>
              <td class="num" data-testid="restock-sellable-days">{{ sellableDaysText(r.sellable_days) }}</td>
              <td>
                <span class="st" :class="statusMeta(r.status).cls">{{ statusMeta(r.status).text }}</span>
                <span
                  v-if="gapFlagMeta(r.gap_flag)"
                  class="st"
                  :class="gapFlagMeta(r.gap_flag)?.cls"
                  :title="gapFlagTitle(r.gap_flag, r.lost_sales, r.avail, data.params.window)"
                  data-testid="restock-gap-flag"
                >{{ gapFlagMeta(r.gap_flag)?.text }}</span>
              </td>
            </tr>
          </tbody>
        </template>
        <tfoot>
          <tr class="grand">
            <td colspan="9">8 城总计（{{ data.params.days }} 天 × {{ Math.round(data.params.safety * 100) }}% × {{ data.params.window }} 日窗口）</td>
            <td class="num qty" data-testid="restock-grand-total">+{{ fmt(data.grand_total) }}</td>
            <td colspan="2"><span class="grand-sub">本周建议采购总量</span></td>
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- SKU 视图：平铺长表（一行一个 SKU × 城市），与城市视图共用同一份计算 -->
    <div
      v-if="data && data.view === 'sku' && data.rows.length"
      class="rp-wrap"
      data-testid="restock-sku-table"
    >
      <table class="rp-table">
        <thead>
          <tr>
            <th>城市</th>
            <th>商品信息</th>
            <th>店铺</th>
            <th data-testid="restock-erp-head">ERP 编码</th>
            <th class="num">现货</th>
            <th class="num">在途</th>
            <th class="num">日均销速</th>
            <th class="num" data-testid="restock-avail-head">周期有货天数</th>
            <th class="num">目标库存</th>
            <th class="num">建议补货量</th>
            <th class="num">预计可售天数</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in data.rows"
            :key="`${r.sku}-${r.city}-${r.shop}`"
            class="data-row"
          >
            <td>{{ r.city }}</td>
            <td class="cell-product">
              <img
                v-if="thumbSrc(r)"
                class="thumb"
                :src="thumbSrc(r) ?? undefined"
                :srcset="invThumbSrcSet(r.sku_pic)"
                :alt="r.sku_name ?? ''"
                data-testid="restock-thumb"
                @error="onThumbError(r)"
              />
              <div v-else class="thumb thumb-empty" data-testid="restock-thumb-empty">无图</div>
              <div class="product">
                <span class="name" :title="r.sku_name ?? ''">{{ orEmpty(r.sku_name) }}</span>
                <span class="sku">{{ r.sku ?? "" }}</span>
                <span class="ident">
                  <span v-if="r.category">{{ r.category }}</span>
                  <span v-if="r.spu">SPU {{ r.spu }}</span>
                  <span v-if="r.seller">{{ r.seller }}</span>
                  <span class="shelf">{{ shelfText(r.shelf_status) }}</span>
                </span>
              </div>
            </td>
            <td>{{ orEmpty(r.shop) }}</td>
            <td class="erp" data-testid="restock-erp-code">{{ erpText(r.erp_code) }}</td>
            <td class="num">{{ fmt(r.stock) }}</td>
            <td class="num">{{ fmt(r.purchase) }}</td>
            <td class="num">{{ (r.velocity_kind === "shrunk" ? "≈" : "") + fmt(r.velocity) }} 件/天</td>
            <td
              class="num"
              :class="availCellClass(r.velocity_kind)"
              :title="availTitle(r.velocity_kind, r.avail, algo.credMin, algo.refWindow)"
              data-testid="restock-avail-days"
            >{{ fmt(r.avail) }}</td>
            <td class="num">{{ fmt(r.target) }}</td>
            <td class="num qty">+{{ fmt(r.qty) }}</td>
            <td class="num" data-testid="restock-sellable-days">{{ sellableDaysText(r.sellable_days) }}</td>
            <td>
              <span class="st" :class="statusMeta(r.status).cls">{{ statusMeta(r.status).text }}</span>
              <span
                v-if="gapFlagMeta(r.gap_flag)"
                class="st"
                :class="gapFlagMeta(r.gap_flag)?.cls"
                :title="gapFlagTitle(r.gap_flag, r.lost_sales, r.avail, data.params.window)"
                data-testid="restock-gap-flag"
              >{{ gapFlagMeta(r.gap_flag)?.text }}</span>
            </td>
          </tr>
        </tbody>
        <tfoot>
          <tr class="grand">
            <td colspan="9">共 {{ data.sku_count }} 个 SKU · 本周建议采购总量</td>
            <td class="num qty" data-testid="restock-grand-total">+{{ fmt(data.grand_total) }}</td>
            <td colspan="2"></td>
          </tr>
        </tfoot>
      </table>
    </div>

    <!-- 销速算法说明对话框：讲清 ≈ 是怎么算出来的（数字全由后端真实函数算出） -->
    <div
      v-if="showAlgo && data"
      class="rp-modal"
      data-testid="restock-algo-modal"
      @click.self="showAlgo = false"
    >
      <div class="rp-modal-box" role="dialog" aria-modal="true" aria-label="销速算法说明">
        <div class="rp-modal-head">
          <h3>销速算法说明（为什么会出现 ≈）</h3>
          <button
            class="rp-modal-close"
            data-testid="restock-algo-close"
            aria-label="关闭"
            @click="showAlgo = false"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </button>
        </div>
        <div class="rp-modal-body">
          <section class="rp-algo-sec">
            <h4>为什么不能直接「出库 ÷ 有货天数」</h4>
            <p>
              有货天数很少时直算会爆表：示例里 14 日窗只有 {{ ex("avail") }} 天有货、这几天出了
              {{ ex("out") }} 件，直算 {{ ex("out") }} ÷ {{ ex("avail") }} =
              <b>{{ ex("v_raw") }} 件/天</b>，目标库存被顶到 <b>{{ ex("target_raw") }} 件</b>——
              因为那唯一一天是补货到仓后的爆发日，里面混着整个缺货期积压的需求。
            </p>
            <p class="rp-algo-why">
              真实销速被夹在两个界之间：<b>{{ ex("v_floor") }}</b>（按日历摊薄，低估）
              ≤ 真实销速 ≤ <b>{{ ex("v_raw") }}</b>（全速运转，高估）。
              页面上的 <b>≈</b> 表示「收缩估算值」：按下面三步算出来的保守估计，不是直算。
            </p>
          </section>

          <section class="rp-algo-sec">
            <h4>第 1 步 · 算参照销速（分母垫底）</h4>
            <div class="rp-algo-formula">
              v_ref = {{ algo.refWindow }} 日出库 ÷ max({{ algo.refWindow }} 日有货天数, {{ algo.refWindow / 2 }})
            </div>
            <p>
              示例：{{ ex("out_ref") }} ÷ max({{ ex("avail_ref") }}, {{ algo.refWindow / 2 }})
              = {{ ex("out_ref") }} ÷ {{ algo.refWindow / 2 }} ≈ <b>{{ ex("v_ref") }} 件/天</b>
            </p>
            <p class="rp-algo-why">
              为什么：参照窗自己也只断断续续有货，直算 {{ ex("out_ref") }} ÷ {{ ex("avail_ref") }}
              会踩一模一样的坑（那几天同样有爆发成分）。所以无论实际有货几天，最多按半个参照窗摊——
              得到一个「偏保守、但没被爆发污染」的基准。
            </p>
          </section>

          <section class="rp-algo-sec">
            <h4>第 2 步 · 给短窗销速封顶（突发封顶）</h4>
            <div class="rp-algo-formula">
              v_short' = min(短窗销速, v_ref × {{ algo.burstCap }})
            </div>
            <p>
              示例：min({{ ex("v_raw") }}, {{ ex("v_ref") }} × {{ algo.burstCap }} = {{ ex("v_capped") }})
              = <b>{{ ex("v_capped") }}</b>
            </p>
            <p class="rp-algo-why">
              为什么：补货日可能真热卖（真实需求 + 积压释放叠加），但再猛也不能当常态——
              最多允许放大到参照销速的 {{ algo.burstCap }} 倍。封顶砍掉的是"积压假象"，
              留下的是"确实有点热销"的信号。
            </p>
          </section>

          <section class="rp-algo-sec">
            <h4>第 3 步 · 按可信度加权（credibility 思想）</h4>
            <div class="rp-algo-formula">
              w = 有货天数 ÷ {{ algo.credMin }}　→　v = w × v_short' + (1 − w) × v_ref
            </div>
            <p>
              示例：w = {{ ex("avail") }} ÷ {{ algo.credMin }} ≈
              <b>{{ fmt((algo.ex["w"] ?? 0) * 100) }}%</b>　→　v ≈ <b>{{ ex("v") }} 件/天</b>
            </p>
            <p class="rp-algo-why">
              为什么：这是保险精算里的可信度思想——"新证据只占一成分量，其余信历史基准"。
              可信门槛 {{ algo.credMin }} 天：样本越少越信基准（有货 1 天只信
              {{ fmt(100 / algo.credMin) }}%，有货 {{ algo.credMin }} 天就全信短窗、不再掺基准）。
            </p>
          </section>

          <section class="rp-algo-sec">
            <h4>代入补货公式</h4>
            <p>
              v × 覆盖天数 × (1 + 安全系数) = {{ ex("v") }} × {{ algo.days }} 天 ×
              (1 + {{ Math.round(algo.safety * 100) }}%) ≈ <b>{{ ex("target") }} 件</b>
              <span class="rp-algo-why">（同一份数据直算会给 {{ ex("target_raw") }} 件，差近十倍）</span>
            </p>
          </section>

          <section class="rp-algo-sec">
            <h4>什么时候用哪一档</h4>
            <table class="rp-algo-table">
              <thead>
                <tr><th>窗口内有货天数</th><th>销速怎么算</th><th>页面标记</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>0 天</td>
                  <td>0（无销速依据，宁可不建议，不凭空估）</td>
                  <td>无 ≈；不列入补货清单</td>
                </tr>
                <tr>
                  <td>1 ~ {{ algo.credMin - 1 }} 天</td>
                  <td>收缩估算（上面三步）</td>
                  <td>销速前标 ≈，有货天数标琥珀色</td>
                </tr>
                <tr>
                  <td>≥ {{ algo.credMin }} 天</td>
                  <td>直算：窗口出库 ÷ 有货天数</td>
                  <td>无 ≈</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="rp-algo-sec rp-algo-params">
            当前参数：可信门槛 {{ algo.credMin }} 天 · 突发封顶 {{ algo.burstCap }} 倍 ·
            参照窗 {{ algo.refWindow }} 日 · 销速窗口 {{ algo.windowDays }} 日
          </section>
        </div>
      </div>
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
/* 参数条可读性（2026-10-24 用户反馈）：整条参数区字号上调、颜色整体加深，
   三个输入控件（覆盖天数 / 安全系数 / 周期选择）加大、框线加深。 */
.rp-params {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text-3);
}
.rp-param {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  /* 标签文字比正文再深一档，避免 13px 小字发虚 */
  color: var(--color-text-2);
  font-weight: 500;
}
.rp-param input,
.rp-param select {
  height: 34px;
  padding: 0 12px;
  /* 框线加深：库存视图把 --color-border-2 覆盖成近白的 #eef2f7，原 1px 几乎看不见边界；
     这里改用 --color-border-3(#cbd5e1) 并加粗到 2px（1.5px 会被浏览器按整像素取整回 1px），
     输入框才有实体感。 */
  border: 2px solid var(--color-border-3);
  border-radius: var(--radius-md, 8px);
  /* 输入框里的数据字号与容器字号同步放大（16px），避免框大字小 */
  font-size: calc(16px * var(--font-scale));
  color: var(--color-text);
  background: var(--color-surface);
  font-variant-numeric: tabular-nums;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.rp-param input {
  width: 84px;
  text-align: center;
}
.rp-param input:focus,
.rp-param select:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.rp-formula {
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-3);
  background: var(--color-surface-2);
  border: 1px dashed var(--color-border-3);
  padding: 6px 12px;
  border-radius: var(--radius-md, 8px);
}
.rp-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.rp-export-msg {
  font-size: calc(12px * var(--font-scale));
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
  font-size: calc(12.5px * var(--font-scale));
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
  font-size: calc(13px * var(--font-scale));
  margin-bottom: 12px;
}
.rp-loading,
.rp-empty {
  padding: 26px 0;
  text-align: center;
  font-size: calc(13px * var(--font-scale));
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
  font-size: calc(12.5px * var(--font-scale));
}
/* 表头字号 12px → 14px：与库存矩阵表表头（InventoryMatrixTable 的 .th，14px）
   对齐，两个视图的列名层级一致（2026-10 补货工作台模式视觉统一）。 */
.rp-table th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--color-brand-tint, #eff6ff);
  color: var(--color-brand-dark, #1d4ed8);
  font-size: calc(14px * var(--font-scale));
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
  font-size: calc(11.5px * var(--font-scale));
  color: var(--color-text-3);
}
/* 视图切换：城市视图 / SKU 视图（同一份计算的两种装配） */
.rp-views {
  display: inline-flex;
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-surface);
}
.rp-view-btn {
  border: 0;
  background: transparent;
  padding: 6px 12px;
  font-size: calc(12.5px * var(--font-scale));
  color: var(--color-text-2);
  cursor: pointer;
}
.rp-view-btn + .rp-view-btn {
  border-left: 1px solid var(--color-border, #e5e7eb);
}
.rp-view-btn.active {
  background: var(--color-brand, #2563eb);
  color: #fff;
  font-weight: 600;
}
/* 商品信息单元格：缩略图 + 名称/SKU + 身份信息（类目 / SPU / 销售员 / 上下柜） */
.cell-product {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 260px;
}
.thumb {
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  border-radius: 6px;
  object-fit: cover;
  background: var(--color-surface-2, #f3f4f6);
  border: 1px solid var(--color-border, #e5e7eb);
}
.thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: calc(11px * var(--font-scale));
  color: var(--color-text-3);
}
.product {
  min-width: 0;
}
.ident {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-top: 2px;
  font-size: calc(11px * var(--font-scale));
  color: var(--color-text-3);
}
.ident .shelf {
  color: var(--color-text-2);
}
.erp {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: calc(11.5px * var(--font-scale));
  color: var(--color-text-2);
  white-space: nowrap;
}
.qty {
  font-weight: 700;
  color: var(--color-brand-dark, #1d4ed8);
  font-size: calc(13.5px * var(--font-scale));
}
.group-row td {
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-size: calc(12px * var(--font-scale));
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
  font-size: calc(11px * var(--font-scale));
  font-weight: 600;
}
.st-alert {
  color: #dc2626;
  background: #fef2f2;
  border: 1px solid #fecaca;
}
.st-incoming {
  /* 琥珀字用 -700 档（#b45309，与矩阵表在途档同色）：-600 档 #d97706 在浅底上
     只有 3.2:1，11px 徽章字读不清；-700 档 5:1（2026-10-24 可读性统一）。 */
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
}
.st-healthy {
  /* 绿字同样提到 -700 档 #15803d（原 #16a34a 3.3:1 → 5:1）。 */
  color: #15803d;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}
/* 缺口关注徽章（紫）：与库存健康三档（绿/琥珀/红）区分开——这里说的是
   "需求缺口"（缺货损失），不是"库存水位"；文字用 violet-700 #6d28d9
   保证 11px 小字在浅底上读得清。 */
.st-gap {
  color: #6d28d9;
  background: #f5f3ff;
  border: 1px solid #ddd6fe;
  margin-left: 4px;
}
/* 周期有货天数：收缩估算行琥珀色提示（与在途徽章同档 #b45309——12.5px 小字
   在浅底上要 -700 档才读得清，见 ai-rules §3 最浅文字可读性规则） */
.avail-low {
  color: #b45309;
  font-weight: 600;
}

/* 「销速算法说明」按钮：参数条里的次要动作 */
.rp-btn-info {
  height: 34px;
  align-self: center;
}

/* 销速算法说明对话框 */
.rp-modal {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.rp-modal-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-lg, 8px);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.28);
  width: 100%;
  max-width: 780px;
  max-height: 84vh;
  display: flex;
  flex-direction: column;
}
.rp-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 18px;
  border-bottom: 1px solid var(--color-divider);
}
.rp-modal-head h3 {
  margin: 0;
  font-size: calc(15px * var(--font-scale));
  color: var(--color-text-strong);
}
.rp-modal-close {
  border: 0;
  background: transparent;
  padding: 4px;
  cursor: pointer;
  color: var(--color-text-3);
  border-radius: 6px;
  line-height: 0;
}
.rp-modal-close:hover {
  color: var(--color-text-strong);
  background: var(--color-surface-2);
}
.rp-modal-close svg {
  width: 17px;
  height: 17px;
}
.rp-modal-body {
  padding: 14px 18px 18px;
  overflow: auto;
  font-size: calc(13px * var(--font-scale));
  line-height: 1.75;
  color: var(--color-text-2);
}
.rp-algo-sec {
  margin-bottom: 15px;
}
.rp-algo-sec h4 {
  margin: 0 0 5px;
  font-size: calc(13.5px * var(--font-scale));
  color: var(--color-text-strong);
}
.rp-algo-sec p {
  margin: 4px 0;
}
.rp-algo-formula {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: calc(12.5px * var(--font-scale));
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-radius: 8px;
  padding: 7px 10px;
  margin: 6px 0;
  color: var(--color-text-2);
}
.rp-algo-why {
  color: var(--color-text-3);
  font-size: calc(12.5px * var(--font-scale));
}
.rp-algo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: calc(12.5px * var(--font-scale));
  margin-top: 4px;
}
.rp-algo-table th,
.rp-algo-table td {
  border-bottom: 1px solid var(--color-divider);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
.rp-algo-table th {
  color: var(--color-text-3);
  font-weight: 600;
}
.rp-algo-params {
  margin-bottom: 0;
  padding: 9px 12px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-2);
  border-radius: 8px;
  color: var(--color-text-3);
  font-size: calc(12.5px * var(--font-scale));
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
  font-size: calc(14px * var(--font-scale));
}
.grand-sub {
  font-weight: 400;
  font-size: calc(11px * var(--font-scale));
  opacity: 0.85;
}
</style>
