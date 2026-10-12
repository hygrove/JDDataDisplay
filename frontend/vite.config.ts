import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // ⚠️ 代理目标必须写 `127.0.0.1` 而不是 `localhost`。
    //
    // 原因（实测）：本机 `dns.lookup('localhost')` 返回 **[::1(IPv6), 127.0.0.1(IPv4)]**，
    // Node 20+ 默认 verbatim 解析**不回落**，会先试 `::1`；
    // 而 uvicorn 只监听 `0.0.0.0`（**纯 IPv4**，见 `uvicorn --host` 默认值），
    // 于是 `::1:8000` 直接 ECONNREFUSED —— 表现为**整站代理 502**（/api 与 /images 全挂），
    // 但 `curl http://localhost:8000` 却正常（curl 会在 IPv6 失败后回落 IPv4，掩盖了这个差异）。
    //
    // ⚠️ 同理 `npm run dev` 的 Local 地址也是 `[::1]:5173`，浏览器同机访问不受影响。
    //部署到别的机器/端口时用环境变量 VITE_PROXY_TARGET 覆盖。
    proxy: {
      "/api": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
      "/images": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
