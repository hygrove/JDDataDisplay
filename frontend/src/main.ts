// 应用入口：装配 Vue 应用、Pinia 状态、路由后挂载到 #app。
//
// 装配顺序是**有讲究**的：必须先 use(createPinia()) 再 use(router)。
// 原因：router.ts 的全局前置守卫 beforeEach 里会调用 useUiStore() 来点亮加载遮罩，
// 而 useUiStore() 依赖已装配的 Pinia 实例；若 Pinia 晚于 router 安装，
// 首次导航触发守卫时就会报 "getActivePinia was called with no active Pinia"。
import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { router } from "./router";
import { readStoredScale } from "./stores/uiPrefs";
import "./style.css";
// Element Plus：按需引入（本项目只用到少量组件，见下方注册清单）。
// 为什么按需而非全量：全量注册会把整个 EP 打进产物，而本项目实际只用
// 表格 / 分页 / 标签 / 提示 等少数几个，收益远小于体积代价。
// 组件清单见 references/element-plus-integration.md（架构手册 §6.2 单一事实来源）。
import {
  ElTable,
  ElTableColumn,
  ElPagination,
  ElTag,
  ElTooltip,
  ElPopover,
  ElDatePicker,
  ElButton,
  ElSelect,
  ElOption,
  ElIcon,
  ElCheckbox,
  ElConfigProvider,
} from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/table-column/style/css";
import "element-plus/es/components/pagination/style/css";
import "element-plus/es/components/tag/style/css";
import "element-plus/es/components/tooltip/style/css";
import "element-plus/es/components/popover/style/css";
import "element-plus/es/components/date-picker/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/icon/style/css";
import "element-plus/es/components/checkbox/style/css";
import "element-plus/es/components/config-provider/style/css";

// 挂载前把缩放系数写进 CSS 变量：首屏即按用户上次选的档位渲染，
// 避免先按标准档（--font-scale:1）出字、挂载后再跳档导致的闪烁。
document.documentElement.style.setProperty("--font-scale", String(readStoredScale()));

/**
 * 创建并挂载整个应用。
 *
 * @returns {void} 无返回值，副作用是把应用挂在 DOM 的 #app 节点上。
 * @throws {Error} 当 #app 挂载点不存在时，Vue 会在内部告警并抛出挂载失败错误。
 * @example
 * // 由 index.html 引入本模块后自动执行，无需手动调用：
 * // <script type="module" src="/src/main.ts"></script>
 */
createApp(App)
  .use(createPinia())
  .use(router)
  // Element Plus 中文语言包（分页/日期选择器的「月」「日」等文案）。
  // 必须挂在 ElConfigProvider 上，见 App.vue —— 组件是全局注册的，
  // 但 locale 需要由 ElConfigProvider 提供。
  .component("ElTable", ElTable)
  .component("ElTableColumn", ElTableColumn)
  .component("ElPagination", ElPagination)
  .component("ElTag", ElTag)
  .component("ElTooltip", ElTooltip)
  .component("ElPopover", ElPopover)
  .component("ElDatePicker", ElDatePicker)
  .component("ElButton", ElButton)
  .component("ElSelect", ElSelect)
  .component("ElOption", ElOption)
  .component("ElIcon", ElIcon)
  .component("ElCheckbox", ElCheckbox)
  .component("ElConfigProvider", ElConfigProvider)
  .mount("#app");
