<script setup lang="ts">
// 应用下拉选择：Element Plus 的 el-select 收敛版。
//
// 存在的理由：手写 <select> 的原生下拉在各浏览器/系统上样式差异大，
// 且无法跟随字号缩放；el-select 统一渲染且可被令牌驱动。
import { computed } from "vue";

/**
 * 单个选项。
 */
export interface AppSelectOption {
  /** 选项值 */
  value: number | string;
  /** 显示文本 */
  label: string;
}

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 当前选中值（v-model 绑定） */
    modelValue: number | string | undefined;
    /** 选项列表 */
    options: AppSelectOption[];
    /** 控件宽度（px） */
    width?: number;
    /** 是否禁用 */
    disabled?: boolean;
  }>(),
  {
    width: 96,
    disabled: false,
  }
);

/**
 * 组件事件：v-model 双向绑定。
 */
const emit = defineEmits<{
  (e: "update:modelValue", v: number | string | undefined): void;
}>();

/**
 * 内部值代理。
 */
const inner = computed<number | string | undefined>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});
</script>

<template>
  <el-select
    v-model="inner"
    :disabled="disabled"
    class="app-select"
    :style="{ width: width + 'px' }"
  >
    <el-option
      v-for="opt in options"
      :key="opt.value"
      :label="opt.label"
      :value="opt.value"
    />
  </el-select>
</template>

<style scoped>
/* 穿透覆盖：EP 的选择器正文写死 14px，需跟随全局缩放系数。
   下挂面板被 teleport 到 body，scoped 选择器够不到；好在 EP 面板项用的是
   var(--el-font-size-base)，已由 style.css 的映射层接上缩放，无需额外覆盖。 */
.app-select :deep(.el-select__wrapper) {
  font-size: calc(13px * var(--font-scale));
  min-height: calc(28px * var(--font-scale));
}
</style>
