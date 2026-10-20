<script setup lang="ts">
// 库存「指标配置」面板：一个按钮 + 下拉勾选面板。
// 与 MetricConfigPanel 的区别：
//   1) 指标来自库存 meta.metrics（identity/per_city/national），不是 POP 的 metrics.ts；
//   2) 仅管理「非锁定」指标（identity 恒可见、不可勾选，单独以禁用态展示）；
//   3) 配色跟随库存视图的蓝色主题（父级已把 --color-brand 覆盖成 #2563eb），
//      不引用任何写死的红色 rgba，避免红蓝混色。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { InventoryMetric } from "../../types";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 完整指标清单（来自 meta，已按展示顺序） */
  metrics: InventoryMetric[];
  /** 当前勾选的非锁定指标 key（v-model:keys） */
  keys: string[];
}>();

/** 勾选变化回写（配合 v-model:keys）。 */
const emit = defineEmits<{ (e: "update:keys", value: string[]): void }>();

/** 下拉面板是否展开 */
const open = ref(false);
/** 面板根节点引用，用于「点击外部关闭」 */
const rootRef = ref<HTMLElement | null>(null);

/** 已勾选集合（O(1) 判断）。 */
const selected = computed(() => new Set(props.keys));

/** 按类型分组（identity 恒可见、per_city / national 可勾选）。 */
const identityMetrics = computed(() => props.metrics.filter((m) => m.kind === "identity"));
const selectableMetrics = computed(() => props.metrics.filter((m) => !m.locked));

/**
 * 回写勾选结果：只保留非锁定且有效的 key，并按 meta 顺序重排。
 *
 * @param {string[]} keys - 新的 key 数组（顺序任意）。
 * @returns {void} 无返回值。
 */
function writeKeys(keys: string[]): void {
  const set = new Set(keys);
  emit("update:keys", props.metrics.filter((m) => !m.locked && set.has(m.key)).map((m) => m.key));
}

/** 切换单个指标勾选。 */
function toggle(key: string): void {
  const next = new Set(props.keys);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  writeKeys([...next]);
}

/** 全部可选指标 key。 */
function allKeys(): string[] {
  return props.metrics.filter((m) => !m.locked).map((m) => m.key);
}

/** 后端默认可见的非锁定指标 key。 */
function defaultKeys(): string[] {
  return props.metrics.filter((m) => !m.locked && m.default_visible).map((m) => m.key);
}

/** 点击面板外部关闭。 */
function onDocClick(e: MouseEvent): void {
  if (!open.value) return;
  if (rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false;
}
/** Esc 关闭。 */
function onEsc(e: KeyboardEvent): void {
  if (e.key === "Escape") open.value = false;
}
onMounted(() => {
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onEsc);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onEsc);
});
</script>

