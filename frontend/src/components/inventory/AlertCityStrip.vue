<script setup lang="ts">
// 8 城预警紧凑条：城市名 + 常驻阈值输入。
//
// 设计要点：
// - 阈值是「城市级」的（一个数字管全城所有 SKU，2026-10 grilling 拍板）；
//   输入框 change 时抛 update:threshold，由父级 PUT 后端并刷新（持久化到 SQLite）。
// - 单元格底色统一为库存视图主题蓝渐变（#eff6ff → 白，对应父级覆盖的
//   --color-brand: #2563eb），与全站红品牌隔离；预警/在途计数与建议补货
//   合计已按要求移除，不再以徽章 / 文字展示。
// - 下柜 SKU 已由后端排除，计数口径与补货工作台一致。
// - 布局：容器通栏，8 城恒定单行、格子等分伸缩、整组居中；不够宽才换行，
//   详见 <style> 段注释（2026-10 重排）。
import type { AlertCityStat } from "../../types";

const props = defineProps<{
  /** 8 城汇总（顺序与 meta.cities 一致） */
  cities: AlertCityStat[];
  /** 阈值保存中（禁用输入，防重复提交） */
  saving?: boolean;
}>();

const emit = defineEmits<{
  /** 阈值编辑提交（change 触发一次一条；父级负责 PUT + 全量刷新） */
  (e: "update:threshold", v: { code: string; threshold: number }): void;
}>();

/** 阈值输入 change：合法化后上报父级（父级 PUT 成功后回填新 summary）。 */
function onThreshold(c: AlertCityStat, e: Event): void {
  const raw = Number((e.target as HTMLInputElement).value);
  const v = Number.isFinite(raw) ? Math.max(0, Math.round(raw)) : 0;
  // 值没变就不打扰后端
  if (v === c.threshold) return;
  emit("update:threshold", { code: c.code, threshold: v });
}
</script>

<template>
  <div class="strip" data-testid="alert-city-strip">
    <div
      v-for="c in props.cities"
      :key="c.code"
      class="cell"
      :data-testid="'alert-city-' + c.code"
    >
      <span class="name">{{ c.city }}</span>
      <div class="th-row">
        <label class="th-label">阈值
          <input
            class="th-input"
            type="number"
            min="0"
            step="1"
            :value="c.threshold"
            :disabled="props.saving"
            :data-testid="'alert-threshold-' + c.code"
            @change="onThreshold(c, $event)"
          >
        </label>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============================================================
   城市条布局（2026-10 重排）：8 城恒定单行、格子等分、整组居中
   ============================================================
   - 容器：通栏（width:100%，不再用 fit-content）→ 用满内容列，把原先
     「卡外大片留白」变成格子本身的宽度；卡片随父级宽度自然向外扩展。
   - 格子：flex:1 1 0 + min-width:104px + max-width:180px
     ① 基准 0：换行判定按 min-width 算（8×104 + 7×1 = 839px），只要容器
        ≥ 839px 就恒定 8 城单行；不会像原先 flex:1 那样被等分拉伸变形。
     ② grow:1：单行时 8 格等分铺满，每格 = clamp(104, (W-7)/8, 180)，
        既不浪费空间，也没有「行尾参差」。
     ③ max-width:180：超宽屏时整组封顶居中，两侧留白，容器仍继续外扩。
   - 窄屏（容器 < 839px）：自动换行成多行，每行内格子等分铺满该行，
     内容完整显示、不溢出；单元格内边距收到 10px 以压低单行门槛。
   - 分隔：单元格之间 1px 白缝（gap，卡片底色即白），靠渐变断点区分城市格。
   ============================================================ */
.strip {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 1px;
  /* 通栏：用满内容列（原 fit-content 会把卡缩到内容宽，白白浪费两侧空间） */
  width: 100%;
  background: var(--color-surface);
  border: 1px solid var(--color-border-2);
  border-radius: var(--radius-lg, 14px);
  /* 圆角裁切：分组贴到卡片边时切掉圆角外的部分（原先是 overflow-x:auto 滚动容器） */
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  margin-bottom: 14px;
}
.cell {
  /* 等分伸缩：基准 0 + grow → 8 城单行时等分铺满；min-width 定住换行门槛，
     max-width 防止超宽屏上格子过宽（原先是 flex:1 + min-width:148px） */
  flex: 1 1 0;
  min-width: 104px;
  max-width: 180px;
  padding: 11px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  /* 主题蓝渐变：#eff6ff → 白，对应父级覆盖的 --color-brand: #2563eb（库存视图蓝色主题） */
  background: linear-gradient(180deg, #eff6ff, var(--color-surface) 72%);
  transition: background 0.2s;
}
.name {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--color-text-strong);
}
.th-row {
  display: flex;
  align-items: center;
  font-size: 11.5px;
  color: var(--color-text-4);
}
.th-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 700;
  color: var(--color-text-2);
  white-space: nowrap;
}
.th-input {
  width: 54px;
  height: 24px;
  padding: 0 6px;
  border: 1px solid var(--color-border-2);
  border-radius: 6px;
  font-size: 12px;
  text-align: center;
  color: var(--color-text-2);
  font-variant-numeric: tabular-nums;
  outline: none;
  background: var(--color-surface);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.th-input:focus {
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.th-input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
