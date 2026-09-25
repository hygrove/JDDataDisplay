# -*- coding: utf-8 -*-
"""模块批处理管线：数据源 -> 规整 -> 层级 JSON + summary + manifest。

整体流程：
  1. _load_image_map()：把数据源里的 SPU 图片同步到 data/images 并生成多尺寸缩略图；
  2. run_module(spec, image_map)：对每个模块拉取各 Source 的长表、拼接、转换，
     产出 {module_id}.json（层级明细）与 {module_id}.summary.json（预聚合汇总）；
  3. run_all()：跑完全部模块后写出 manifest.json（模块清单，前端入口第一跳）。

扩展方式（配置驱动，改这里即可，不用改下游）：
  1) 实现 Source 协议（见 sources/base.py）；
  2) 在 default_registry() 里追加一条 ModuleSpec；
  3) 可选：在 transforms 里为该模块定制转换逻辑。
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from . import config, transforms
from .make_thumbs import build_thumbnails
from .models import Manifest, ModuleManifest
from .sources.base import Source
from .sources.excel_source import PopSpuExcelSource


@dataclass
class ModuleSpec:
    """单个模块的批处理配置（配置驱动扩展的入口）。

    Attributes:
        module_id: 模块唯一标识，同时决定产出文件名（如 pop_spu_detail.json）。
        title: 模块中文名，展示给使用方。
        description: 模块说明，进入 manifest 供前端展示。
        sources: 该模块的数据源列表，每项需满足 Source 协议；可多个源拼接。
        top_n: summary 里 TOP SPU 榜单保留条数，默认取 config.SUMMARY_TOP_N。

    Example:
        >>> spec = ModuleSpec(
        ...     module_id="pop_spu_detail",
        ...     title="POP 单品明细",
        ...     description="按店铺/SPU/日期的商品经营指标",
        ...     sources=[PopSpuExcelSource()],
        ... )
        >>> spec.module_id
        'pop_spu_detail'
    """

    module_id: str
    title: str
    description: str
    sources: list[Source] = field(default_factory=list)
    top_n: int = config.SUMMARY_TOP_N


def _load_image_map() -> dict[str, str]:
    """同步 SPU 图片并生成多尺寸缩略图，返回「SPU 号 -> 图片访问路径」映射。

    做两件事：
      1. 把数据源目录里的 PNG 拷贝到 DATA_DIR/images（仅在目标不存在或比源图旧时拷贝）；
      2. 调用 build_thumbnails 生成 96/120/180 的 AVIF + WebP 缩略图（幂等）。

    Returns:
        dict[str, str]: key 为图片文件名主干（即 SPU 号），value 为前端可访问的 URL 路径
            （形如 "/images/100xxxx.png"）。数据源目录不存在时返回空 dict。

    Side Effects:
        写文件到 config.IMAGES_DIR 与 config.THUMBS_DIR；打印缩略图生成数量。

    Example:
        >>> image_map = _load_image_map()
        >>> list(image_map)[:1]
        ['100123456']
    """
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = config.POP_SOURCE_DIR / config.POP_IMAGE_DIR_NAME
    image_map: dict[str, str] = {}
    if src_dir.is_dir():
        for img in src_dir.glob("*.png"):
            dst = config.IMAGES_DIR / img.name
            # mtime 比较实现「增量拷贝」：源图没变就不重复复制，批处理可反复运行
            if not dst.exists() or dst.stat().st_mtime < img.stat().st_mtime:
                shutil.copy2(img, dst)
            # stem 即 SPU 号：数据源约定图片以 SPU 号命名，据此与明细数据关联
            image_map[img.stem] = f"/images/{img.name}"
        # 缩略图随批处理幂等重算：只有缺失或过期才真正编码
        n = build_thumbnails(src_dir, config.THUMBS_DIR)
        print(f"[pipeline] 生成缩略图 {n} 个 -> {config.THUMBS_DIR}")
    return image_map


def run_module(spec: ModuleSpec, image_map: dict[str, str]) -> ModuleManifest:
    """执行单个模块的批处理：拉数 -> 转换 -> 落盘两个 JSON -> 返回 manifest 条目。

    Args:
        spec: 模块配置，决定用哪些数据源、产出什么文件名、TOP N 取多少。
        image_map: 「SPU 号 -> 图片 URL」映射，由 _load_image_map() 生成，
            交给 transforms 填充到每个 SPU 节点上（图片缺失时降级为空）。

    Returns:
        ModuleManifest: 该模块的清单条目，含店铺列表、日期区间、SPU 数、行数、更新时间。

    Raises:
        Exception: 数据源 fetch 失败（如 Excel 缺失/损坏）、 transforms 转换失败，
            或磁盘不可写时由底层抛出；调用方 run_all() 不捕获，由 run_batch 统一兜底。

    Example:
        >>> spec = default_registry()[0]
        >>> entry = run_module(spec, {})
        >>> entry.module_id
        'pop_spu_detail'
    """
    # 依次拉取各数据源；多个源时用 diagonal_relaxed 纵向拼接，
    # 该模式允许各源列集合不同（缺失列自动补 null），正好适配「Excel + 数据库推广数据」的场景
    frames = [s.fetch() for s in spec.sources]
    df = pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
    # 多源 JOIN 场景（如 Excel + DB 推广数据）可在此按 (shop, date, spu) 关联

    # 层级明细（店铺->SPU->日期->指标）与预聚合汇总（图表用）分别产出，
    # 拆开是为了让「明细翻页」与「图表汇总」各取所需、互不拖累
    module_data = transforms.build_module_data(spec.module_id, df, image_map)
    summary = transforms.build_summary(spec.module_id, df, image_map, spec.top_n)

    config.MODULES_DIR.mkdir(parents=True, exist_ok=True)
    (config.MODULES_DIR / f"{spec.module_id}.json").write_text(
        module_data.model_dump_json(), encoding="utf-8"
    )
    (config.MODULES_DIR / f"{spec.module_id}.summary.json").write_text(
        summary.model_dump_json(), encoding="utf-8"
    )

    # SPU 数按 (shop, spu) 去重统计：同一个 SPU 可能跨店存在，不能只按 spu 去重
    spu_count = df.select(["shop", "spu"]).unique().height
    return ModuleManifest(
        module_id=spec.module_id,
        title=spec.title,
        description=spec.description,
        shops=sorted(df["shop"].unique().to_list()),
        date_range=module_data.date_range,
        spu_count=spu_count,
        row_count=df.height,
        updated_at=module_data.updated_at,
    )


def default_registry() -> list[ModuleSpec]:
    """当前启用的模块清单。新增模块在这里追加一条即可，无需改动其它代码。

    Returns:
        list[ModuleSpec]: 模块配置列表（当前仅 pop_spu_detail 一个模块）。

    Example:
        >>> [s.module_id for s in default_registry()]
        ['pop_spu_detail']
    """
    return [
        ModuleSpec(
            module_id="pop_spu_detail",
            title="POP 单品明细",
            description="按店铺/SPU/日期的商品经营指标：访客、成交、转化、推广。",
            sources=[PopSpuExcelSource()],
        ),
    ]


def run_all() -> Manifest:
    """跑完全部模块并写出 manifest.json。

    Returns:
        Manifest: 全局清单，含生成时间与各模块的 ModuleManifest 条目。

    Raises:
        Exception: 任一模块处理失败时向上抛出（run_batch.run_once 会捕获并记入状态）。

    Side Effects:
        写出 DATA_DIR/manifest.json，以及每个模块的 .json / .summary.json。

    Example:
        >>> manifest = run_all()
        >>> [m.module_id for m in manifest.modules]
        ['pop_spu_detail']
    """
    image_map = _load_image_map()
    entries = [run_module(spec, image_map) for spec in default_registry()]
    manifest = Manifest(generated_at=datetime.now(timezone.utc), modules=entries)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    return manifest
