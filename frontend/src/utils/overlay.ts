// 浮层互斥登记处：保证同一时刻全页面只有一个「日期浮层」处于展开状态。
//
// 为什么需要它：
//   单品分析页的「月份对比」模块里有**两个** DateRangePicker（基准月 / 对比月）。
//   它们的浮层（月份面板 / 日历面板）都是 fixed 定位挂在各自的 .drp 下，
//   组件之间彼此不知情 —— 点开左边再点右边，两块面板会同时展开、互相压盖。
//
// 为什么不用 provide/inject：
//   DateRangePicker 在模块页、单品分析页都是独立使用的，
//   不一定存在共同的父组件 provide 点。这里用模块级状态（天然全局唯一），
//   谁打开谁登记，登记动作自动把别人的挤掉，对调用方零心智负担。
import { ref, watch } from "vue";

/**
 * 当前展开的浮层实例标识；空串表示没有浮层展开。
 *
 * @remarks 每个 DateRangePicker 实例在创建时领到一个唯一 id，见 claimOverlay。
 */
export const activeOverlay = ref("");

/** 实例序号自增器：保证每次 setup() 拿到不同的 id（同一页面会创建多个实例） */
let seq = 0;

/**
 * 为一个浮层实例领取唯一标识。
 *
 * @param {string} prefix - 标识前缀，便于调试时看出是哪个组件（如 "drp"）。
 * @returns {string} 本实例唯一 id，形如 "drp#3"。
 * @example
 * ```ts
 * const myId = claimOverlay("drp"); // "drp#3"
 * ```
 */
export function claimOverlay(prefix: string): string {
  seq += 1;
  return `${prefix}#${seq}`;
}

/**
 * 把本实例与「全局唯一展开」绑定：自己打开时登记，别人打开时自己收起。
 *
 * @param {string} id - claimOverlay 领到的实例标识。
 * @param {() => void} onDeactivate - 被别人挤掉时的收起动作（关掉全部浮层）。
 * @returns {void} 无返回值；副作用是注册一个 watch，需在组件里调用。
 * @example
 * ```ts
 * const overlayId = claimOverlay("drp");
 * useOverlayLock(overlayId, () => {
 *   open.value = false;
 *   monthOpen.value = false;
 * });
 * ```
 */
export function useOverlayLock(id: string, onDeactivate: () => void): void {
  watch(activeOverlay, (cur) => {
    // 有别的实例登记了，自己让位；等于自己时不处理（否则会死循环式互相收起）
    if (cur && cur !== id) onDeactivate();
  });
}

/**
 * 声明「本实例的某个浮层已展开」，把其它浮层挤掉。
 *
 * @param {string} id - 本实例标识。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * function openPanel() {
 *   activeOverlay.value = overlayId; // 顺手关掉同页其它选择器的面板
 *   open.value = true;
 * }
 * ```
 */
export function acquireOverlay(id: string): void {
  activeOverlay.value = id;
}
