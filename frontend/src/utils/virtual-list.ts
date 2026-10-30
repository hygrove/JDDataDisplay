// 虚拟滚动的纯函数：行高取样策略 + 表格模式的占位高度计算。
//
// 为什么用 @tanstack/vue-virtual（项目已在 GenericTable.vue 用它）：
//   「滚动位置 → 可见行区间」这摊数学交给库。手写一遍容易漏边界——
//   视口高度未就绪（首帧只渲染一行）、滚过头（tbody 空掉表格塌陷）、
//   行高变化（字号缩放）……每个都是「用户看到错行数据」级的坑。
//   库负责区间与测量，这里只负责两件库不管的事：
//   1) 行高策略：实测 + 取众数（见 pickRowHeight）；
//   2) 表格模式的占位高度：把库的虚拟项区间翻译成两个占位 <tr> 的高度
//      （见 tableSpacers）。
//
// 为什么用「占位行」而不是库的 div 网格模式：
//   补货清单是 <table>，要保住表头吸顶（position:sticky）、表尾合计行吸底、
//   以及原生表格的列宽布局。div 网格（绝对定位 + translateY）会把行从表格流里
//   拿出来，列宽就失去依据了；占位行方案让表格结构原封不动。
import { computed, type ComputedRef, type Ref } from "vue";
import { useVirtualizer } from "@tanstack/vue-virtual";

/**
 * 从一段行高样本里挑出「代表性行高」（众数）。
 *
 * @remarks
 * 为什么取众数而不是最大值或第一个：
 * - 取最大值：偶发一条换行变高的行会把整表行高撑大，滚动条长度失真；
 * - 取第一个：窗口里第一条恰好是特例就整体偏了；
 * - 取众数：样本几十行，多数一致 → 稳。个别更高的行只会让那一处略有空隙，
 *   不会累积成整表漂移。
 *
 * 行高会随**字号缩放**变化（`--font-scale` 是 CSS 变量，文字变大行就变高），
 * 所以行高不能写死常量，必须运行时实测——这就是本函数存在的原因。
 *
 * @param {number[]} samples - 行高样本（px）。
 * @returns {number} 代表性行高；样本为空或全非法时返回 0。
 * @example
 * ```ts
 * pickRowHeight([69, 69, 70, 69]); // 69
 * ```
 */
export function pickRowHeight(samples: number[]): number {
  const counts = new Map<number, number>();
  for (const raw of samples) {
    if (!Number.isFinite(raw) || raw <= 0) continue;
    const h = Math.round(raw * 10) / 10;
    counts.set(h, (counts.get(h) ?? 0) + 1);
  }
  let best = 0;
  let bestCount = 0;
  for (const [h, n] of counts) {
    // 同样票数时取更小的行高（宁可略密也不留缝）。
    if (n > bestCount) {
      best = h;
      bestCount = n;
    }
  }
  return best;
}

/**
 * 虚拟项区间 → 表格模式的上下占位高度。
 *
 * @remarks
 * 表格里没有「绝对定位行」这回事，要让窗口落在正确的滚动位置，
 * 只能在 tbody 顶部/底部各插一个「高度撑开、内容为空」的占位行：
 * - 上占位 = 第一个虚拟项的起始偏移（它前面的行都被这段高度顶掉了）；
 * - 下占位 = 总高度 − 最后一个虚拟项的结束偏移（它后面的行同理）。
 *
 * @param {Array<{ start: number; end: number }>} items - 库的虚拟项（按顺序）。
 * @param {number} totalSize - 列表总高度（库给的 `getTotalSize()`）。
 * @returns {{ top: number; bottom: number }} 上下占位高度（px）。
 * @example
 * ```ts
 * tableSpacers([{ start: 700, end: 769 }], 107778); // { top: 700, bottom: 107009 }
 * ```
 */
export function tableSpacers(
  items: ReadonlyArray<{ start: number; end: number }>,
  totalSize: number,
): { top: number; bottom: number } {
  if (!items.length) return { top: 0, bottom: 0 };
  const first = items[0];
  const last = items[items.length - 1];
  return {
    top: first.start,
    bottom: Math.max(0, totalSize - last.end),
  };
}

/**
 * 表格专用的虚拟滚动封装：在库之上补齐「行高实测 → 估计行高」的闭环。
 *
 * @remarks
 * 库用 `estimateSize` 估算行高（算总高度与可见区间）。本项目的行高随字号缩放
 * 变化，必须实测后回填——所以这里把行高做成响应式 ref：
 * 实测值变了 → 库的 options 变 → 调 `measure()` 让它丢弃旧测量重新估算。
 *
 * @param {object} options - 选项。
 * @param {Ref<number> | ComputedRef<number>} options.count - 总行数。
 * @param {Ref<HTMLElement | null>} options.scrollEl - 滚动容器（表格外层）。
 * @param {Ref<number>} options.rowHeight - 实测行高（px，响应式）。
 * @param {number} [options.overscan=8] - 视口外额外渲染的行数。
 * @returns {object} 库的虚拟器 + 派生出的「当前窗口行」与「上下占位高度」。
 * @example
 * ```ts
 * const { virtualizer, visibleRows, spacers } = useTableVirtualizer({
 *   count: computed(() => rows.value.length),
 *   scrollEl: wrapRef,
 *   rowHeight: rowHeightRef,
 * });
 * ```
 */
export function useTableVirtualizer(options: {
  count: Ref<number> | ComputedRef<number>;
  scrollEl: Ref<HTMLElement | null>;
  rowHeight: Ref<number>;
  overscan?: number;
}) {
  const virtualizer = useVirtualizer(
    computed(() => ({
      count: options.count.value,
      getScrollElement: () => options.scrollEl.value,
      estimateSize: () => options.rowHeight.value,
      overscan: options.overscan ?? 8,
    })),
  );

  /** 当前窗口内的行（库算区间，这里只负责把下标换成数据行）。 */
  const visibleRows = computed<number[]>(() =>
    virtualizer.value.getVirtualItems().map((v) => v.index),
  );

  /** 上下占位高度（供模板插占位行）。 */
  const spacers = computed(() =>
    tableSpacers(virtualizer.value.getVirtualItems(), virtualizer.value.getTotalSize()),
  );

  return { virtualizer, visibleRows, spacers };
}
