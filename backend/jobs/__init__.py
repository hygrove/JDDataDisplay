# -*- coding: utf-8 -*-
"""批处理包：读源数据 -> 清洗规整 -> 产出层级 JSON / 汇总 JSON / manifest。

子模块职责：
  config      全局路径与开关常量（支持环境变量覆盖）
  models      Pydantic 数据模型（产出 JSON 的 schema，需与前端 types.ts 对齐）
  sources     数据源实现（excel_source 读 Excel/CSV，db_source 为数据库占位骨架）
  transforms  Polars 清洗与聚合（指标口径的后端事实来源）
  pipeline    模块管线编排（拉数 -> 转换 -> 落盘 -> manifest）
  make_thumbs SPU 图片多尺寸缩略图生成（AVIF + WebP）
  run_batch   批处理入口脚本（供计划任务 / cron 调用）

入口：
  python -m backend.jobs.run_batch
"""
