# -*- coding: utf-8 -*-
"""批处理全局配置常量。

本模块只放**常量**，不放逻辑：所有路径与开关都支持环境变量覆盖，
便于把项目整体搬到别的目录 / 服务器时无需改代码。

环境变量一览：
  JD_DATA_DIR            批处理产出目录，默认 backend/app/data
  POP_SOURCE_DIR         数据源根目录，默认项目下 ResourceData
  PG_DSN                 PostgreSQL 连接串，留空则跳过数据库源
  BATCH_RETRY_ENABLED    是否启用重试（默认关闭）
  BATCH_RETRY_MAX_ATTEMPTS / BATCH_RETRY_BASE_DELAY  重试次数与退避基数

示例：
    >>> from backend.jobs import config
    >>> str(config.DATA_DIR.name)          # 产出目录名
    'data'
    >>> config.POP_TABLE_DIR_NAME          # 数据源里的固定子目录名
    '数据源表目录'
"""
from __future__ import annotations

import os
from pathlib import Path

# ---- 路径 ----
# BACKEND_DIR：backend/ 目录本身，作为其它默认路径的基准锚点
BACKEND_DIR = Path(__file__).resolve().parent.parent

# DATA_DIR：批处理产出的 JSON / 图片都放这里，由 FastAPI 静态托管后供前端读取
DATA_DIR = Path(os.getenv("JD_DATA_DIR", BACKEND_DIR / "app" / "data"))

# MODULES_DIR：各模块的明细 / 汇总 JSON 存放目录（如 pop_spu_detail.json）
MODULES_DIR = DATA_DIR / "modules"

# IMAGES_DIR：SPU 原图目录，前端 /images 直接指向这里
IMAGES_DIR = DATA_DIR / "images"

# THUMBS_DIR：多尺寸缩略图根目录（AVIF + WebP 双格式），由 make_thumbs.build_thumbnails 幂等生成；
# 之所以单独出一份缩略图，是因为卡片展示只有 96/120/180px，直接下原图（PNG 常 0.9~3MB）会拖慢首屏
THUMBS_DIR = IMAGES_DIR / "thumbs"

# POP_SOURCE_DIR：POP 单品明细的数据源根目录（项目内的 ResourceData）
POP_SOURCE_DIR = Path(os.getenv("POP_SOURCE_DIR", BACKEND_DIR.parent / "ResourceData"))

# POP_TABLE_DIR_NAME：数据源里存放各店铺「明细 xlsx / 推广 csv」的固定子目录名。
# 注意：早期实现会遍历「最新日期文件夹」，导致换一天的数据就换目录、极不稳定；
# 现在刻意改成读固定文件夹，牺牲一点「自动找最新」的便利换取可预期性
POP_TABLE_DIR_NAME = "数据源表目录"

# POP_IMAGE_DIR_NAME：数据源根目录下存放 SPU 图片的目录名
POP_IMAGE_DIR_NAME = "单品spu图片"

# PG_DSN：PostgreSQL 连接串。留空表示不启用数据库源（db_source.PostgresSource 会自动跳过）
PG_DSN = os.getenv("PG_DSN", "")

# ---- 重试机制（功能保留，但默认不启用）----
# RETRY_ENABLED：批处理失败是否自动重试。默认 false，因为上游数据出错时重试多半也是同样的错，
# 不如让失败显式暴露出来由人排查；需要无人值守时再打开
RETRY_ENABLED = os.getenv("BATCH_RETRY_ENABLED", "false").lower() == "true"

# RETRY_MAX_ATTEMPTS：单个任务最多重试次数（含首次执行）
RETRY_MAX_ATTEMPTS = int(os.getenv("BATCH_RETRY_MAX_ATTEMPTS", "3"))

# RETRY_BASE_DELAY：指数退避的基数秒数，实际等待 = BASE_DELAY * 2^(n-1)，避免把下游系统打垮
RETRY_BASE_DELAY = float(os.getenv("BATCH_RETRY_BASE_DELAY", "2.0"))

# ---- 批处理参数 ----
# SUMMARY_TOP_N：模块汇总 summary 里「TOP SPU」榜单保留的条数
SUMMARY_TOP_N = 10