<template>
  <div class="mc" data-testid="inv-metric-config" ref="rootRef">
    <button
      class="mc-btn"
      data-testid="inv-metric-config-btn"
      :class="{ active: open }"
      @click="open = !open"
    >
      <svg class="mc-ico" viewBox="0 0 1024 1024" width="16" height="16" xmlns="http://www.w3.org/2000/svg"><path d="M199.978667 488.021333H416c38.4 0 71.978667-33.621333 71.978667-72.021333V199.978667c0-38.4-33.578667-71.978667-71.978667-71.978667H199.978667C161.578667 128 128 161.621333 128 200.021333V416c0 38.4 33.578667 72.021333 71.978667 72.021333zM535.978667 718.378667c0 96 81.621333 177.621333 182.4 177.621333 96 0 177.621333-81.621333 172.8-177.621333 0-96-76.8-177.578667-177.621334-177.578667-96 0-177.578667 76.8-177.578666 177.578667z" fill="#2563eb" opacity=".4"/><path d="M608 488.021333h215.978667c38.4 0 72.021333-33.621333 72.021333-72.021333V199.978667C896 161.621333 862.378667 128 819.2 128h-211.2c-38.4 0-72.021333 33.621333-72.021333 72.021333V416c0 38.4 33.621333 72.021333 72.021333 72.021333zM199.978667 896H416c38.4 0 71.978667-33.621333 71.978667-76.8v-211.2c0-38.4-33.578667-72.021333-71.978667-72.021333H199.978667c-38.4 0-71.978667 33.621333-71.978667 72.021333v216.021333c0 38.4 33.578667 71.978667 71.978667 71.978667z" fill="#2563eb"/></svg>
      <span>指标配置</span>
      <span class="mc-count">{{ props.keys.length }}/{{ selectableMetrics.length }}</span>
    </button>

    <div v-if="open" class="mc-panel" data-testid="inv-metric-config-panel">
      <div class="mc-head">
        <div>
          <div class="mc-title">选择要展示的指标</div>
          <div class="mc-sub">勾选后自动保存；身份列始终展示</div>
        </div>
        <button class="mc-close" @click="open = false">✕</button>
      </div>

      <div class="mc-actions">
        <span class="mc-actions-label">快捷操作</span>
        <button class="mc-link" @click="writeKeys(allKeys())">全选</button>
        <button class="mc-link" @click="writeKeys(defaultKeys())">恢复默认</button>
        <button class="mc-link" @click="writeKeys([])">清空</button>
      </div>

      <div class="mc-body">
        <!-- 身份列：恒可见，禁用勾选 -->
        <div class="mc-group">
          <div class="mc-group-head">
            <span class="mc-group-title">身份列（始终展示）</span>
          </div>
          <div class="mc-items">
            <label
              v-for="m in identityMetrics"
              :key="m.key"
              class="mc-item locked"
            >
              <input type="checkbox" checked disabled />
              <span>{{ m.title }}</span>
            </label>
          </div>
        </div>

        <!-- 每城指标 -->
        <div class="mc-group">
          <div class="mc-group-head">
            <span class="mc-group-title">每城指标</span>
            <span class="mc-group-hint">展开为 8 城子列</span>
          </div>
          <div class="mc-items">
            <label
              v-for="m in selectableMetrics.filter((x) => x.kind === 'per_city')"
              :key="m.key"
              class="mc-item"
              :class="{ on: selected.has(m.key) }"
            >
              <input
                type="checkbox"
                :checked="selected.has(m.key)"
                @change="toggle(m.key)"
              />
              <span>{{ m.title }}</span>
            </label>
          </div>
        </div>

        <!-- 全国指标 -->
        <div class="mc-group">
          <div class="mc-group-head">
            <span class="mc-group-title">全国指标</span>
          </div>
          <div class="mc-items">
            <label
              v-for="m in selectableMetrics.filter((x) => x.kind === 'national')"
              :key="m.key"
              class="mc-item"
              :class="{ on: selected.has(m.key) }"
            >
              <input
                type="checkbox"
                :checked="selected.has(m.key)"
                @change="toggle(m.key)"
              />
              <span>{{ m.title }}</span>
            </label>
          </div>
        </div>
      </div>

      <div class="mc-foot">
        <span>勾选后自动保存，刷新依然生效</span>
        <button class="mc-done" @click="open = false">完成</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mc {
  position: relative;
}
.mc-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-md);
  padding: 7px 12px;
  font-size: 13px;
  color: var(--color-text-2);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
}
.mc-btn:hover,
.mc-btn.active {
  border-color: var(--color-brand);
  color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.mc-ico {
  flex: none;
  width: 14px;
  height: 14px;
  color: var(--color-text-5);
}
.mc-count {
  font-size: 11px;
  color: var(--color-text-5);
  background: var(--color-bg);
  border-radius: var(--radius-pill);
  padding: 1px 7px;
  font-variant-numeric: tabular-nums;
}
.mc-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  width: 440px;
  max-width: calc(100vw - 40px);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.14);
  z-index: 60;
  overflow: hidden;
}
.mc-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--color-bg);
}
.mc-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-strong);
}
.mc-sub {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--color-text-5);
}
.mc-close {
  background: none;
  border: none;
  color: var(--color-text-5);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 4px;
}
.mc-close:hover {
  color: var(--color-brand);
}
.mc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-bg);
  background: var(--color-surface-3);
}
.mc-actions-label {
  font-size: 11.5px;
  color: var(--color-text-5);
  margin-right: 2px;
}
.mc-link {
  background: none;
  border: 1px solid var(--color-border-2);
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--color-text-3);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.mc-link:hover {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.mc-body {
  max-height: 340px;
  overflow: auto;
  padding: 6px 14px 12px;
}
.mc-group {
  padding: 10px 0 4px;
  border-bottom: 1px dashed var(--color-bg);
}
.mc-group:last-child {
  border-bottom: none;
}
.mc-group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
.mc-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-2);
}
.mc-group-hint {
  font-size: 11px;
  color: var(--color-text-6);
}
.mc-items {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 10px;
}
.mc-item {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 8px;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  color: var(--color-text-3);
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, color 0.15s;
}
.mc-item:hover {
  background: var(--color-surface-2);
}
.mc-item.on {
  color: var(--color-text-strong);
  background: var(--color-brand-tint-4);
}
.mc-item.locked {
  cursor: default;
  color: var(--color-text-5);
  background: var(--color-surface-2);
}
.mc-item input {
  accent-color: var(--color-brand);
  cursor: pointer;
}
.mc-item.locked input {
  cursor: default;
}
.mc-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-top: 1px solid var(--color-bg);
  background: var(--color-surface-3);
  font-size: 11.5px;
  color: var(--color-text-5);
}
.mc-done {
  background: var(--color-brand);
  color: #fff;
  border: none;
  border-radius: var(--radius-md);
  padding: 6px 14px;
  font-size: 12.5px;
  cursor: pointer;
}
.mc-done:hover {
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.28);
}
@media (max-width: 520px) {
  .mc-items {
    grid-template-columns: 1fr;
  }
}
</style>
