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

defineProps<{ option: EChartsOption; height?: string }>();

const chartRef = ref<InstanceType<typeof VChart> | null>(null);
let timer: ReturnType<typeof setTimeout> | undefined;

function onResize() {
  if (timer !== undefined) clearTimeout(timer);
  timer = setTimeout(() => chartRef.value?.resize(), 200);
}

onMounted(() => window.addEventListener("resize", onResize));
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (timer !== undefined) clearTimeout(timer);
});
</script>

<template>
  <VChart ref="chartRef" class="chart" :option="option" :style="{ height: height ?? '320px' }" />
</template>

<style scoped>
.chart {
  width: 100%;
}
</style>
