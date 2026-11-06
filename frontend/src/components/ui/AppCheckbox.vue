<script setup lang="ts">
// 应用复选框：Element Plus 的 el-checkbox 收敛版。
//
// 存在的理由：指标配置面板等处需要复选框组。手写 <input type="checkbox">
// 要自己实现勾选态样式、焦点环、禁用态、键盘可达性——
// 这些是易漏的无障碍细节，交给组件库更稳。
// 包装后统一尺寸与字号缩放，业务侧不必每次写 size。
import { computed } from "vue";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 勾选状态（受控用法） */
    modelValue?: boolean;
    /** 是否禁用 */
    disabled?: boolean;
    /** 尺寸 */
    size?: "small" | "default" | "large";
  }>(),
  {
    modelValue: false,
    disabled: false,
    size: "default",
  }
);

/**
 * 组件事件：勾选状态变化。
 */
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "change", v: boolean): void;
}>();

/**
 * 内部值代理：受控模式，勾选变化由父组件决定是否采纳。
 */
const inner = computed<boolean>({
  get: () => props.modelValue,
  set: (v) => {
    emit("update:modelValue", v);
    emit("change", v);
  },
});
</script>

<template>
  <el-checkbox
    v-model="inner"
    :disabled="disabled"
    :size="size"
    class="app-check"
  >
    <slot />
  </el-checkbox>
</template>

<style scoped>
/* 穿透覆盖：勾选框与文字字号都需跟随全局缩放系数 */
.app-check :deep(.el-checkbox__label) {
  font-size: calc(13px * var(--font-scale));
}

.app-check :deep(.el-checkbox__inner) {
  border-radius: var(--radius-xs);
}
</style>
