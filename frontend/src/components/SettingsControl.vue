<script setup lang="ts">
// 设置入口（齿轮）+ 设置对话框。
//
// 定位（2026-10-26 改版，方案①「侧栏底部独立设置行」）：
// 齿轮**不再挂在 logo 行内**——挂在 logo 旁会挤压「JD 数据平台」文字
// （logo 是 flex 行，齿轮占位后文字被压缩）。现改为侧栏底部独立一行，
// 按钮形态为「⚙ 设置」整行可点，语义更直白、也留出了整行扩展位。
// 点击弹出设置对话框（Teleport 到 body），字号缩放是其中第一个设置分组。
// 未来新增设置项（主题色等）时：在 set-body 里追加 .set-group 分组即可，
// 入口与对话框框架不变。
//
// 关闭方式：右上角 ×、点击遮罩、Esc。
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  useUiPrefsStore,
  FONT_SCALES,
  FONT_SCALE_LABELS,
} from "../stores/uiPrefs";

const uiPrefs = useUiPrefsStore();

/** 对话框显隐。 */
const open = ref(false);

/** 当前档位下标（0..4）。 */
const idx = computed(() => uiPrefs.index());

/** 当前档位中文名。 */
const label = computed(() => FONT_SCALE_LABELS[idx.value]);

/** 是否已到最小 / 最大档（边界禁用）。 */
const atMin = computed(() => idx.value === 0);
const atMax = computed(() => idx.value === FONT_SCALES.length - 1);

/**
 * 刻度位置：各档在轨道上均布（0% ~ 100%）。
 *
 * 由 FONT_SCALES 长度动态推导，改档位数时不必同步改这里。
 */
const TICK_POS = FONT_SCALES.map(
  (_, i) => `${((i / (FONT_SCALES.length - 1)) * 100).toFixed(2)}%`,
);

/** 填充条 / 圆点位置 = 当前档刻度位置。 */
const curPos = computed(() => TICK_POS[idx.value]);

/**
 * 直接选中某一档。
 *
 * @param {number} i - 目标档位下标（0..3）。
 * @returns {void}
 */
function pick(i: number): void {
  if (i < 0 || i >= FONT_SCALES.length) return;
  uiPrefs.apply(FONT_SCALES[i]);
}

/** Esc 关闭对话框。 */
function onKey(e: KeyboardEvent): void {
  if (e.key === "Escape") open.value = false;
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <button
    type="button"
    class="set-btn"
    aria-label="设置"
    data-testid="settings-open"
    @click="open = !open"
  >
    <svg
      class="set-ico"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <circle cx="12" cy="12" r="3" />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
      />
    </svg>
    <span class="set-btn-text">设置</span>
  </button>

  <Teleport to="body">
    <div
      v-if="open"
      class="set-overlay"
      data-testid="settings-dialog"
      @click.self="open = false"
    >
      <div class="set-card" role="dialog" aria-modal="true" aria-label="设置">
        <div class="set-head">
          <span class="set-title">设置</span>
          <button
            type="button"
            class="set-close"
            aria-label="关闭设置"
            data-testid="settings-close"
            @click="open = false"
          >
            ×
          </button>
        </div>

        <div class="set-body">
          <!-- 字号分组：未来新设置项（如主题色）在此追加 .set-group -->
          <div class="set-group">
            <div class="set-row1">
              <span class="set-label">字号</span>
              <span class="set-cur" data-testid="font-scale-value">{{ label }}</span>
            </div>
            <div class="set-slider">
              <button
                type="button"
                class="set-abtn"
                :disabled="atMin"
                aria-label="缩小字号"
                data-testid="font-scale-down"
                @click="uiPrefs.step(-1)"
              >
                A-
              </button>
              <div class="set-track">
                <div class="set-fill" :style="{ width: curPos }"></div>
                <button
                  v-for="(s, i) in FONT_SCALES"
                  :key="i"
                  type="button"
                  class="set-tick"
                  :class="{ on: i <= idx }"
                  :style="{ left: TICK_POS[i] }"
                  :aria-label="'字号档位 ' + FONT_SCALE_LABELS[i]"
                  :data-testid="'font-scale-tick-' + i"
                  @click="pick(i)"
                ></button>
                <div class="set-dot" :style="{ left: curPos }"></div>
              </div>
              <button
                type="button"
                class="set-abtn set-abtn-lg"
                :disabled="atMax"
                aria-label="放大字号"
                data-testid="font-scale-up"
                @click="uiPrefs.step(1)"
              >
                A+
              </button>
            </div>
            <div class="set-hint">调整全站正文字号；图表内标签保持不变</div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 齿轮按钮：侧栏底部独立一行（方案①）。
   整行可点 + 「⚙ 设置」文字标签——比裸图标更好找，也为后续设置项留出整行位置。
   ⚠️ 不要再放回 .logo 行内：那里是 flex 行，齿轮会挤压「JD 数据平台」文字。 */
.set-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: calc(14px * var(--font-scale));
  text-align: left;
  cursor: pointer;
}
.set-btn:hover {
  border-color: var(--color-brand);
  color: var(--color-brand);
  background: var(--color-brand-tint);
}
.set-btn-text {
  font-weight: 500;
}
.set-ico {
  width: 16px;
  height: 16px;
  flex: none;
  transition: transform 0.35s;
}
.set-btn:hover .set-ico {
  transform: rotate(40deg);
}

