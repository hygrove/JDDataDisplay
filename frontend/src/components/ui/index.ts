// Element Plus 收敛层：把第三方组件库的外观收敛到本项目 Design Tokens。
//
// 为什么需要这一层（而不是在业务组件里直接写 el-button/el-select）：
//   1. 业务组件里直接写 <el-button>，样式调整就要散落到几十个文件 ——
//      这正是架构手册 §6.2 反对的「多处各写一份」，改一处漏九处。
//   2. Element Plus 自带默认样式（蓝底白字、方正边框），与本项目的
//      品牌红 + 圆角 + 字号缩放体系不一致，直接用会「看起来像两个产品」。
//   3. 字号缩放（--font-scale）必须作用到 EP 组件上，否则用户调字号时
//      自研控件变了、EP 控件不变，页面出现半大半小。
//
// 收敛的三个层次（由内到外）：
//   第 1 层 · CSS 变量映射 —— 在 style.css 里把 --el-* 映射到本项目令牌
//                             （全局生效，一次改全站跟随）。
//   第 2 层 · 组件级覆盖   —— 在本文件用 :deep() 微调 EP 组件的内部结构
//                             （EP 的样式是 scoped 的，只能穿透）。
//   第 3 层 · 语义包装组件 —— 业务代码只用一个自己的组件名（如 <AppButton>），
//                             不直接依赖 EP，EP 换版本/换库时业务代码不动。
//
// 用法：业务组件里 import { AppButton } from "@/components/ui"，不要直接写 el-*。
// 这样「EP 是实现细节」这条边界才是硬的。

/* ============================================================
   第 3 层 · 语义包装组件
   ============================================================ */

/**
 * 应用按钮：包住 el-button，统一 size / 圆角 / 字号缩放。
 *
 * 为什么不让业务代码直接用 el-button：
 *   按钮在各页面出现几十次，若每处都写 size="small" type="primary"，
 *   调整按钮规格就要改几十个文件；包装一次、处处继承。
 */
export { default as AppButton } from "./AppButton.vue";

/**
 * 应用输入框：包住 el-input-number，统一宽度与字号缩放。
 *
 * 数值输入用 el-input-number 而非 el-input：它自带 min/max/step 约束与
 * 步进按钮，手写 <input type="number"> 常常漏掉边界校验。
 */
export { default as AppNumberInput } from "./AppNumberInput.vue";

/**
 * 应用下拉选择：包住 el-select，统一尺寸。
 */
export { default as AppSelect } from "./AppSelect.vue";

/**
 * 应用复选框：包住 el-checkbox，统一尺寸与无障碍细节。
 */
export { default as AppCheckbox } from "./AppCheckbox.vue";

/**
 * 应用标签：包住 el-tag，统一圆角与字号。
 *
 * ⚠️ 涨跌语义色不走这里：红涨绿跌是业务语义（--color-up/--color-down），
 * 与 EP 的 success/danger 含义不同，混用会误导用户（手册 §1.9）。
 * 涨跌标签请继续用项目自己的 .up/.down 类。
 */
export { default as AppTag } from "./AppTag.vue";

/**
 * 应用对话框：包住 el-dialog，统一标题栏、关闭按钮、内边距与字号缩放。
 *
 * 为什么自建遮罩要换成它：焦点陷阱、Esc 关闭、滚动锁、层级管理
 * 都是无障碍细节，自建版本容易漏；EP 已做对。
 */
export { default as AppDialog } from "./AppDialog.vue";

/**
 * 应用日期区间选择器：包住 el-date-picker 的 daterange，统一值格式与字号缩放。
 *
 * 为什么不让业务代码直接用 el-date-picker：区间值在不同写法下可能是
 * Date 对象 / 时间戳 / 字符串，散落到各页面极易出现「一处 YYYY-MM-DD、
 * 一处带时分秒」的静默漂移；收敛成 [from, to] 字符串数组后，
 * 业务层只跟 from/to 打交道，EP 换版本也不外溢。
 */
export { default as AppDateRangePicker } from "./AppDateRangePicker.vue";
