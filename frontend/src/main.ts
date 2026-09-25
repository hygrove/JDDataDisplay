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
import "./style.css";

/**
 * 创建并挂载整个应用。
 *
 * @returns {void} 无返回值，副作用是把应用挂在 DOM 的 #app 节点上。
 * @throws {Error} 当 #app 挂载点不存在时，Vue 会在内部告警并抛出挂载失败错误。
 * @example
 * // 由 index.html 引入本模块后自动执行，无需手动调用：
 * // <script type="module" src="/src/main.ts"></script>
 */
createApp(App).use(createPinia()).use(router).mount("#app");
