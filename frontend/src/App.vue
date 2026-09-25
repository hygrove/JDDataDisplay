<script setup lang="ts">
// 整体布局：左侧导航（模块 + 店铺切换），右侧主业务区。
import { computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useModuleStore } from "./stores/module";
import { useUiStore } from "./stores/ui";
import { fetchManifest } from "./api";

const store = useModuleStore();
const ui = useUiStore();
const route = useRoute();
const router = useRouter();

onMounted(async () => {
  if (!store.manifest) {
    try {
      store.manifest = await fetchManifest();
      // 首页自动跳到第一个模块
      if (route.path === "/" && store.manifest.modules.length) {
        router.replace(`/module/${store.manifest.modules[0].module_id}`);
      }
    } catch (e) {
      store.error = String(e);
    }
  }
});

/**
 * 侧边栏「店铺」列表。
 *
 * @remarks
 * 刻意**从路由上的 moduleId 查 manifest**，而不是用 store.currentModule：
 * 单品分析页不调用 store.init()，且离开模块页时 store.reset() 会清空 moduleId，
 * 依赖 store 会让分析页的店铺列表凭空消失。按路由自查 manifest 最稳。
 *
 * @returns {string[]} 当前模块的店铺名数组；manifest 未加载或模块不存在时为空数组。
 * @example
 * ```ts
 * shops.value; // ["钻芯旗舰店", "卡求旗舰店"]
 * ```
 */
const shops = computed(
  () => store.manifest?.modules.find((m) => m.module_id === route.params.moduleId)?.shops ?? [],
);

/**
 * 当前高亮的店铺。
 *
 * @remarks
 * 两个页面的「当前店铺」来源不同：
 * - 单品分析页以 URL 上的 query.shop 为准（可分享、可刷新保持）；
 * - 模块列表页以 store.shop（用户筛选状态）为准。
 *
 * @returns {string} 店铺名；空串表示「全部店铺」。
 * @example
 * ```ts
 * activeShop.value; // "" 或 "钻芯旗舰店"
 * ```
 */
const activeShop = computed(() =>
  route.name === "spu-analysis" ? ((route.query.shop as string) || "") : store.shop,
);

/**
 * 点击侧边栏店铺：按当前所在页面走不同行为。
 *
 * @remarks
 * 在单品分析页点店铺时**不能**只改 store.shop——分析页不读 store.shop，
 * 改了也看不到效果。所以这里跳转回模块页并把店铺带在 query 上，行为符合直觉。
 *
 * @param {string} shop - 目标店铺名；传空串表示「全部店铺」。
 * @returns {void} 无返回值。
 * @example
 * ```ts
 * onShopClick("钻芯旗舰店"); // 列表页：筛选该店；分析页：跳回列表页并带上 shop
 * onShopClick("");           // 切回全部店铺
 * ```
 */
function onShopClick(shop: string) {
  if (route.name === "spu-analysis") {
    // 分析页 -> 跳回模块列表页，店铺通过 query 传递（空店铺时不带 shop 参数）
    void router.push({
      name: "module",
      params: { moduleId: route.params.moduleId as string },
      query: shop ? { shop } : {},
    });
  } else {
    // 列表页 -> 直接切换筛选（store 内部会触发重新拉数据）
    store.setShop(shop);
  }
}
</script>

