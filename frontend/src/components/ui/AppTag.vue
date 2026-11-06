<script setup lang="ts">
// 应用标签：Element Plus 的 el-tag 收敛版。
//
// 存在的理由：状态胶囊在各页面出现多处（补货状态、预警档位、导入结果…），
// 规格不统一会显得像拼凑。包装一次统一圆角、字号、内边距。
//
// ⚠️ 涨跌语义不走这个组件：
//   本项目「红涨绿跌」（--color-up 红 / --color-down 绿）是**数值涨跌**语义，
//   而 EP 的 success(绿)/danger(红) 是**操作结果**语义。两者含义相反，
//   混用会误导用户（架构手册 §1.9 明确要求区分）。
//   涨跌标签继续用项目自己的 .up / .down 类，不要换成 el-tag 的 type。
import { computed } from "vue";

/**
 * 组件 Props。
 */
const props = withDefaults(
  defineProps<{
    /** 标签类型 */
    type?: "primary" | "success" | "info" | "warning" | "danger";
    /** 尺寸 */
    size?: "small" | "default" | "large";
    /** 是否可关闭 */
    closable?: boolean;
    /** 圆形胶囊 */
    round?: boolean;
  }>(),
  {
    type: "info",
    size: "small",
    closable: false,
    round: true,
  }
);

/**
 * 是否展示为「深字 + 浅底」的胶囊（与项目既有的 success-ink/success-tint 配套色一致）。
 *
 * 为什么这样配：项目里状态提示已经有一套成对配色令牌，
 * 让 EP 标签复用同一套色，页面里两种胶囊看起来是一家人。
 */
const useInkTint = computed(() => props.type === "success" || props.type === "danger");
</script>

<template>
  <el-tag
    :type="type"
    :size="size"
    :closable="closable"
    :round="round"
    class="app-tag"
    :class="{ 'app-tag--ink': useInkTint }"
    disable-transitions
  >
    <slot />
  </el-tag>
</template>

<style scoped>
/* 标签字号缩放由 style.css 的映射层统一覆盖（--el-tag-font-size 不跟
   --el-font-size-base 走），这里只处理项目语义色。 */
.app-tag--ink {
  border-color: transparent;
}

.app-tag--ink.el-tag--success {
  background-color: var(--color-success-tint);
  color: var(--color-success-ink);
}

.app-tag--ink.el-tag--danger {
  background-color: var(--color-danger-tint);
  color: var(--color-danger-ink);
}
</style>
