<script setup lang="ts">
// 应用按钮：Element Plus 的 el-button 收敛版。
//
// 存在的理由：业务组件里若直接写 <el-button size="small" type="primary">，
// 按钮规格就散落到几十个文件里；改一次按钮外观要改几十处。
// 包装一次、处处继承 —— 规格的唯一事实来源是这个组件。
//
// 为什么不用 EP 的 size 属性做全局默认：
//   EP 的 size 只影响内置尺寸，不影响字号缩放（--font-scale）。
//   本项目的字号缩放是全局铁律（手册 §1.1），按钮文字必须一起缩放。
import { computed } from "vue";

/**
 * 组件 Props：在 el-button 原有 props 基础上收窄为本项目实际用到的几种。
 */
const props = withDefaults(
  defineProps<{
    /** 按钮语义类型：primary=品牌红主操作，default=次要，info=信息提示 */
    type?: "primary" | "default" | "info" | "success" | "warning" | "danger" | "text";
    /** 尺寸：small=表格内联，default=工具栏 */
    size?: "small" | "default" | "large";
    /** 是否禁用 */
    disabled?: boolean;
    /** 原生按钮 type，表单内默认为 button（避免误提交） */
    nativeType?: "button" | "submit" | "reset";
    /** 是否为纯图标按钮（无文字） */
    iconOnly?: boolean;
  }>(),
  {
    type: "default",
    size: "default",
    disabled: false,
    nativeType: "button",
    iconOnly: false,
  }
);

/**
 * 传给 el-button 的 size：iconOnly 时强制 small，
 * 避免纯图标按钮因 default 尺寸撑大行高。
 */
const epSize = computed(() => (props.iconOnly ? "small" : props.size));
</script>

<template>
  <el-button
    :type="type"
    :size="epSize"
    :disabled="disabled"
    :native-type="nativeType"
    class="app-btn"
    :class="{ 'app-btn--icon': iconOnly }"
  >
    <slot />
  </el-button>
</template>

<style scoped>
/* 第 2 层 · 组件级覆盖：EP 的按钮高度写死（32/24px）、小尺寸字号写死 12px，
   字号缩放后文字会被裁切，故高度随内容增长、字号跟随全局系数。
   选择器带 .el-button 提高优先级，压过 EP 的 .el-button--small 默认值。 */
.app-btn.el-button {
  height: auto;
  min-height: calc(32px * var(--font-scale));
  padding-block: calc(6px * var(--font-scale));
  font-size: calc(13px * var(--font-scale));
  line-height: 1.2;
}

/* 纯图标按钮：去掉左右内边距，视觉上与图标对齐 */
.app-btn--icon.el-button {
  padding-inline: 0;
}

/* 表格内联的小按钮：更紧凑的内边距，适配密集表格的行高 */
.app-btn.el-button--small {
  min-height: calc(24px * var(--font-scale));
  padding-block: calc(4px * var(--font-scale));
  font-size: calc(12px * var(--font-scale));
}
</style>
