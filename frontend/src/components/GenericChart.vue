<script setup lang="ts">
// 通用图表组件：vue-echarts 按需引入，由父组件传入 option 配置驱动。
//
// 为什么不用 echarts 自带的 autoresize？
// autoresize 基于 ResizeObserver 监听「容器尺寸变化」。本项目的图表宽度是 100%、
// 放在 CSS grid 里，重绘时容器宽度会在亚像素级别来回抖（如 400.5px ↔ 400px），
// 于是「尺寸变→重绘→尺寸又变→再重绘」形成 ResizeObserver 死循环，页面持续重绘。
// 改法：只在 window 尺寸变化时（防抖）手动 resize 一次，彻底消除反馈环。
//
// 为什么用 SVG 渲染器而不是 Canvas？
// Canvas 走 GPU 硬件加速合成。在部分显卡驱动 + Edge 组合下，窗口内有持续被 GPU
// 合成的 canvas 时，Windows 会一直把该窗口当成前台活跃窗口，表现为「点最小化后
// 立刻被拉回前台」（已知 Edge/Chrome GPU bug）。SVG 渲染不走 GPU canvas，无此坑，
// 且仪表盘线条更清晰、产物更小。
import { onBeforeUnmount, onMounted, ref } from "vue";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { LineChart, BarChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkLineComponent,
} from "echarts/components";
import VChart from "vue-echarts";
import type { EChartsOption } from "echarts";

/**
 * 按需注册 echarts 组件。
 *
 * @remarks
 * 只注册实际用到的图表类型与组件，避免把整个 echarts 打进包里（体积差好几倍）。
 * 之后若要用新图表类型（如雷达图），必须在这里补注册，否则运行时报「未注册」。
 */
use([
  SVGRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  MarkAreaComponent,
  MarkLineComponent,
]);

/**
 * 组件 Props。
 */
defineProps<{
  /** ECharts 配置项（由父组件按指标清单动态生成） */
  option: EChartsOption;
  /** 图表高度 CSS 值；默认 320px */
  height?: string;
}>();

/** 图表实例引用，用于手动触发 resize */
const chartRef = ref<InstanceType<typeof VChart> | null>(null);
/** resize 防抖定时器句柄 */
let timer: ReturnType<typeof setTimeout> | undefined;

/**
 * 窗口尺寸变化时的防抖重绘。
 *
 * @remarks
 * 200ms 防抖：拖拽窗口时 resize 事件会高频触发，
 * 每次都重绘 echarts 会非常卡；等停下来再画一次即可。
 *
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * // 由 window resize 事件触发，通常不手动调用：
 * window.addEventListener("resize", onResize);
 * ```
 */
function onResize() {
  // 先清掉上一次未执行的定时器，保证只有最后一次尺寸变化会真正触发重绘
  if (timer !== undefined) clearTimeout(timer);
  timer = setTimeout(() => chartRef.value?.resize(), 200);
}

// 监听 window resize（而不是容器 ResizeObserver）：见文件头「为什么不用 autoresize」
onMounted(() => window.addEventListener("resize", onResize));
onBeforeUnmount(() => {
  // 必须移除监听 + 清理定时器，否则组件销毁后回调仍持有 chartRef，造成内存泄漏
  window.removeEventListener("resize", onResize);
  if (timer !== undefined) clearTimeout(timer);
});
</script>

<template>
  <VChart ref="chartRef" class="chart" :option="option" :style="{ height: height ?? '320px' }" />
</template>

<style scoped>
.chart {
  /* 宽度交给父容器（CSS grid）决定，echarts 会根据实际像素渲染 */
  width: 100%;
}
</style>
