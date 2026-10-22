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
    // ⚠️ 后端挂载的每个前端可见静态前缀都必须在这里列举：
    // /images 原图、/thumbs 缩略图（前端 thumb() 改造后图片全走 /thumbs/{96|120|180}）、
    // /inv-pics 库存 SKU 商品原图（库存矩阵表第一列点击放大用；其缩略图在
    // /thumbs/inv/<业务线>/<尺寸>/<SKU> 下，已被 /thumbs 覆盖，无需另列）。
    // 漏列 /thumbs 的后果：dev 下缩略图请求由 Vite 接单 → 404 → 图片全裂（生产正常，极具迷惑性）。
    proxy: {
      "/api": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
      "/images": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
      "/thumbs": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
      "/inv-pics": process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000",
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
