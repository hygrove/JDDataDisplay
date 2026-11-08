<script setup lang="ts">
// 应用下拉选择：Element Plus 的 el-select 收敛版。
//
// 存在的理由：手写 <select> 的原生下拉在各浏览器/系统上样式差异大，
// 且无法跟随字号缩放；el-select 统一渲染且可被令牌驱动。
//
// D9 增强：选项可带 group / badge / tone，用于店铺下拉「分组 + 搜索 +
// 类型徽章 + 底色」。三项全是**可选字段**：库存页传的扁平 options 没有这些
// 字段，仍走原来的扁平渲染，0 行为变化。
import { computed } from "vue";

/**
 * 单个选项。
 */
export interface AppSelectOption {
  /** 选项值 */
  value: number | string;
  /** 显示文本（同时是 filterable 的匹配文本） */
  label: string;
  /** 可选分组键；任一选项带 group 即启用分组渲染 */
  group?: string;
  /** 可选分组标题（由调用方按字典生成，通常含数量） */
  groupLabel?: string;
  /** 可选右侧徽章文案 */
  badge?: string;
  /** 可选语义色调键，映射到 CSS 类 as-tone-<tone>；未知键走默认中性样式 */
  tone?: string;
}

/** 分组视图的一项。 */
interface OptionGroup {
  key: string;
  label: string;
  items: AppSelectOption[];
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
    /** 是否可搜索；分组下拉建议开启 */
    filterable?: boolean;
    /** 无匹配时的文案 */
    noMatchText?: string;
  }>(),
  {
    width: 96,
    disabled: false,
    filterable: false,
    noMatchText: "无匹配数据",
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

/**
 * 分组视图：带 group 的选项按首次出现顺序成组；全部不带 group 时返回 null，
 * 保持旧版扁平渲染（库存页 0 影响）。
 *
 * @returns 分组数组，或 `null` 表示无需分组渲染。
 */
const groups = computed<OptionGroup[] | null>(() => {
  const withGroup = props.options.filter((o) => o.group);
  if (!withGroup.length) return null;
  const map = new Map<string, OptionGroup>();
  for (const o of withGroup) {
    const key = o.group as string;
    let g = map.get(key);
    if (!g) {
      g = { key, label: o.groupLabel ?? key, items: [] };
      map.set(key, g);
    }
    g.items.push(o);
  }
  return [...map.values()];
});

/** 置顶的扁平项（如「全部店铺」）；无分组时就是全部选项。 */
const flatOptions = computed<AppSelectOption[]>(() =>
  groups.value ? props.options.filter((o) => !o.group) : props.options,
);
</script>

<template>
  <el-select
    v-model="inner"
    :disabled="disabled"
    :filterable="filterable"
    :no-match-text="noMatchText"
    class="app-select"
    :style="{ width: width + 'px' }"
    popper-class="app-select-popper"
  >
    <!-- 置顶项不属于任何分组；无分组模式下它就是完整选项列表 -->
    <el-option
      v-for="opt in flatOptions"
      :key="`flat-${opt.value}`"
      :label="opt.label"
      :value="opt.value"
    />
    <template v-if="groups">
      <el-option-group v-for="g in groups" :key="g.key" :label="g.label">
        <el-option
          v-for="opt in g.items"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        >
          <!-- 三重线索：色条（辅助）+ 店名（语义）+ 文字徽章（结构），
               ⛔ 颜色不是唯一区分手段（色弱 / 黑白打印仍可读）。 -->
          <div class="as-opt" :class="opt.tone ? `as-tone-${opt.tone}` : ''">
            <span class="as-bar"></span>
            <span class="as-name">{{ opt.label }}</span>
            <span v-if="opt.badge" class="as-badge">{{ opt.badge }}</span>
          </div>
        </el-option>
      </el-option-group>
    </template>
  </el-select>
</template>

<style scoped>
/* 穿透覆盖：EP 的选择器正文写死 14px，需跟随全局缩放系数。
   下挂面板被 teleport 到 body，scoped 选择器够不到 → 见下方非 scoped 样式。 */
.app-select :deep(.el-select__wrapper) {
  font-size: calc(13px * var(--font-scale));
  min-height: calc(28px * var(--font-scale));
}
</style>

<style>
/* ⚠️ el-select 的下拉面板 teleport 到 body，scoped 样式无法命中，必须用非 scoped
   样式 + popper-class 前缀限定作用域。颜色一律引用 style.css 的全局语义令牌，
   ⛔ 不写魔法色值、不定义 --el-* 变量（收敛层纪律）。 */
.app-select-popper .as-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: calc(26px * var(--font-scale));
  padding: 2px 6px;
  border-radius: 5px;
  font-size: calc(12.5px * var(--font-scale));
}
.app-select-popper .as-bar {
  width: 3px;
  height: 16px;
  border-radius: 2px;
  flex: none;
  background: var(--color-border-2);
}
.app-select-popper .as-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-select-popper .as-badge {
  flex: none;
  font-size: calc(10.5px * var(--font-scale));
  font-weight: 700;
  border: 1px solid var(--color-border-2);
  border-radius: 4px;
  padding: 0 6px;
  color: var(--color-text-3);
  background: var(--color-surface);
}
/* 明显底色（用户 2026-11-08 拍板）：自营蓝 / POP 琥珀。
   令牌只在全局 :root 新增，不修改任何既有值 ⇒ 全站品牌色零变化。
   每种色调只声明 3 个语义变量、渲染规则共用一组：新增类型时照抄一组变量
   即可，不必复制整段渲染规则（与 opsDaily.ts 的 tone 边界说明对齐）。 */
.app-select-popper .as-tone-self {
  --as-bg: var(--color-shop-self-bg);
  --as-fg: var(--color-shop-self-fg);
  --as-line: var(--color-shop-self-line);
}
.app-select-popper .as-tone-pop {
  --as-bg: var(--color-shop-pop-bg);
  --as-fg: var(--color-shop-pop-fg);
  --as-line: var(--color-shop-pop-line);
}
.app-select-popper .as-tone-self,
.app-select-popper .as-tone-pop { background: var(--as-bg); color: var(--as-fg); }
.app-select-popper .as-tone-self .as-bar,
.app-select-popper .as-tone-pop .as-bar { background: var(--as-fg); }
.app-select-popper .as-tone-self .as-badge,
.app-select-popper .as-tone-pop .as-badge {
  border-color: var(--as-line); color: var(--as-fg); background: var(--color-surface);
}
</style>
