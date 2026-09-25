# -*- coding: utf-8 -*-
"""SPU 原图 -> 多尺寸缩略图（AVIF 优先 + WebP 兜底）。

背景（为什么要做这件事）：
  源图普遍很大（常见 2000px 的 PNG，0.9~3MB），而前端展示框实际只有 96 / 120 / 180px，
  直接把原图下发纯属浪费带宽、拖慢首屏。这里在**批处理阶段**就把缩略图生成好，
  前端用 <picture> 让浏览器自动挑 AVIF / WebP，既省流量又不用运行时现算。

产出：
  thumbs/{96,120,180}/{stem}.avif 与 thumbs/{96,120,180}/{stem}.webp
  原图仍保留在 /images 下，详情页要看大图时依然可访问。

触发时机：
  在 pipeline._load_image_map 中调用，随每次批处理**幂等重算**——
  只有「缩略图不存在」或「比源图旧」时才重新生成，源图没变则直接跳过。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# 注：Pillow >= 12.0 已原生支持 AVIF 读写（本环境为 12.3.0），无需 pillow-avif-plugin。
# 如降级到旧 Pillow，需额外安装 pillow-avif-plugin 并 import 以注册编解码器，
# 否则保存 AVIF 时会抛 "unknown file extension"。

# THUMB_SIZES：需要生成的缩略图尺寸，与前端展示框一一对应——
#   180 = 单日模式卡片图，120 = 区间模式行内图，96 = 单品分析页 hero 图
THUMB_SIZES = (96, 120, 180)

# THUMB_FORMATS：(文件扩展名, Pillow 保存格式名, 质量)
#   AVIF 体积最小画质最好，排第一供浏览器优先采用；WebP 作为兼容性兜底。
#   质量值经过实测权衡：AVIF 55 / WebP 80 在缩略图尺寸下肉眼无差，但体积明显更小
THUMB_FORMATS = (
    ("avif", "AVIF", 55),
    ("webp", "WEBP", 80),
)

# _SRC_EXTS：支持的源图后缀；不在集合里的文件（如 xlsx、临时文件）直接跳过
_SRC_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _open_rgb(img_path: Path) -> Image.Image:
    """打开图片并转成 RGB 模式（透明区域填白底）。

    为什么要转 RGB：
      AVIF / WebP 保存带 alpha 通道的 RGBA 图时，透明区域在部分浏览器里会被渲染成黑色块。
      商品图大多是白底+透明背景，统一铺白底可以避免「缩略图边缘发黑」的视觉 bug。

    Args:
        img_path: 源图片路径，需为 _SRC_EXTS 中任一格式。

    Returns:
        Image.Image: RGB 模式的图片对象；调用方负责在使用完毕后 close()。

    Raises:
        Exception: 图片文件损坏 / 格式不被 Pillow 识别时，由 Image.open 或 im.load() 抛出。
            上层 build_thumbnails 会捕获并跳过该图，不会中断整批处理。

    Example:
        >>> im = _open_rgb(Path("spu.png"))
        >>> im.mode
        'RGB'
    """
    im = Image.open(img_path)
    # 显式 load()：Pillow 是惰性读取，异常常在这里才抛出，提前触发便于上层捕获
    im.load()
    # RGBA / LA / 带 transparency 的调色板图都可能有透明像素，统一合成到白底
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        # 用 alpha 通道作为蒙版粘贴，保留原图非透明部分的细节
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def build_thumbnails(src_dir: Path, thumbs_root: Path) -> int:
    """为 src_dir 下的所有图片生成多尺寸 AVIF + WebP 缩略图。

    幂等逻辑：
      只有当「某尺寸某格式的缩略图不存在」或「缩略图修改时间早于源图」时才重新生成，
      这样每天跑批处理时，未变动的图不会重复编码（AVIF 编码较耗 CPU）。

    Args:
        src_dir: 源图目录（数据源里的「单品spu图片」或已同步到 DATA_DIR/images 的目录）。
        thumbs_root: 缩略图根目录，产物落在 thumbs_root/{size}/{stem}.{ext}。

    Returns:
        int: 本次实际生成（保存成功）的缩略图文件数。全部跳过时为 0。

    Raises:
        本函数**不向外抛异常**：单张图解码失败或保存失败都只打印告警并跳过，
        保证一张坏图不会让整批缩略图生成中断。

    Example:
        >>> n = build_thumbnails(Path("ResourceData/单品spu图片"), Path("data/images/thumbs"))
        >>> n >= 0
        True
    """
    if not src_dir.is_dir():
        return 0

    # 预建三个尺寸目录，避免保存时逐文件判断目录是否存在
    for size in THUMB_SIZES:
        (thumbs_root / str(size)).mkdir(parents=True, exist_ok=True)

    total = 0
    # sorted 保证处理顺序稳定，便于日志对照与问题复现
    for src in sorted(src_dir.iterdir()):
        # 跳过子目录与非图片后缀（数据源目录里可能混入 xlsx、临时文件）
        if not src.is_file() or src.suffix.lower() not in _SRC_EXTS:
            continue

        # 判断是否需要重算：任一尺寸/格式的产物缺失或过期即需要
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
            # copy 后再 thumbnail：thumbnail 是原地缩放，避免污染下一轮尺寸的计算基准
            thumb = im.copy()
            # LANCZOS 在缩小时画质最好；thumbnail 会保持宽高比且不会放大（长边不超过 size）
            thumb.thumbnail((size, size), Image.LANCZOS)
            for ext, fmt, quality in THUMB_FORMATS:
                out = thumbs_root / str(size) / f"{src.stem}.{ext}"
                try:
                    thumb.save(out, fmt, quality=quality)
                    total += 1
                except Exception as e:
                    # 单个格式保存失败（如编解码器缺失）不影响其它格式，只告警
                    print(f"[make_thumbs] 生成 {out.name} 失败: {e}")
        # 显式释放解码后的位图，避免大目录批处理时内存堆积
        im.close()
    return total
