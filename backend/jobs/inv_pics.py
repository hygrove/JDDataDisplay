# -*- coding: utf-8 -*-
"""库存 SKU 商品图：图片索引 + 缩略图构建。

背景（为什么要有这个模块）：
  库存矩阵表第一列要按 **SKU** 展示商品图。图片放在数据源目录
  `ResourceData/inventory_sku_pic/<业务线>/<SKU>.jpg`——文件名主干就是 SKU，
  与 `inventory_daily.sku` 逐字符相等（实测 388 个库存 SKU 中 379 个命中，97.7%）。

  ⚠️ 与 POP 单品明细模块的分工（两条链路彼此独立，别混）：
    - POP 单品明细：按 **SPU** 取图，源图在 `ResourceData/单品spu图片/`，
      缩略图在 `data/images/thumbs/{96,120,180}/{spu}.*`，由 pipeline 负责同步与生成；
    - 库存矩阵表（本模块）：按 **SKU** 取图，源图在 `ResourceData/inventory_sku_pic/`，
      缩略图在 `data/images/thumbs/inv/<业务线>/{120,240}/{sku}.*`。
    本模块**不触碰** POP 那条链路的任何代码与产物。

职责：
  1. build_index()：扫描图片目录的**一层**子目录，建立「SKU → 图片」的内存索引；
  2. get_index()：带缓存的索引访问，用目录 mtime 做失效判定——
     运营把新图丢进目录后无需重启服务即可生效；
  3. pic_path(sku)：给 API 层用，返回前端可访问的路径（如 "/inv-pics/jpzy/100080186911.jpg"）；
  4. build_inv_thumbs()：按业务线子目录生成 120/240 两档缩略图（幂等，复用 make_thumbs）。

为什么由后端裁决图片路径，而不是前端自己拼：
  前端若自己拼，就得在 4 个业务线子目录里逐个试探，缺失的 SKU 会产生 4 次 404 噪音；
  后端建好索引后随 `/api/inventory/rows` 的行一起下发，前端只管渲染，
  「有没有图」在数据层就已经确定，缺图行直接不带该字段。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

# PIC_URL_PREFIX：原图的访问前缀，由 FastAPI 静态挂载托管（见 backend/app/main.py）。
# 只挂 inventory_sku_pic 这一个子目录，不暴露整个 ResourceData。
PIC_URL_PREFIX = "/inv-pics"

# _IMAGE_EXTS：目录里可能出现非图片文件（说明文档、临时文件等），只认这些后缀
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# THUMB_SIZES：库存 SKU 图的缩略图档位。
#   只出 120 一档：商品图本身是 1:1 正方形（源图 800×800），矩阵表第一列的展示框
#   也做成 1:1 的 120×120，缩略图按 1:1 呈现（像素对像素、不缩放）——这是本模块的口径。
#   不出 96/180（库存模块没有那两个展示位），也不出 240 的 2x 档
#   （DPR 高只是让浏览器用更多物理像素画同一块区域，对 1:1 呈现没有帮助，反而多一倍流量）。
THUMB_SIZES: tuple[int, ...] = (120,)


@dataclass(frozen=True)
class SkuPic:
    """一张库存 SKU 商品图的位置信息。"""

    # sub：业务线子目录名（jpzy / kqzy / ybzy / pop）
    sub: str
    # name：文件名（含扩展名）
    name: str

    @property
    def sku(self) -> str:
        """SKU（= 文件名主干）。"""
        return Path(self.name).stem

    @property
    def url(self) -> str:
        """前端可访问的原图路径（形如 "/inv-pics/jpzy/100080186911.jpg"）。"""
        return f"{PIC_URL_PREFIX}/{self.sub}/{self.name}"


# 索引缓存：_index_cache 为 None 表示还没建过；_index_stamp 是建索引时的目录指纹
_index_cache: dict[str, SkuPic] | None = None
_index_stamp: tuple[int, ...] = ()
_index_lock = threading.Lock()


def build_index(root: Path | None = None) -> dict[str, SkuPic]:
    """扫描图片目录，建立「SKU → SkuPic」索引。

    只扫描**一层**子目录（业务线），不递归：目录约定就是「一级业务线 / SKU 文件名」，
    递归反而可能把临时目录、备份目录里的图也收进来。

    同一 SKU 在多个业务线同时出现时，按业务线名排序取第一个并打印告警——
    结果必须确定可复现，否则同一行在不同请求里会随机换图。

    Args:
        root: 图片根目录；None 表示用 config.INV_PIC_DIR。目录不存在时返回空索引。

    Returns:
        dict[str, SkuPic]: key 为 SKU，value 为图片位置信息。

    Example:
        >>> idx = build_index(Path("ResourceData/inventory_sku_pic"))
        >>> len(idx) >= 0
        True
    """
    root = Path(root) if root is not None else config.INV_PIC_DIR
    index: dict[str, SkuPic] = {}
    if not root.is_dir():
        return index
    for sub_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        sub = sub_dir.name
        for f in sorted(sub_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _IMAGE_EXTS:
                continue
            sku = f.stem
            if not sku:
                continue
            if sku in index:
                print(f"[inv_pics] 警告：SKU {sku} 同时存在于 {index[sku].sub} 与 {sub}，沿用 {index[sku].sub}")
                continue
            index[sku] = SkuPic(sub=sub, name=f.name)
    return index


def _fingerprint(root: Path) -> tuple[int, ...]:
    """图片目录的变更指纹（根目录 + 各业务线子目录的 mtime）。

    为什么够用：
      运营把新图丢进某个业务线目录 → 该子目录 mtime 变化 → 指纹变化 → 索引重建；
      图片被就地覆盖（同文件名换内容）→ 路径不变，StaticFiles 的 ETag 会变，
      浏览器照样能拿到新图，所以不需要为它重建索引。

    Args:
        root: 图片根目录。

    Returns:
        tuple[int, ...]: 指纹；目录不存在时返回空元组。
    """
    if not root.is_dir():
        return ()
    parts: list[int] = [root.stat().st_mtime_ns]
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            parts.append(sub.stat().st_mtime_ns)
        except OSError:  # 目录在扫描过程中被删/改名，跳过即可
            continue
    return tuple(parts)


def get_index(root: Path | None = None) -> dict[str, SkuPic]:
    """取图片索引（带缓存 + 目录指纹失效重建）。

    @remarks
    缓存的意义：/rows 每次请求都要给当页行挂图片路径，逐请求全量扫 400+ 文件
    纯属浪费；而运营丢新图进目录后必须能自动生效，所以用目录 mtime 做失效判定，
    做到「不重启服务也能用上新图」。

    Args:
        root: 图片根目录；None 表示用 config.INV_PIC_DIR。

    Returns:
        dict[str, SkuPic]: key 为 SKU，value 为图片位置信息（**只读**，调用方不要修改）。
    """
    global _index_cache, _index_stamp
    root = Path(root) if root is not None else config.INV_PIC_DIR
    with _index_lock:
        stamp = _fingerprint(root)
        if _index_cache is None or stamp != _index_stamp:
            _index_cache = build_index(root)
            _index_stamp = stamp
            print(f"[inv_pics] 图片索引已重建：{len(_index_cache)} 条")
        return _index_cache


def pic_path(sku: str | None) -> str | None:
    """查某个 SKU 的原图访问路径。

    Args:
        sku: 库存 SKU（= 图片文件名主干）；空值返回 None。

    Returns:
        str | None: 形如 "/inv-pics/jpzy/100080186911.jpg" 的访问路径；该 SKU 无图返回 None。

    Example:
        >>> pic_path("") is None
        True
    """
    if not sku:
        return None
    pic = get_index().get(sku)
    return pic.url if pic else None


def build_inv_thumbs(
    root: Path | None = None,
    sizes: tuple[int, ...] = THUMB_SIZES,
) -> int:
    """为库存 SKU 商品图生成缩略图（120 / 240 两档，AVIF + WebP）。

    产物落在 `config.INV_THUMBS_DIR/<业务线>/<尺寸>/<SKU>.<格式>`，
    即 `data/images/thumbs/inv/jpzy/120/100080186911.avif`，
    被既有的 `/thumbs` 静态挂载直接托管（前端无需新增 proxy 前缀）。

    幂等：沿用 make_thumbs 的判定——产物缺失或早于源图 mtime 才重新编码，
    因此日常批处理近乎零开销，首次全量约 400 张 × 2 档 × 2 格式（AVIF 编码耗时，分钟级）。

    Args:
        root: 图片根目录；None 表示用 config.INV_PIC_DIR。
        sizes: 缩略图尺寸档位；默认 (120, 240)。

    Returns:
        int: 本次实际生成的缩略图文件数（全部已存在时为 0）。
    """
    from .make_thumbs import build_thumbnails  # 延迟导入：避免 import 期就拉起 Pillow

    root = Path(root) if root is not None else config.INV_PIC_DIR
    if not root.is_dir():
        return 0
    total = 0
    for sub_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        n = build_thumbnails(sub_dir, config.INV_THUMBS_DIR / sub_dir.name, sizes=sizes)
        total += n
    return total


def _main() -> int:
    """命令行入口：单独生成库存 SKU 缩略图（首次部署 / 预览用）。

    用法：
        .venv/Scripts/python.exe -m backend.jobs.inv_pics
    """
    root = config.INV_PIC_DIR
    index = build_index(root)
    print(f"[inv_pics] 图片目录：{root}")
    print(f"[inv_pics] 索引条目：{len(index)}")
    n = build_inv_thumbs(root)
    print(f"[inv_pics] 新生成缩略图文件：{n}（目录：{config.INV_THUMBS_DIR}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
