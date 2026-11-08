<script setup lang="ts">
// 应用日期区间选择器：Element Plus el-date-picker 的收敛版（2026-11-08 D7）。
//
// 存在的理由：两个独立的原生 <input type="date"> 是「两个控件」，用户要的是
// 一个整体；EP 的 daterange 在一个日历弹层里框选起止，且天然保证起 ≤ 止。
// 业务组件只传 [from, to] 字符串数组，不直接接触 EP 的 value-format /
// disabledDate / 事件载荷 ⇒ EP 始终是实现细节（ADR-0020 收敛层边界）。
import { computed } from "vue";

/** 日期区间值：[起始日, 结束日]，均为 YYYY-MM-DD；未选满时为 null。 */
export type AppDateRange = [string, string] | null;

const props = withDefaults(
  defineProps<{
    /** 当前区间（v-model 绑定） */
    modelValue: AppDateRange;
    /** 可选的最大日期（YYYY-MM-DD）；超出的日期在日历里禁用 */
    max?: string;
    /** 控件宽度（px） */
    width?: number;
  }>(),
  { max: undefined, width: 300 },
);

const emit = defineEmits<{
  (e: "update:modelValue", v: AppDateRange): void;
  (e: "change", v: AppDateRange): void;
}>();

/** 内部值代理：把 EP 的 v-model 收敛成项目自己的字符串数组契约。 */
const inner = computed<AppDateRange>({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
});

/**
 * 禁用晚于 max 的日期，替代原生 input 的 :max 属性。
 *
 * @remarks 用本地时间 23:59:59 收口，避免按 UTC 比较时把当天误判成「明天」。
 * @param d - EP 传入的待判定日期。
 * @returns true = 该日期不可选。
 */
function disabledDate(d: Date): boolean {
  if (!props.max) return false;
  return d.getTime() > new Date(`${props.max}T23:59:59`).getTime();
}

/**
 * 把 EP 的 change 事件透传给业务层。
 *
 * @remarks 刻意不接收 EP 的载荷类型：业务层（onFilterChange）只读页面自己的
 * from/to，载荷内容不参与判定；这样 EP 换版本改事件签名也不会波及这里。
 */
function onChange(): void {
  emit("change", inner.value);
}
</script>

<template>
  <el-date-picker
    v-model="inner"
    type="daterange"
    class="app-date-range"
    value-format="YYYY-MM-DD"
    range-separator="至"
    start-placeholder="开始日期"
    end-placeholder="结束日期"
    :disabled-date="disabledDate"
    :clearable="false"
    :style="{ width: width + 'px' }"
    @change="onChange"
  />
</template>

<style scoped>
/* 穿透覆盖：EP 的区间输入正文写死 14px，需跟随全局缩放系数。
   颜色 / 边框 / 圆角一律走 style.css 的 --el-* 映射层，⛔ 此处不写色值。 */
.app-date-range {
  font-size: calc(13px * var(--font-scale));
}
.app-date-range :deep(.el-range-input),
.app-date-range :deep(.el-range-separator) {
  font-size: calc(13px * var(--font-scale));
}
</style>
