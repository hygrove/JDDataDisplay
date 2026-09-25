# -*- coding: utf-8 -*-
"""FastAPI 应用入口：单端口同时托管「前端页面 + 数据 API + SPU 图片 + 缩略图」。

启动方式（仓库根目录执行）：
  uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

开发模式说明：
  前端用 vite dev server（端口 5173），通过 vite 的 proxy 把 /api 与 /images 转发到 8000，
  因此这里额外放开了 5173 的 CORS；生产是同源部署，CORS 中间件不会生效也无害。

⚠️ 挂载顺序是硬约束：
  SPA 回退路由 `/{full_path:path}` 会吞掉**一切**未匹配的 GET 请求，
  所以 `/images`、`/thumbs`、`/assets` 这些静态挂载**必须**写在它之前，
  否则图片请求会被回退成 index.html（表现为「页面能开、图片全 404」）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..jobs import config
from .api import router as api_router

# FRONTEND_DIST：前端 npm run build 的产物目录，生产模式下页面全部由这里提供
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="JD 数据平台", version="0.1.0")

# CORS：仅开发期 vite dev server（5173）需要；生产同源，保留不影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务 API 路由（/api/manifest、/api/module/...、/api/refresh 等）
app.include_router(api_router)

# SPU 原图：目录可能尚不存在（首次跑批处理前），先建目录避免 StaticFiles 启动即报错
config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=config.IMAGES_DIR), name="images")

# 缩略图：多尺寸（96/120/180）AVIF + WebP，由批处理 make_thumbs 生成；
# 前端按展示框实际尺寸引用对应目录，避免为了 96px 的框去下载 MB 级原图
config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/thumbs", StaticFiles(directory=config.THUMBS_DIR), name="thumbs")

# 前端静态资源与 SPA 回退：仅在 dist 存在时挂载，
# 否则纯后端启动（如只跑 API 调试）不会因为找不到 dist 而崩
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """SPA history 模式路由回退：非静态资源的路径一律返回 index.html。

        Vue Router 用的是 history 模式，直接访问 /module/xxx 这类前端路由时，
        后端并没有对应的物理文件，必须回退到 index.html 交给前端路由接管，
        否则刷新页面会 404。

        Args:
            full_path: 请求的路径（不含开头的斜杠语义，由 FastAPI path 转换器给出）；
                可能为空（访问根路径 /）。

        Returns:
            FileResponse: 若该路径在 dist 下确实存在同名文件（如 favicon.ico）则直接返回该文件，
                否则返回 index.html 交给前端路由。

        Example:
            GET /module/pop_spu_detail  ->  dist/index.html（前端路由接管）
            GET /favicon.ico            ->  dist/favicon.ico（真实文件直接返回）
        """
        target = FRONTEND_DIST / full_path
        # 真实存在的文件优先直接返回，避免把静态小文件也回退成 HTML
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
