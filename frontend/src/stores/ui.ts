// 全局 UI 状态：仅承载「路由级加载遮罩」这类跨页面、与具体业务数据无关的瞬时态。
//
// 为什么需要它（而不是让页面自己管自己的遮罩）：
// 单品分析页是懒加载且依赖 echarts（chunk 较大），点击「单品分析 →」后
// 要等 chunk 下载 + 解析，这段时间内目标组件尚未挂载、它自己的遮罩根本不会出现，
// 用户就会感到「点了半天没反应」。
// 所以这里用 navigating 在「路由跳转一开始」就亮起全屏遮罩，
// 等组件挂载后（SpuAnalysisView.onMounted）再复位，把遮罩交棒给页面自身的遮罩，
// 两段等待无缝衔接、不闪空。
//
// 组合式写法：状态用 ref 承载，动作用普通函数承载，最后一并 return。
import { defineStore } from "pinia";
import { ref } from "vue";

/**
 * 全局 UI 状态 store（组合式 / Setup Store）。
 *
 * @remarks
 * 只放与业务数据无关、需要跨组件/跨页面共享的瞬时 UI 态；
 * 模块数据请放 stores/module.ts，指标勾选请放 stores/metrics.ts。
 *
 * @example
 * ```ts
 * const ui = useUiStore();
 * ui.setNavigating(true);   // 点亮全屏遮罩
 * ui.setNavigating(false);  // 关闭遮罩（由目标组件挂载后调用）
 * ```
 */
export const useUiStore = defineStore("ui", () => {
  /** 是否正在跳转到单品分析页（覆盖懒加载 chunk 的等待期） */
  const navigating = ref(false);
  /** 遮罩上显示的文案 */
  const loadingText = ref("数据加载中…");

  /**
   * 设置「正在跳转」标志，控制全局全屏加载遮罩的显示与隐藏。
   *
   * @param {boolean} v - true 显示遮罩，false 隐藏遮罩。
   * @returns {void} 无返回值。
   * @example
   * ```ts
   * // 路由守卫中点亮：
   * useUiStore().setNavigating(true);
   * // 目标组件挂载后交棒：
   * onMounted(() => useUiStore().setNavigating(false));
   * ```
   */
  function setNavigating(v: boolean): void {
    navigating.value = v;
  }

  return { navigating, loadingText, setNavigating };
});
