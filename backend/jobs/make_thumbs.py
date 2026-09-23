# -*- coding: utf-8 -*-
"""SPU 原图 -> 多尺寸缩略图（AVIF 优先 + WebP 兜底）。

源图较大（如 2000px PNG），而前端展示框只有 96/120/180px，直接下原图纯属浪费带宽。
本模块在批处理阶段为每个 SPU 原图生成「最长边 = 展示框尺寸」的缩略图：
  - AVIF：体积最小、画质好，浏览器优先采用；
  - WebP：给不认识 AVIF 的浏览器兜底。
原图继续保留在 /images 下，详情页需要看大图时仍可访问。

触发：在 pipeline._load_image_map 中调用，随每次批处理幂等重算（源图更新才重新生成）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# 注：Pillow >= 12.0 已原生支持 AVIF 读写（本环境为 12.3.0），无需 pillow-avif-plugin。
# 如降级到旧 Pillow，需额外安装 pillow-avif-plugin 并 import 以注册编解码器。

# 需要生成缩略图的尺寸（对应前端展示框：卡片 180 / 区间行 120 / 详情 hero 96）
THUMB_SIZES = (96, 120, 180)
# (扩展名, Pillow 保存格式, 质量)
THUMB_FORMATS = (
    ("avif", "AVIF", 55),
    ("webp", "WEBP", 80),
)
# 支持的源图格式
_SRC_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _open_rgb(img_path: Path) -> Image.Image:
    """打开图片并转成 RGB（透明区域填白，避免转 AVIF/WebP 后变黑）。"""
    im = Image.open(img_path)
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def build_thumbnails(src_dir: Path, thumbs_root: Path) -> int:
    """为 src_dir 下所有图片生成多尺寸 AVIF+WebP 缩略图，返回生成的文件数。

    幂等：仅当缩略图不存在或比源图旧时才重新生成。
    """
    if not src_dir.is_dir():
        return 0
    for size in THUMB_SIZES:
        (thumbs_root / str(size)).mkdir(parents=True, exist_ok=True)

    total = 0
    for src in sorted(src_dir.iterdir()):
        if not src.is_file() or src.suffix.lower() not in _SRC_EXTS:
            continue
        # 判断是否需要重算
        need = False
        for size in THUMB_SIZES:
            for ext, _, _ in THUMB_FORMATS:
                out = thumbs_root / str(size) / f"{src.stem}.{ext}"
                if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
                    need = True
                    break
            if need:
                break
        if not need:
            continue

        try:
            im = _open_rgb(src)
        except Exception as e:  # 单张坏图不应中断整批
            print(f"[make_thumbs] 跳过无法解码的源图 {src.name}: {e}")
            continue

        for size in THUMB_SIZES:
            thumb = im.copy()
            thumb.thumbnail((size, size), Image.LANCZOS)
            for ext, fmt, quality in THUMB_FORMATS:
                out = thumbs_root / str(size) / f"{src.stem}.{ext}"
                try:
                    thumb.save(out, fmt, quality=quality)
                    total += 1
                except Exception as e:
                    print(f"[make_thumbs] 生成 {out.name} 失败: {e}")
        im.close()
    return total
