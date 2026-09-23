import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 代理目标默认本地 8000；部署到别的机器/端口时用环境变量 VITE_PROXY_TARGET 覆盖
    proxy: {
      "/api": process.env.VITE_PROXY_TARGET || "http://localhost:8000",
      "/images": process.env.VITE_PROXY_TARGET || "http://localhost:8000",
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
