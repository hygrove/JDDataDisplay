<script setup lang="ts">
// 应用下拉选择：Element Plus 的 el-select 收敛版。
//
// 存在的理由：手写 <select> 的原生下拉在各浏览器/系统上样式差异大，
// 且无法跟随字号缩放；el-select 统一渲染且可被令牌驱动。
//
// D9 增强：选项可带 group / badge / tone，用于店铺下拉「分组 + 搜索 +
// 类型徽章 + 悬停底色」。这些全是**可选字段**：库存页传的扁平 options 没有
// 这些字段，仍走原来的扁平渲染，0 行为变化。
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
  /** 可选语义色调键：常驻只染色条 / 徽章，鼠标悬停时整行染对应底色；
   *  未知键走中性 hover（字典驱动，新增类型 0 改码） */
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
          :class="opt.tone ? `as-host as-host-${opt.tone}` : ''"
        >
          <!-- 三重线索：色条（辅助）+ 店名（语义）+ 文字徽章（结构），
               ⛔ 颜色不是唯一区分手段（色弱 / 黑白打印仍可读）。
               el-option 的 class 会透传到 <li>：hover 要染「整行」而非内部
               div，且 CSS 变量必须由 li 定义（父元素读不到子元素的变量）。 -->
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
/* 明显底色 = 鼠标悬停提示（2026-11-08 用户看实测截图后的纠正）。
   拍板预览是「常驻白底 + 色条 + 徽章」，上一轮误把浅蓝 / 浅琥珀铺成每行
   常驻底色：既与预览不符，又把 EP 自带的 hover 高亮盖住 ⇒ 移入毫无反馈。
   现在常驻只留色条 + 文字徽章，hover 时按类型整行染「明显底色」。
   ⚠️ 变量定义在 li（.as-host-*）而非内部 div：li 自己要染色，父元素读不到
   子元素变量；内部 .as-bar / .as-badge 靠继承拿同一组变量。
   ⚠️ .as-tone-* 仍留在 div 上，只作类型标记（验证脚本按它数分组）。
   ⚠️ 未知 tone 没有变量 ⇒ var(--as-*, fallback) 自动降级为中性 hover，
   字典驱动新增类型 0 改码（与 opsDaily.ts 的 tone 边界说明对齐）。 */
.app-select-popper .as-host-self {
  --as-bg: var(--color-shop-self-bg);
  --as-fg: var(--color-shop-self-fg);
  --as-line: var(--color-shop-self-line);
}
.app-select-popper .as-host-pop {
  --as-bg: var(--color-shop-pop-bg);
  --as-fg: var(--color-shop-pop-fg);
  --as-line: var(--color-shop-pop-line);
}
/* 常驻：整行不铺底（对齐方案预览），类型线索只落在色条与徽章上。 */
.app-select-popper .as-host .as-bar { background: var(--as-fg, var(--color-border-2)); }
.app-select-popper .as-host .as-badge {
  border-color: var(--as-line, var(--color-border-2));
  color: var(--as-fg, var(--color-text-3));
  background: var(--color-surface);
}
/* hover：整行染类型色（特定性压过 EP 自带的浅灰 hover）；未知类型走中性浅灰。
   ⛔ 不写在内部 .as-opt 上：那样 li 的 padding 区域不会亮，且会再挡住 EP hover。 */
.app-select-popper .as-host { transition: background-color 0.12s ease; }
.app-select-popper .el-select-dropdown__item.as-host:hover {
  background: var(--as-bg, var(--color-surface-2));
}
</style>
