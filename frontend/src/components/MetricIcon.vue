<script setup lang="ts">
// 指标线性图标（lucide 风格，24x24，currentColor 描边）。
// 图标名与 metrics.ts 里每个指标的 icon 字段一一对应。
//
// 为什么手写 SVG 而不是引入图标库：
//   本项目只用到十来个图标，引入整个 lucide/iconify 会显著增加打包体积；
//   手写内联 path 零依赖，且描边用 currentColor，能自动跟随父级文字颜色（hover 变红等）。
import { computed } from "vue";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 图标名，需与 metrics.ts 中指标的 icon 字段一致（如 "yen"、"cart"） */
    name: string;
    /** 图标边长（px）；默认 16 */
    size?: number;
  }>(),
  { size: 16 },
);

/**
 * ICONS：图标名 -> SVG 内部标记（path / circle / polyline…）。
 *
 * @remarks
 * 只存**内部**标记，外层 <svg> 统一提供 viewBox 与描边属性，
 * 这样每个图标只需关心形状，颜色/线宽由外层统一控制（currentColor）。
 * 新增图标时：在这里加一项，并在 metrics.ts 对应指标的 icon 字段引用它。
 */
const ICONS: Record<string, string> = {
  // 成交金额
  yen: '<path d="M7 5h10"/><path d="M10 5l2 5 2-5"/><path d="M12 10v8"/><path d="M8.5 13h7"/>',
  // 成交单量
  cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 2-1.58l1.65-7.42H5.12"/>',
  // 成交客户数
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  // 成交商品件数
  box: '<path d="M16.5 9.4 7.5 4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="m3.27 6.96 8.73 5.05 8.73-5.05"/><path d="M12 22.08V12"/>',
  // 成交转化率
  funnel: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
  // 客单价
  tag: '<path d="M12.59 2.59A2 2 0 0 0 11.17 2H4a2 2 0 0 0-2 2v7.17a2 2 0 0 0 .59 1.42l8.7 8.7a2.43 2.43 0 0 0 3.42 0l6.58-6.58a2.43 2.43 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
  // 搜索曝光次数
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  // 搜索点击率
  mouse: '<path d="M4 4l7 17 2.5-7.5L21 11z"/>',
  // 商品访客数
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  // 推广花费
  mega: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/>',
  // 推广成交金额
  wallet: '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>',
  // ROI
  trend: '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
  // 推广占比
  pie: '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
  // 退款（金额 / 单量 共用）
  refund: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
};

/**
 * 当前图标的内部 SVG 标记。
 *
 * @remarks
 * 未知图标名时回退成一个空心圆，而不是渲染空 SVG——
 * 空 SVG 会让布局塌成 0 宽（图标位消失），空心圆至少占位稳定。
 */
const inner = computed(() => ICONS[props.name] ?? '<circle cx="12" cy="12" r="9"/>');
</script>

<template>
  <!--
    用 v-html 注入 inner：图标内容是可信的常量字符串（非用户输入），
    不存在 XSS 风险；且这样能避免为每个图标单写一个组件。
  -->
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    v-html="inner"
  ></svg>
</template>
