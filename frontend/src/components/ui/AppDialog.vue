<script setup lang="ts">
// 应用对话框：Element Plus 的 el-dialog 收敛版。
//
// 存在的理由：
//   1. 自建遮罩 + role="dialog" 要自己处理焦点陷阱、Esc、滚动锁与层级——
//      这些是易漏的无障碍细节；el-dialog 已经做对，直接复用。
//   2. 标题栏、关闭按钮、内边距在各页面各写一份会漂移；这里只保留一个可配置外壳。
//   3. 字号缩放必须作用到对话框正文（EP 把正文写死 14px），统一在包装层处理。
//
// 用法：
//   <AppDialog v-model="open" title="设置" :width="320" close-testid="settings-close">
//     ...正文...
//   </AppDialog>
import { computed } from "vue";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 显隐（v-model 绑定） */
    modelValue: boolean;
    /** 标题（同时作为对话框的无障碍名称） */
    title: string;
    /** 宽度：数字按 px 处理 */
    width?: string | number;
    /** 关闭按钮的 data-testid，便于视觉回归脚本定位 */
    closeTestid?: string;
  }>(),
  {
    width: 480,
    closeTestid: "",
  },
);

/**
 * 组件事件：v-model 双向绑定。
 */
const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
}>();

/** 内部值代理：el-dialog 的显隐由父组件决定。 */
const visible = computed<boolean>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

/** EP 的 width 支持 number | string，这里把数字统一成 px，避免隐式约定。 */
const dialogWidth = computed(() =>
  typeof props.width === "number" ? `${props.width}px` : props.width,
);
</script>

<template>
  <el-dialog
    v-model="visible"
    :width="dialogWidth"
    :show-close="false"
    align-center
    append-to-body
    destroy-on-close
    modal-class="app-dialog-overlay"
    class="app-dialog"
  >
    <!-- 自定义标题栏：保留 EP 的无障碍 id/class，同时把关闭按钮换成项目统一样式 -->
    <template #header="{ titleId, titleClass }">
      <div class="app-dialog-head">
        <span :id="titleId" :class="titleClass" class="app-dialog-title">{{ title }}</span>
        <button
          type="button"
          class="app-dialog-close"
          :data-testid="closeTestid || undefined"
          aria-label="关闭"
          @click="visible = false"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        </button>
      </div>
    </template>
    <slot />
  </el-dialog>
</template>

<style scoped>
/* 第 2 层 · 组件级覆盖：EP 对话框默认 16px 内边距、正文 14px 写死；
   这里把内边距交给标题栏/正文自己控制，正文接上字号缩放。
   ⚠️ el-dialog 会被 teleport 到 body，scoped 选择器够不到外壳，
   必须用 :global()。 */
:global(.app-dialog.el-dialog) {
  display: flex;
  flex-direction: column;
  max-height: 84vh;
  padding: 0;
  border-radius: 12px;
}

/* EP 默认把标题栏设为可拖动（cursor:move）；本项目对话框不做拖动 */
:global(.app-dialog .el-dialog__header) {
  padding: 0;
  margin: 0;
  cursor: default;
}

/* 正文是滚动容器：长内容（销速算法说明）在 84vh 内滚动，不撑出屏幕 */
:global(.app-dialog .el-dialog__body) {
  flex: 1;
  overflow: auto;
  padding: 14px 16px 16px;
  font-size: calc(13px * var(--font-scale));
}

.app-dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border-2);
}

.app-dialog-title.el-dialog__title {
  font-size: calc(15px * var(--font-scale));
  font-weight: 600;
  line-height: 1.4;
  color: var(--color-text);
}

.app-dialog-close {
  flex: none;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-4);
  cursor: pointer;
}
.app-dialog-close:hover {
  color: var(--color-text);
  background: var(--color-surface-2);
}
.app-dialog-close svg {
  width: 17px;
  height: 17px;
}
</style>
