import { createRouter, createWebHistory } from "vue-router";
import { useUiStore } from "./stores/ui";

// 视图懒加载：首屏不加载非必要模块代码。
// 尤其 SpuAnalysisView 依赖 echarts（体积大），必须懒加载，否则会拖慢首屏。
const ModuleView = () => import("./views/ModuleView.vue");
const SpuAnalysisView = () => import("./views/SpuAnalysisView.vue");

// 首页空白占位（App.vue 会在拉到 manifest 后自动跳转到第一个模块）。
// ⚠️ 必须用 render 函数而不是 template 字符串：生产构建用的是 runtime-only 版本 Vue，
//    不含模板编译器，写 template 会在运行时报 "Component provided template option but
//    runtime compilation is not supported"。
const HomeBlank = { render: () => null };

/**
 * 全局路由实例（history 模式）。
 *
 * @remarks
 * 三条路由：
 * - `/` 首页空白占位，由 App.vue 拉到 manifest 后自动跳转到第一个模块；
 * - `/module/:moduleId` 模块页（SPU 明细）；
 * - `/module/:moduleId/spu/:spu` 单品分析页，可带 `?shop=xxx` 指定店铺。
 *
 * @example
 * ```ts
 * router.push({ name: "module", params: { moduleId: "pop_spu_detail" } });
 * router.push({ name: "spu-analysis", params: { moduleId: "pop_spu_detail", spu: "100123" } });
 * ```
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeBlank },
    {
      path: "/module/:moduleId",
      name: "module",
      component: ModuleView,
      // props: true 让 :moduleId 直接作为 prop 传给组件，组件不必再用 route.params 取值
      props: true,
    },
    {
      // 单品分析页：/module/pop_spu_detail/spu/100xxxx?shop=xxx
      path: "/module/:moduleId/spu/:spu",
      name: "spu-analysis",
      component: SpuAnalysisView,
      props: true,
    },
  ],
});

// 路由级加载遮罩：单品分析页是懒加载大 chunk（含 echarts），点击跳转后要等 chunk
// 下载+解析，这段时间内目标组件还没挂载、自身遮罩不会出现。这里在「跳转一开始」就亮起
// 全屏遮罩，组件挂载后再由 SpuAnalysisView.onMounted 把 navigating 复位（交棒给页面自身遮罩）。
//
// ⚠️ useUiStore() 必须在导航发生的**运行时**调用（此时 Pinia 已装配），
//    不能放在模块顶层——模块顶层执行时机早于 Pinia 安装，会抛「no active Pinia」。
router.beforeEach((to) => {
  // 只对单品分析页点亮：其它导航本来就很快，加遮罩反而会造成闪烁
  if (to.name === "spu-analysis") {
    useUiStore().setNavigating(true);
  }
});

// 兜底复位：路由解析失败（如 chunk 加载失败）时也要收起遮罩，
// 否则遮罩会永久卡住、页面再也无法操作
router.onError(() => {
  useUiStore().setNavigating(false);
});
