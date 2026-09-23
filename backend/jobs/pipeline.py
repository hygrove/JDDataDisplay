# -*- coding: utf-8 -*-
"""模块管线：数据源 -> 清洗 -> 层级 JSON + summary + manifest。

新增模块只需：1) 实现 Source；2) 在 MODULE_REGISTRY 注册配置；3) （可选）自定义 transforms。
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
    """模块配置（配置驱动扩展的入口）。"""

    module_id: str
    title: str
    description: str
    sources: list[Source] = field(default_factory=list)
    top_n: int = config.SUMMARY_TOP_N


def _load_image_map() -> dict[str, str]:
    """把数据源里的 SPU 图片拷贝到 data/images，生成多尺寸缩略图，返回 spu -> URL 路径。"""
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    src_dir = config.POP_SOURCE_DIR / config.POP_IMAGE_DIR_NAME
    image_map: dict[str, str] = {}
    if src_dir.is_dir():
        for img in src_dir.glob("*.png"):
            dst = config.IMAGES_DIR / img.name
            if not dst.exists() or dst.stat().st_mtime < img.stat().st_mtime:
                shutil.copy2(img, dst)
            image_map[img.stem] = f"/images/{img.name}"
        # 生成缩略图（AVIF 优先 + WebP 兜底），随批处理幂等重算
        n = build_thumbnails(src_dir, config.THUMBS_DIR)
        print(f"[pipeline] 生成缩略图 {n} 个 -> {config.THUMBS_DIR}")
    return image_map


def run_module(spec: ModuleSpec, image_map: dict[str, str]) -> ModuleManifest:
    """执行单个模块的批处理，返回其 manifest 条目。"""
    frames = [s.fetch() for s in spec.sources]
    df = pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
    # 多源 JOIN 场景（如 Excel + DB 推广数据）可在此按 (shop, date, spu) 关联

    module_data = transforms.build_module_data(spec.module_id, df, image_map)
    summary = transforms.build_summary(spec.module_id, df, image_map, spec.top_n)

    config.MODULES_DIR.mkdir(parents=True, exist_ok=True)
    (config.MODULES_DIR / f"{spec.module_id}.json").write_text(
        module_data.model_dump_json(), encoding="utf-8"
    )
    (config.MODULES_DIR / f"{spec.module_id}.summary.json").write_text(
        summary.model_dump_json(), encoding="utf-8"
    )

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
    """当前启用的模块列表。新增模块在这里追加即可。"""
    return [
        ModuleSpec(
            module_id="pop_spu_detail",
            title="POP 单品明细",
            description="按店铺/SPU/日期的商品经营指标：访客、成交、转化、推广。",
            sources=[PopSpuExcelSource()],
        ),
    ]


def run_all() -> Manifest:
    """跑全部模块，写 manifest.json。"""
    image_map = _load_image_map()
    entries = [run_module(spec, image_map) for spec in default_registry()]
    manifest = Manifest(generated_at=datetime.now(timezone.utc), modules=entries)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    return manifest
