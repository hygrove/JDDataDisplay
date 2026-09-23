# -*- coding: utf-8 -*-
"""FastAPI 入口：托管前端 dist + 数据 API + SPU 图片。

启动（仓库根目录）：
  uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
开发模式前端用 vite dev server（代理 /api 与 /images 到 8000）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..jobs import config
from .api import router as api_router

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="JD 数据平台", version="0.1.0")

# 开发期 vite dev server 跨域（生产同源，无影响）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# SPU 图片
config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=config.IMAGES_DIR), name="images")

# 缩略图（多尺寸 AVIF+WebP，由批处理生成，前端按展示框尺寸引用以省带宽）
config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/thumbs", StaticFiles(directory=config.THUMBS_DIR), name="thumbs")

# 前端静态资源（生产模式：先 build 前端）
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """SPA history 路由回退到 index.html。"""
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
