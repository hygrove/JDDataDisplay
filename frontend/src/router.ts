import { createRouter, createWebHistory } from "vue-router";

// 模块视图懒加载：首屏不加载全部模块代码
const ModuleView = () => import("./views/ModuleView.vue");
const SpuAnalysisView = () => import("./views/SpuAnalysisView.vue");

// 首页空白占位（App.vue 会在拉到 manifest 后自动跳转到第一个模块）。
// 注意：必须用 render 函数——生产构建是 runtime-only，不支持 template 字符串。
const HomeBlank = { render: () => null };

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeBlank },
    {
      path: "/module/:moduleId",
      name: "module",
      component: ModuleView,
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
