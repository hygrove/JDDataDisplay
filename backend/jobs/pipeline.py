# -*- coding: utf-8 -*-
"""批处理产物层：SPU 图片同步与模块注册表（ADR-0012）。

POP 明细数据链路已改为「API 直查 SQLite」（见 backend/app/api.py 与
docs/adr/0012-pop-db-direct-api.md）：批处理不再生成 modules/*.json 与
manifest.json，本模块只剩两件事：
  1. sync_images()：把数据源 SPU 原图同步到 DATA_DIR/images，并幂等生成
     多尺寸缩略图（96/120/180 的 AVIF + WebP）；
  2. default_registry()：模块元数据注册表（id / 标题 / 说明），供 API 的
     /api/manifest 与批处理状态消息共用，是模块清单的唯一登记处。

扩展方式（新增模块）：
  1) 在 default_registry() 里登记一条 ModuleSpec（元数据）；
  2) 数据查询侧按新模块实现（API 直查 DB）；图片同步无需改动。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from . import config
from .make_thumbs import build_thumbnails


@dataclass
class ModuleSpec:
    """单个模块的元数据登记（模块清单的唯一事实来源）。

    Attributes:
        module_id: 模块唯一标识（前端路由参数，如 "pop_spu_detail"）。
        title: 模块中文名，展示给使用方。
        description: 模块说明，进入 manifest 供前端展示。

    Example:
        >>> spec = ModuleSpec(module_id="pop_spu_detail", title="POP 单品明细", description="...")
        >>> spec.module_id
        'pop_spu_detail'
    """

    module_id: str
    title: str
    description: str


def sync_images() -> None:
    """把数据源 SPU 图片同步到 DATA_DIR/images，并幂等生成多尺寸缩略图。

    只做同步（增量拷贝 + 缩略图编码），不返回映射——JSON 产物已废除（ADR-0012），
    「SPU 号 -> URL」映射由 API 侧按需扫描 IMAGES_DIR 构建（api._image_map）。

    Side Effects:
        写文件到 config.IMAGES_DIR 与 config.THUMBS_DIR；打印缩略图生成数量。
        数据源图片目录不存在时什么都不做（首次部署尚未拷图的正常状态）。

    Example:
        >>> sync_images()   # doctest: +SKIP
    """
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = config.POP_SOURCE_DIR / config.POP_IMAGE_DIR_NAME
    if not src_dir.is_dir():
        return
    for img in src_dir.glob("*.png"):
        dst = config.IMAGES_DIR / img.name
        # mtime 比较实现「增量拷贝」：源图没变就不重复复制，批处理可反复运行
        if not dst.exists() or dst.stat().st_mtime < img.stat().st_mtime:
            shutil.copy2(img, dst)
    # 缩略图随批处理幂等重算：只有缺失或过期才真正编码
    n = build_thumbnails(src_dir, config.THUMBS_DIR)
    print(f"[pipeline] 生成缩略图 {n} 个 -> {config.THUMBS_DIR}")


def default_registry() -> list[ModuleSpec]:
    """当前启用的模块清单。新增模块在这里登记一条即可，无需改动其它代码。

    Returns:
        list[ModuleSpec]: 模块元数据列表（当前仅 pop_spu_detail 一个模块）。

    Example:
        >>> [s.module_id for s in default_registry()]
        ['pop_spu_detail']
    """
    return [
        ModuleSpec(
            module_id="pop_spu_detail",
            title="POP 单品明细",
            description="按店铺/SPU/日期的商品经营指标：访客、成交、转化、推广。",
        ),
    ]
