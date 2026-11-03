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
// Element Plus：按需引入，只注册 components/ui/ 收敛层实际用到的组件。
// 为什么按需而非全量：全量注册会把整个 EP 打进产物，而本项目只用到
// 按钮 / 标签 / 下拉 / 数字输入 / 复选框等少数几个。
// 收敛边界见 docs/adr/0019-tech-stack-layered-verdict.md 与 components/ui/index.ts。
import {
  ElButton,
  ElCheckbox,
  ElConfigProvider,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElTag,
} from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/checkbox/style/css";
import "element-plus/es/components/config-provider/style/css";
import "element-plus/es/components/input-number/style/css";
import "element-plus/es/components/option/style/css";
import "element-plus/es/components/select/style/css";
import "element-plus/es/components/tag/style/css";

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
  // Element Plus 全局注册：与 components/ui/ 的语义包装一一对应。
  // 漏注册的后果不是构建报错，而是运行时渲染成未知元素（AppNumberInput
  // 曾因漏注册 ElInputNumber 而静默失效）；verify:ep 现在会把这条断言化。
  // 中文语言包必须挂在 ElConfigProvider 上，见 App.vue。
  .component("ElButton", ElButton)
  .component("ElCheckbox", ElCheckbox)
  .component("ElConfigProvider", ElConfigProvider)
  .component("ElInputNumber", ElInputNumber)
  .component("ElOption", ElOption)
  .component("ElSelect", ElSelect)
  .component("ElTag", ElTag)
  .mount("#app");
