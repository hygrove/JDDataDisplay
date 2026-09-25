# -*- coding: utf-8 -*-
"""FastAPI 应用包：对外提供数据 API 与前端页面托管。

子模块职责：
  main     FastAPI 入口（挂载 API 路由、静态资源、SPA 回退）
  api      数据接口（manifest / 模块明细分页 / 汇总 / 单品分析 / 状态 / 刷新）
  analysis 单品分析的业务逻辑（节假日判定、分组对比、分析文案生成）

启动：
  uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
"""
