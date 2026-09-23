<script setup lang="ts">
// 整体布局：左侧导航（模块 + 店铺切换），右侧主业务区。
import { computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useModuleStore } from "./stores/module";
import { fetchManifest } from "./api";

const store = useModuleStore();
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

// 店铺列表：从路由当前模块推导，不依赖 store.moduleId
// （单品分析页不调用 store.init，store.moduleId 为空会导致店铺列表消失）
const shops = computed(
  () => store.manifest?.modules.find((m) => m.module_id === route.params.moduleId)?.shops ?? [],
);

// 当前选中店铺：分析页以路由 query.shop 为准，列表页以 store.shop 为准
const activeShop = computed(() =>
  route.name === "spu-analysis" ? ((route.query.shop as string) || "") : store.shop,
);

// 点击店铺：列表页直接切换店铺筛选；分析页则离开分析回到商品明细页（模块列表）
function onShopClick(shop: string) {
  if (route.name === "spu-analysis") {
    void router.push({
      name: "module",
      params: { moduleId: route.params.moduleId as string },
      query: shop ? { shop } : {},
    });
  } else {
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

      <img class="sider-watermark" src="/undraw_all-the-data_ijgn.svg" alt="" />
    </aside>

    <main class="main">
      <router-view />
    </main>
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
}
</style>
