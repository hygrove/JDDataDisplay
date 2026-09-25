<script setup lang="ts">
// 数字滚动计数：值变化时（含首次挂载从 0 滚到目标）做 easeOutCubic 缓动。
// 用于指标卡 / 指标行，让数据「载入即有动效」。null 显示 --。
import { onMounted, ref, watch } from "vue";
import { fmtBy } from "../modules";
import type { MetricFormat } from "../metrics";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 目标数值；null 表示缺失，直接显示 "--" 且不播动画 */
    value: number | null;
    /** 展示格式，决定滚动过程中每一帧如何格式化 */
    format: MetricFormat;
    /** 动画时长（ms）；默认 700 */
    duration?: number;
  }>(),
  { duration: 700 },
);

/** 当前动画进度上的数值（作为下一段动画的起点） */
const display = ref(0);
/** 实际渲染到页面上的文本 */
const text = ref("--");
/** requestAnimationFrame 句柄，用于取消上一段未完成的动画 */
let raf = 0;

/**
 * 按当前值刷新显示文本。
 *
 * @param {number} v - 当前要显示的数值。
 * @returns {void} 无返回值，副作用是更新 text。
 * @example
 * ```ts
 * render(1234.5); // text 变成按 format 格式化后的字符串
 * ```
 */
function render(v: number) {
  text.value = fmtBy(props.format, v);
}

/**
 * 从当前 display 值缓动到目标值（easeOutCubic）。
 *
 * @remarks
 * 每次开始新动画前先 cancelAnimationFrame：
 * 快速切换 SPU / 日期时 value 会连续变化，若不取消上一段动画，
 * 两段 rAF 会同时写 display，导致数字来回跳。
 *
 * @param {number | null} to - 目标值；null / NaN 时直接显示 "--" 并重置 display。
 * @returns {void} 无返回值。
 * @throws 无。
 * @example
 * ```ts
 * animate(100);  // 从当前值滚到 100
 * animate(null); // 显示 "--"
 * ```
 */
function animate(to: number | null) {
  cancelAnimationFrame(raf);
  // 缺失值不播动画：显示为 --，并把基准归零，下次有值时从 0 开始滚
  if (to == null || Number.isNaN(to)) {
    text.value = "--";
    display.value = 0;
    return;
  }
  const from = display.value;
  const dur = props.duration ?? 700;
  const t0 = performance.now();
  const step = (now: number) => {
    // p 是线性进度 0→1；e 是 easeOutCubic 缓动后的进度（先快后慢）
    const p = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    const cur = from + (to - from) * e;
    display.value = cur;
    render(cur);
    if (p < 1) raf = requestAnimationFrame(step);
    else {
      // 收尾时强制落到精确目标值，避免浮点误差让最终值差一点点（如 99.999）
      display.value = to;
      render(to);
    }
  };
  raf = requestAnimationFrame(step);
}

// 首次挂载：从 0 滚到目标值
onMounted(() => {
  if (props.value == null) {
    text.value = "--";
    return;
  }
  animate(props.value);
});

// 后续值变化（切 SPU / 换日期 / 换指标）：从当前值接着滚到新值
watch(
  () => props.value,
  (v) => animate(v),
);
</script>

<template>
  <!-- tabular-nums：等宽数字，滚动时数字不会左右抖动 -->
  <span class="cu">{{ text }}</span>
</template>

<style scoped>
.cu {
  font-variant-numeric: tabular-nums;
}
</style>