/* 遮罩 + 对话框 */
.set-overlay {
  position: fixed;
  inset: 0;
  z-index: 400;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(1.5px);
  -webkit-backdrop-filter: blur(1.5px);
  display: flex;
  align-items: center;
  justify-content: center;
}
.set-card {
  width: 320px;
  max-width: calc(100vw - 40px);
  background: var(--color-surface);
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.22);
  overflow: hidden;
}
.set-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px;
  border-bottom: 1px solid var(--color-border-2);
}
.set-title {
  font-size: calc(15px * var(--font-scale));
  font-weight: 600;
  color: var(--color-text);
}
.set-close {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-5);
  font-size: calc(17px * var(--font-scale));
  line-height: 1;
  cursor: pointer;
}
.set-close:hover {
  background: var(--color-surface-2);
  color: var(--color-text);
}
.set-body {
  padding: 16px;
}
.set-group {
  padding-bottom: 4px;
}
.set-group + .set-group {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-2);
}

/* 字号滑杆（双行：标签行 + 滑杆行） */
.set-row1 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.set-label {
  font-size: calc(13px * var(--font-scale));
  color: var(--color-text-2);
}
.set-cur {
  font-size: calc(12px * var(--font-scale));
  color: var(--color-brand);
  font-weight: 600;
}
.set-slider {
  display: flex;
  align-items: center;
  gap: 10px;
}
.set-abtn {
  flex: none;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--color-border-3);
  border-radius: 7px;
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: calc(12px * var(--font-scale));
  line-height: 1;
  cursor: pointer;
}
.set-abtn-lg {
  font-size: calc(14px * var(--font-scale));
}
.set-abtn:hover:not(:disabled) {
  border-color: var(--color-brand);
  color: var(--color-brand);
}
.set-abtn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.set-track {
  flex: 1;
  position: relative;
  height: 28px;
  display: flex;
  align-items: center;
}
.set-track::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 3px;
  transform: translateY(-50%);
  background: var(--color-border-2);
  border-radius: 2px;
}
.set-fill {
  position: absolute;
  left: 0;
  top: 50%;
  height: 3px;
  transform: translateY(-50%);
  background: var(--color-brand);
  border-radius: 2px;
  transition: width 0.18s ease;
}
.set-tick {
  position: absolute;
  top: 50%;
  width: 6px;
  height: 6px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--color-border-3);
  transform: translate(-50%, -50%);
  cursor: pointer;
  transition: background 0.18s;
}
.set-tick.on {
  background: var(--color-brand);
}
.set-dot {
  position: absolute;
  top: 50%;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--color-surface);
  border: 2.5px solid var(--color-brand);
  transform: translate(-50%, -50%);
  box-shadow: 0 1px 4px rgba(225, 37, 27, 0.35);
  pointer-events: none;
  transition: left 0.18s ease;
}
.set-hint {
  margin-top: 10px;
  font-size: calc(11px * var(--font-scale));
  color: var(--color-text-5);
}
</style>
