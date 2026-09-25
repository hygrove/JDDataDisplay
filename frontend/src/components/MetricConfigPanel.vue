<script setup lang="ts">
// 指标配置面板：一个「指标配置」按钮（品牌图标）+ 下拉勾选面板。
// 组件本身不关心数据来自哪里：选中的 key 数组由父组件通过 v-model:keys 传入/回写。
// 勾选结果由 stores/metrics.ts 持久化到 localStorage，模块页与单品分析页共用。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { METRICS, METRIC_GROUPS, DEFAULT_METRIC_KEYS } from "../metrics";

/**
 * 组件 Props。
 */
const props = defineProps<{
  /** 当前勾选的指标 key 数组（由父组件通过 v-model 传入） */
  keys: string[];
}>();

/**
 * 组件事件：`update:keys` —— 勾选变化时回写新的 key 数组（配合 v-model:keys 使用）。
 */
const emit = defineEmits<{ (e: "update:keys", value: string[]): void }>();

/** 下拉面板是否展开 */
const open = ref(false);
/** 面板根节点引用，用于「点击外部关闭」时判断点击是否在面板内 */
const rootRef = ref<HTMLElement | null>(null);

/**
 * 当前勾选集合（Set 形式，便于模板里 O(1) 判断是否勾选）。
 *
 * @returns {Set<string>} 已勾选的指标 key 集合。
 * @example
 * ```ts
 * selected.value.has("amount"); // true / false
 * ```
 */
const selected = computed(() => new Set(props.keys));

/**
 * 按分组整理指标（供面板分组渲染），并过滤掉空分组。
 *
 * @returns {Array<{key: string; title: string; hint: string; items: MetricSpec[]}>}
 *      分组元信息 + 该组下的指标列表；没有任何指标的分组被丢弃。
 * @example
 * ```ts
 * groups.value[0].title;  // "商品明细表"
 * groups.value[0].items;  // 该组的 9 个指标
 * ```
 */
const groups = computed(() =>
  METRIC_GROUPS.map((g) => ({
    ...g,
    items: METRICS.filter((m) => m.group === g.key),
  })).filter((g) => g.items.length > 0),
);

/**
 * 回写勾选结果。
 *
 * @remarks
 * 输出前统一按 METRICS 清单顺序重排：
 * 否则「先勾访客数再勾成交金额」会让页面上的指标顺序跟着勾选顺序变，
 * 用户每次改配置都会看到卡片顺序跳来跳去。
 *
 * @param {string[]} keys - 新的 key 数组（顺序任意、可能含无效 key）。
 * @returns {void} 无返回值；通过 emit("update:keys") 把规范化后的数组交给父组件。
 * @example
 * ```ts
 * writeKeys(["visitors", "amount"]); // 实际输出 ["amount", "visitors"]（按清单顺序）
 * writeKeys([]);                     // 清空
 * ```
 */
function writeKeys(keys: string[]) {
  // 统一按清单顺序输出，避免勾选顺序影响卡片/列的实际排列
  const set = new Set(keys);
  emit("update:keys", METRICS.filter((m) => set.has(m.key as string)).map((m) => m.key as string));
}

/**
 * 切换单个指标的勾选状态。
 *
 * @param {string} key - 要切换的指标 key。
 * @returns {void} 无返回值；内部复用 writeKeys 保证输出顺序一致。
 * @example
 * ```ts
 * toggle("orders"); // 成交单量：勾选 <-> 取消
 * ```
 */
function toggle(key: string) {
  const next = new Set(props.keys);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  writeKeys([...next]);
}

/**
 * 全部指标的 key 列表（「全选」快捷操作用）。
 *
 * @returns {string[]} METRICS 中所有指标的 key，按清单顺序。
 * @example
 * ```ts
 * allKeys().length; // 15
 * ```
 */
function allKeys() {
  return METRICS.map((m) => m.key as string);
}

/**
 * 点击面板外部时关闭下拉。
 *
 * @param {MouseEvent} e - 文档点击事件。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * // 由 document 的 click 监听触发；点击面板内部时保持展开
 * ```
 */