<template>
  <div class="layout">
    <aside class="sider">
      <div class="logo">
        <svg class="logo-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 4-5"/></svg>
        <span>JD 数据平台</span>
      </div>

      <div class="nav-section">
        <div class="nav-title">业务模块</div>
        <router-link
          v-for="m in store.manifest?.modules ?? []"
          :key="m.module_id"
          class="nav-item"
          :class="{ active: route.params.moduleId === m.module_id }"
          :to="`/module/${m.module_id}`"
        >
          {{ m.title }}
        </router-link>
      </div>

      <div v-if="route.params.moduleId" class="nav-section grow">
        <div class="nav-title">店铺</div>
        <a
          class="nav-item"
          :class="{ active: activeShop === '' }"
          @click="onShopClick('')"
        >
          全部店铺
        </a>
        <a
          v-for="s in shops"
          :key="s"
          class="nav-item"
          :class="{ active: activeShop === s }"
          @click="onShopClick(s)"
        >
          {{ s }}
        </a>
      </div>

      <div class="sider-foot">
        <div v-if="store.currentModule" class="meta">
          SPU {{ store.currentModule.spu_count }} · 行 {{ store.currentModule.row_count }}
        </div>
      </div>

      <img class="sider-watermark" src="/navbar-background.svg" alt="" />
    </aside>

    <main class="main">
      <router-view />
    </main>
  </div>

  <!-- 路由级加载遮罩：跳转到单品分析页（懒加载大 chunk）时，路由一开始即亮起，
       覆盖 chunk 下载+解析的等待；组件挂载后由 SpuAnalysisView 复位（交棒页面自身遮罩）。 -->
  <div v-if="ui.navigating" class="nav-loading">
    <div class="nl-spinner"></div>
    <div class="nl-text">{{ ui.loadingText }}</div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  background: #f1f5f9;
}
.sider {
  width: 216px;
  flex: none;
  background: #fff;
  border-right: 1px solid #e5e7eb;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  box-sizing: border-box;
  position: relative;
  overflow: hidden;
}
/* 导航文字在插画水印之上，保证可读 */
.sider > *:not(.sider-watermark) {
  position: relative;
  z-index: 1;
}
/* 右下角插画水印（风格 D）：大尺寸、低透明度铺底，不干扰导航 */
.sider-watermark {
  position: absolute;
  right: -16px;
  bottom: 0;
  width: 184px;
  opacity: 0.2;
  pointer-events: none;
  z-index: 0;
}
/* ---------- 路由级加载遮罩（跳转单品分析页时立即出现）---------- */
.nav-loading {
  position: fixed;
  inset: 0;
  z-index: 300;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(1.5px);
  -webkit-backdrop-filter: blur(1.5px);
}
.nl-spinner {
  width: 44px;
  height: 44px;
  border: 4px solid #f6cfcc;
  border-top-color: #e1251b;
  border-radius: 50%;
  animation: nl-spin 0.8s linear infinite;
}
@keyframes nl-spin {
  to { transform: rotate(360deg); }
}
.nl-text {
  font-size: 14px;
  color: #475569;
  letter-spacing: 0.5px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 17px;
  font-weight: 700;
  color: #e1251b;
  padding: 4px 10px 16px;
}
.logo-ico {
  width: 22px;
  height: 22px;
  flex: none;
}
.nav-section {
  margin-bottom: 18px;
}
.nav-section.grow {
  flex: 1;
  overflow: auto;
}
.nav-title {
  font-size: 12px;
  color: #94a3b8;
  padding: 0 10px 8px;
}
.nav-item {
  display: block;
  padding: 9px 10px;
  border-radius: 6px;
  font-size: 14px;
  color: #334155;
  text-decoration: none;
  cursor: pointer;
  margin-bottom: 2px;
}
.nav-item:hover {
  background: #f8fafc;
}
.nav-item.active {
  background: #fef2f2;
  color: #e1251b;
  font-weight: 600;
  box-shadow: inset 3px 0 0 #e1251b;
}
.sider-foot {
  border-top: 1px solid #f1f5f9;
  padding-top: 10px;
}
.meta {
  font-size: 12px;
  color: #94a3b8;
  padding: 0 10px;
}
.main {
  flex: 1;
  overflow: auto;
  padding: 20px 24px;
  box-sizing: border-box;
  /* 右侧主展示区背景：用前端 public 下的白底图片铺底（构建时原样拷进 dist 根目录，
     运行时以 /white-background.jpg 访问）；左侧 .sider 保持纯白不变。
     center/cover 保证图片居中且铺满整个展示区、按比例缩放不变形。 */
  background: #fff url("/white-background.jpg") center / cover no-repeat;
}
</style>
