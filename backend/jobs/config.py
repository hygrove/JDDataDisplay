# -*- coding: utf-8 -*-
"""批处理全局配置。所有路径/开关都支持环境变量覆盖，方便部署时调整。"""
from __future__ import annotations

import os
from pathlib import Path

# ---- 路径 ----
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("JD_DATA_DIR", BACKEND_DIR / "app" / "data"))
MODULES_DIR = DATA_DIR / "modules"
IMAGES_DIR = DATA_DIR / "images"
THUMBS_DIR = IMAGES_DIR / "thumbs"  # 多尺寸缩略图（AVIF+WebP），由 make_thumbs 生成

# POP 单品明细数据源根目录（项目内 ResourceData；其下 数据源表目录 放各店铺明细/推广表）
POP_SOURCE_DIR = Path(os.getenv("POP_SOURCE_DIR", BACKEND_DIR.parent / "ResourceData"))
# 数据源表目录（固定文件夹，不再按最新日期文件夹遍历，保证稳定性）
POP_TABLE_DIR_NAME = "数据源表目录"
# SPU 图片目录（数据源内）
POP_IMAGE_DIR_NAME = "单品spu图片"

# PostgreSQL 连接串（数据库源示例用；留空则跳过 DB 源）
PG_DSN = os.getenv("PG_DSN", "")

# ---- 重试机制（方案要求：功能保留，默认不启用）----
RETRY_ENABLED = os.getenv("BATCH_RETRY_ENABLED", "false").lower() == "true"
RETRY_MAX_ATTEMPTS = int(os.getenv("BATCH_RETRY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY = float(os.getenv("BATCH_RETRY_BASE_DELAY", "2.0"))  # 秒，指数退避基数

# ---- 批处理参数 ----
SUMMARY_TOP_N = 10  # summary 中 TOP SPU 数量