function onDocClick(e: MouseEvent) {
  // 面板未展开时不做任何判断，省一次 contains 计算
  if (!open.value) return;
  // 点击落在面板内部 -> 不关闭（否则点选项会立刻把面板关掉）
  if (rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false;
}

/**
 * 按 Esc 关闭下拉（无障碍/键盘操作习惯）。
 *
 * @param {KeyboardEvent} e - 键盘事件。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * // 由 document 的 keydown 监听触发
 * ```
 */
function onEsc(e: KeyboardEvent) {
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
  <div class="mc" ref="rootRef">
    <button class="mc-btn" :class="{ active: open }" @click="open = !open">
      <svg class="mc-ico" viewBox="0 0 1024 1024" width="16" height="16" xmlns="http://www.w3.org/2000/svg"><path d="M199.978667 488.021333H416c38.4 0 71.978667-33.621333 71.978667-72.021333V199.978667c0-38.4-33.578667-71.978667-71.978667-71.978667H199.978667C161.578667 128 128 161.621333 128 200.021333V416c0 38.4 33.578667 72.021333 71.978667 72.021333zM535.978667 718.378667c0 96 81.621333 177.621333 182.4 177.621333 96 0 177.621333-81.621333 172.8-177.621333 0-96-76.8-177.578667-177.621334-177.578667-96 0-177.578667 76.8-177.578666 177.578667z" fill="#fa4f39" opacity=".4"/><path d="M608 488.021333h215.978667c38.4 0 72.021333-33.621333 72.021333-72.021333V199.978667C896 161.621333 862.378667 128 819.2 128h-211.2c-38.4 0-72.021333 33.621333-72.021333 72.021333V416c0 38.4 33.621333 72.021333 72.021333 72.021333zM199.978667 896H416c38.4 0 71.978667-33.621333 71.978667-76.8v-211.2c0-38.4-33.578667-72.021333-71.978667-72.021333H199.978667c-38.4 0-71.978667 33.621333-71.978667 72.021333v216.021333c0 38.4 33.578667 71.978667 71.978667 71.978667z" fill="#fa4f39"/></svg>
      <span>指标配置</span>
      <span class="mc-count">{{ props.keys.length }}/{{ METRICS.length }}</span>
    </button>

    <div v-if="open" class="mc-panel">
      <div class="mc-head">
        <div>
          <div class="mc-title">选择要展示的指标</div>
          <div class="mc-sub">同时作用于上方指标卡与下方明细列，并同步到单品分析页</div>
        </div>
        <button class="mc-close" @click="open = false">✕</button>
      </div>

      <div class="mc-actions">
        <span class="mc-actions-label">快捷操作</span>
        <button class="mc-link" @click="writeKeys(allKeys())">全选</button>
        <button class="mc-link" @click="writeKeys([...DEFAULT_METRIC_KEYS])">恢复默认</button>
        <button class="mc-link" @click="writeKeys([])">清空</button>
      </div>

      <div class="mc-body">
        <div v-for="g in groups" :key="g.key" class="mc-group">
          <div class="mc-group-head">
            <span class="mc-group-title">{{ g.title }}</span>
            <span class="mc-group-hint">{{ g.hint }}</span>
          </div>
          <div class="mc-items">
            <label
              v-for="m in g.items"
              :key="m.key"
              class="mc-item"
              :class="{ on: selected.has(m.key as string) }"
            >
              <input
                type="checkbox"
                :checked="selected.has(m.key as string)"
                @change="toggle(m.key as string)"
              />
              <span>{{ m.title }}</span>
            </label>
          </div>
        </div>
      </div>

      <div class="mc-foot">
        <span>勾选后自动保存，刷新 / 换页面依然生效</span>
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
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 7px 12px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
}
.mc-btn:hover,
.mc-btn.active {
  border-color: #e1251b;
  color: #e1251b;
  box-shadow: 0 0 0 3px rgba(225, 37, 27, 0.08);
}
.mc-ico {
  font-size: 13px;
}
.mc-count {
  font-size: 11px;
  color: #94a3b8;
  background: #f1f5f9;
  border-radius: 10px;
  padding: 1px 7px;
  font-variant-numeric: tabular-nums;
}
.mc-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  width: 460px;
  max-width: calc(100vw - 40px);
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
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
  border-bottom: 1px solid #f1f5f9;
}
.mc-title {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}
.mc-sub {
  margin-top: 4px;
  font-size: 11.5px;
  color: #94a3b8;
  line-height: 1.6;
}
.mc-close {
  background: none;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 4px;
}
.mc-close:hover {
  color: #e1251b;
}
.mc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid #f1f5f9;
  background: #fafbfc;
}
.mc-actions-label {
  font-size: 11.5px;
  color: #94a3b8;
  margin-right: 2px;
}
.mc-link {
  background: none;
  border: 1px solid #e2e8f0;
  border-radius: 5px;
  padding: 4px 10px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.mc-link:hover {
  border-color: #e1251b;
  color: #e1251b;
}
.mc-body {
  max-height: 320px;
  overflow: auto;
  padding: 6px 14px 12px;
}
.mc-group {
  padding: 10px 0 4px;
  border-bottom: 1px dashed #f1f5f9;
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
  color: #334155;
}
.mc-group-hint {
  font-size: 11px;
  color: #cbd5e1;
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
  border-radius: 6px;
  font-size: 12.5px;
  color: #475569;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s, color 0.15s;
}
.mc-item:hover {
  background: #f8fafc;
}
.mc-item.on {
  color: #0f172a;
  background: #fff5f4;
}
.mc-item input {
  accent-color: #e1251b;
  cursor: pointer;
}
.mc-ico {
  flex: none;
  width: 14px;
  height: 14px;
  color: #94a3b8;
}
.mc-item.on .mc-ico {
  color: #e1251b;
}
.mc-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-top: 1px solid #f1f5f9;
  background: #fafbfc;
  font-size: 11.5px;
  color: #94a3b8;
}
.mc-done {
  background: linear-gradient(135deg, #e1251b, #c81e14);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 12.5px;
  cursor: pointer;
}
.mc-done:hover {
  box-shadow: 0 4px 10px rgba(225, 37, 27, 0.28);
}
@media (max-width: 520px) {
  .mc-items {
    grid-template-columns: 1fr;
  }
}
</style>
