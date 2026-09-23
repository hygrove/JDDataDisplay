<script setup lang="ts">
// 数字滚动计数：值变化时（含首次挂载从 0 滚到目标）做 easeOutCubic 缓动。
// 用于指标卡 / 指标行，让数据「载入即有动效」。null 显示 --。
import { onMounted, ref, watch } from "vue";
import { fmtBy } from "../modules";
import type { MetricFormat } from "../metrics";

const props = withDefaults(
  defineProps<{
    value: number | null;
    format: MetricFormat;
    duration?: number;
  }>(),
  { duration: 700 },
);

const display = ref(0);
const text = ref("--");
let raf = 0;

function render(v: number) {
  text.value = fmtBy(props.format, v);
}

function animate(to: number | null) {
  cancelAnimationFrame(raf);
  if (to == null || Number.isNaN(to)) {
    text.value = "--";
    display.value = 0;
    return;
  }
  const from = display.value;
  const dur = props.duration ?? 700;
  const t0 = performance.now();
  const step = (now: number) => {
    const p = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    const cur = from + (to - from) * e;
    display.value = cur;
    render(cur);
    if (p < 1) raf = requestAnimationFrame(step);
    else {
      display.value = to;
      render(to);
    }
  };
  raf = requestAnimationFrame(step);
}

onMounted(() => {
  if (props.value == null) {
    text.value = "--";
    return;
  }
  animate(props.value);
});

watch(
  () => props.value,
  (v) => animate(v),
);
</script>

<template>
  <span class="cu">{{ text }}</span>
</template>

<style scoped>
.cu {
  font-variant-numeric: tabular-nums;
}
</style>
