<script setup lang="ts">
// 应用数字输入框：Element Plus 的 el-input-number 收敛版。
//
// 存在的理由：
//   1. 手写 <input type="number"> 常常漏掉边界约束——min/max 形同虚设，
//      用户能输入越界值，直到提交才报错。el-input-number 自带 min/max/step
//      与步进按钮，且在越界时自动夹紧。
//   2. 宽度与字号在各页面不一致；这里统一。
//   3. 字号缩放：EP 输入框字号写死，需穿透覆盖。
import { computed } from "vue";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 当前值（v-model 绑定） */
    modelValue: number | undefined;
    /** 最小值 */
    min?: number;
    /** 最大值 */
    max?: number;
    /** 步进间隔 */
    step?: number;
    /** 控件宽度（px），默认与原手写控件一致 */
    width?: number;
    /** 是否禁用 */
    disabled?: boolean;
    /** 无数据时的占位符 */
    placeholder?: string;
  }>(),
  {
    min: undefined,
    max: undefined,
    step: 1,
    width: 68,
    disabled: false,
    placeholder: "--",
  }
);

/**
 * 组件事件：v-model 双向绑定。
 */
const emit = defineEmits<{
  (e: "update:modelValue", v: number | undefined): void;
}>();

/**
 * 内部值代理：el-input-number 期望 number | undefined，
 * 与 v-model 的约定一致，此处直接转发。
 */
const inner = computed<number | undefined>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});
</script>

<template>
  <el-input-number
    v-model="inner"
    :min="min"
    :max="max"
    :step="step"
    :disabled="disabled"
    :placeholder="placeholder"
    :controls="false"
    class="app-num"
    :style="{ width: width + 'px' }"
  />
</template>

<style scoped>
/* 穿透覆盖：EP 输入框内部字号写死，需跟随全局缩放系数 */
.app-num :deep(.el-input__inner) {
  font-size: calc(13px * var(--font-scale));
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* 去掉 EP 默认的 上下箭头 造成的行高膨胀（controls 已关，这里再兜底） */
.app-num :deep(.el-input__wrapper) {
  padding: 0;
}
</style>
