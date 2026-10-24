# -*- coding: utf-8 -*-
"""补货清单导出：后端生成 xlsx（两个 sheet + 商品图嵌入）。

为什么在后端生成（而不是前端 SheetJS）：
  - SheetJS 不能在 xlsx 里嵌图片，而需求要求导出带商品图；
  - 后端用 openpyxl 生成，既能嵌图，又能保证「导出 = 页面所见」——
    导出用的是与清单接口**同一套**行装配与参数夹取逻辑（见 inventory_api）。

两个 sheet：
  - 「按城市」：城市分组头 → 数据行 → 每组小计 → 8 城总计（与城市视图同构）；
  - 「按 SKU」：明细长表，一行一个 SKU × 城市（与 SKU 视图同构，身份列逐行重复）。

ERP 编码缺失时导出写 0（下游模板要一个占位值）；页面上显示「--」——
两处差异是有意的，见 docs/adr/0009-erp-mapping-separate-table.md。
"""
from __future__ import annotations

import io
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..jobs import config

# 导出里图片的显示尺寸（像素）与商品图列宽（Excel 列宽单位）
PIC_PX = 64
PIC_COL_WIDTH = 12
# 图片所在行的行高（磅；1 磅 ≈ 1.33 像素）
PIC_ROW_HEIGHT = 52
# 「商品名称」列宽（两个 sheet 同宽）与折行估算用的行高
NAME_COL_WIDTH = 34
LINE_HEIGHT_PT = 15.0

# 表头底色与字体
_HEAD_FILL = PatternFill("solid", fgColor="EEF2FF")
_HEAD_FONT = Font(bold=True)
_GROUP_FILL = PatternFill("solid", fgColor="F3F4F6")
_GROUP_FONT = Font(bold=True, color="1F2937")

# 状态徽章文案（与前端展示一致：alert→预警 / incoming→在途 / 其余→健康）
_STATUS_TEXT = {"alert": "预警", "incoming": "在途", "healthy": "健康"}


def _status_text(status: Any) -> str:
    """补货状态 → 中文文案。"""
    return _STATUS_TEXT.get(str(status or ""), "健康")


def _erp_cell(code: Any) -> str | int:
    """ERP 编码单元格：未登记写 0（下游模板占位；库里不写 0）。"""
    text = str(code or "").strip()
    return text if text else 0


# 页面缺值占位符（前端 modules/inventory.ts 的 orEmpty / sellableDaysText 同口径）
EMPTY = "—"


def _js_math_round(x: float) -> int:
    """等价 JS ``Math.round``：floor(x + 0.5)，半值向 +∞ 进位。

    与 Python 内置 ``round`` 的**银行家舍入**不同（round(0.5)=0 vs JS 的 1），
    页面用的是 JS 口径，导出必须一模一样，否则同一数值在页面与 Excel 里差 0.1。
    """
    return math.floor(x + 0.5)


def _display_number(value: Any) -> float | str:
    """数值单元格：按页面 ``fmt()`` 的口径写值（Math.round(n*100)/100）。

    页面把数量、销速、目标库存、建议量都过一遍 ``fmt``（两位小数、千分位），
    导出直接写原始值会出现"页面 12.35 / Excel 12.345"这种对不上的情况。
    """
    if value is None:
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(n):
        return ""
    return _js_math_round(n * 100) / 100


def _display_days(value: Any) -> float | str:
    """预计可售天数单元格：按页面 ``sellableDaysText()`` 的口径（toFixed(1)）。

    JS ``toFixed`` 的规则是"在**精确二进制值**上四舍五入，半值远离零"
    （71.25 → "71.3"、-71.25 → "-71.3"、12.35 → "12.3"）；
    Python 内置 ``round`` 是银行家舍入（71.25 → 71.2），直接用会让导出与页面差 0.1。
    这里的写法（``Decimal(精确值).quantize(ROUND_HALF_UP)``）已用 400 个随机样本
    对齐过 V8 的 ``toFixed(1)``，零差异。
    """
    if value is None:
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(n):
        return ""
    try:
        quant = Decimal(1).scaleb(-1)
        return float(Decimal(n).quantize(quant, rounding=ROUND_HALF_UP))
    except Exception:  # noqa: BLE001 —— 极端数值不该让整个导出失败
        return round(n, 1)


def _text_cell(value: Any) -> str:
    """文本单元格：缺失写页面同款占位符「—」（前端 orEmpty 口径）。"""
    text = str(value or "").strip()
    return text if text else EMPTY


def _pic_file(sku: Any) -> Path | None:
    """取库存 SKU 商品图的源文件路径（openpyxl 只认 png/jpeg/gif，故用原图 jpg）。"""
    from ..jobs import inv_pics

    pic = inv_pics.get_index().get(str(sku or ""))
    if pic is None:
        return None
    path = config.INV_PIC_DIR / pic.sub / pic.name
    return path if path.is_file() else None


# SKU → 缩略图字节缓存（同一 SKU 在多个城市行里出现，只压一次）
_thumb_cache: dict[str, bytes] = {}


def _thumb_bytes(sku: Any) -> bytes | None:
    """取 SKU 商品图的 64px 缩略图字节。

    @remarks
    必须先缩小再嵌：源图是 800×800 的 jpg，逐行直接嵌会让 xlsx 膨胀到几百 MB
    （实测单次导出 234MB），缩到 64px 后每张只有几 KB。

    Args:
        sku: 库存 SKU。

    Returns:
        bytes | None: JPEG 字节；无图或图片损坏时返回 None（不报错、不阻断导出）。
    """
    key = str(sku or "")
    if not key:
        return None
    if key in _thumb_cache:
        return _thumb_cache[key] or None
    path = _pic_file(key)
    data = b""
    if path is not None:
        try:
            from PIL import Image as PILImage

            with PILImage.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((PIC_PX, PIC_PX))
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=72)
                data = buf.getvalue()
        except Exception as e:  # noqa: BLE001 —— 图片坏了不该让整个导出失败
            print(f"[restock_export] 商品图缩略失败（跳过）：{path.name} {type(e).__name__}: {e}")
            data = b""
    _thumb_cache[key] = data
    return data or None


def _add_picture(ws: Any, row_idx: int, col_idx: int, sku: Any) -> None:
    """把商品图缩略图嵌进指定单元格（无图不嵌，不报错）。"""
    data = _thumb_bytes(sku)
    if not data:
        return
    try:
        img = XLImage(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 —— 图片坏了不该让整个导出失败
        print(f"[restock_export] 商品图嵌入失败（跳过）：{sku} {type(e).__name__}: {e}")
        return
    img.width = PIC_PX
    img.height = PIC_PX
    # 行高只保证"图放得下"，文字要几行由 _row_height_for_name 决定（谁大听谁的）
    current = ws.row_dimensions[row_idx].height or 0
    ws.row_dimensions[row_idx].height = max(current, PIC_ROW_HEIGHT)
    ws.add_image(img, f"{get_column_letter(col_idx)}{row_idx}")


def _row_height_for_name(name: Any) -> float:
    """按商品名的折行数算行高。

    开了自动换行以后，文字要几行就占几行；行高写死的话，多出来的行会被**裁掉**。
    Excel 列宽单位约等于"数字 0"的宽度，一个中文字约 2 单位；行高按 15pt/行估。

    Args:
        name: 商品名称。

    Returns:
        float: 行高（磅）。至少 PIC_ROW_HEIGHT（64px 的图要放得下）。
    """
    text = str(name or "")
    if not text:
        return PIC_ROW_HEIGHT
    width = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    lines = max(1, -(-width // (NAME_COL_WIDTH - 1)))  # 向上取整
    return max(PIC_ROW_HEIGHT, lines * LINE_HEIGHT_PT + 4)


def _write_header(ws: Any, headers: list[str], widths: list[int]) -> None:
    """写表头并设列宽。"""
    for idx, (title, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = _HEAD_FILL
        cell.font = _HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = width


def _set_wrap(ws: Any, row_idx: int, col_idx: int) -> None:
    """给单元格开自动换行。

    商品名常有长描述（"钻头 龙井水水泥滤芯..."），不换行在 Excel 里会显示不全、
    被邻列挡着；开 wrap 后 Excel 自己折行。
    """
    cell = ws.cell(row=row_idx, column=col_idx)
    cell.alignment = Alignment(wrap_text=True, vertical="center")


def _city_sheet(wb: Workbook, payload: dict[str, Any]) -> None:
    """「按城市」sheet：分组头 → 数据行 → 每组小计 → 8 城总计。"""
    ws = wb.create_sheet("按城市")
    headers = [
        "商品图", "SKU编码", "商品名称", "店铺", "上下柜状态", "三级类目",
        "SPU编码", "销售员名称", "ERP编码", "现货", "在途", "日均销速",
        "周期有货天数", "目标库存", "建议补货量", "状态", "预计可售天数",
    ]
    widths = [PIC_COL_WIDTH, 16, 34, 10, 10, 14, 18, 12, 18, 8, 8, 10, 10, 10, 12, 8, 12]
    _write_header(ws, headers, widths)
    name_col = headers.index("商品名称") + 1

    row_idx = 2
    for group in payload["city_groups"]:
        head = ws.cell(
            row=row_idx,
            column=1,
            value=(
                f"{group['city']}　阈值 {group['threshold']} · "
                f"{len(group['rows'])} SKU · 小计 {group['subtotal']} 件"
            ),
        )
        head.font = _GROUP_FONT
        head.fill = _GROUP_FILL
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=len(headers))
        row_idx += 1
        for r in group["rows"]:
            values = [
                None,
                str(r.get("sku") or ""),
                _text_cell(r.get("sku_name")),
                _text_cell(r.get("shop")),
                _text_cell(r.get("shelf_status")),
                _text_cell(r.get("category")),
                _text_cell(r.get("spu")),
                _text_cell(r.get("seller")),
                _erp_cell(r.get("erp_code")),
                _display_number(r.get("stock")),
                _display_number(r.get("purchase")),
                _display_number(r.get("velocity")),
                _display_number(r.get("avail")),
                _display_number(r.get("target")),
                _display_number(r.get("qty")),
                _status_text(r.get("status")),
                _display_days(r.get("sellable_days")),
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row_idx, column=col, value=value)
            _set_wrap(ws, row_idx, name_col)
            ws.row_dimensions[row_idx].height = _row_height_for_name(r.get("sku_name"))
            _add_picture(ws, row_idx, 1, r.get("sku"))
            row_idx += 1
        sub = ws.cell(
            row=row_idx,
            # 小计写在「目标库存」列下（插列后按表头定位，不再写死列号）
            column=headers.index("目标库存") + 1,
            value=f"小计 {group['subtotal']}",
        )
        sub.font = _GROUP_FONT
        row_idx += 1
    total = ws.cell(
        row=row_idx,
        column=1,
        value=(
            f"8 城总计（{payload['params']['days']} 天 × "
            f"{round(payload['params']['safety'] * 100)}% × {payload['params']['window']} 日窗口）"
            f"　本周建议采购总量 {payload['grand_total']} 件"
        ),
    )
    total.font = _GROUP_FONT
    total.fill = _GROUP_FILL
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=len(headers))


def _sku_sheet(wb: Workbook, payload: dict[str, Any]) -> None:
    """「按 SKU」sheet：明细长表，一行一个 SKU × 城市（身份列逐行重复）。

    商品图放在**第一列**：看图识货是这张表的主要用途，图在最左一列时不用横向滚动。
    """
    ws = wb.create_sheet("按SKU")
    headers = [
        "商品图", "商品名称", "SKU编码", "店铺", "上下柜状态", "三级类目",
        "ERP编码", "销售员名称", "SPU编码", "城市", "现货", "在途", "日均销速",
        "周期有货天数", "目标库存", "建议补货量", "状态", "预计可售天数",
    ]
    widths = [PIC_COL_WIDTH, 34, 16, 10, 10, 14, 18, 12, 18, 8, 8, 8, 10, 10, 10, 12, 8, 12]
    _write_header(ws, headers, widths)
    name_col = headers.index("商品名称") + 1

    row_idx = 2
    for r in payload["rows"]:
        values = [
            None,
            _text_cell(r.get("sku_name")),
            str(r.get("sku") or ""),
            _text_cell(r.get("shop")),
            _text_cell(r.get("shelf_status")),
            _text_cell(r.get("category")),
            _erp_cell(r.get("erp_code")),
            _text_cell(r.get("seller")),
            _text_cell(r.get("spu")),
            str(r.get("city") or ""),
            _display_number(r.get("stock")),
            _display_number(r.get("purchase")),
            _display_number(r.get("velocity")),
            _display_number(r.get("avail")),
            _display_number(r.get("target")),
            _display_number(r.get("qty")),
            _status_text(r.get("status")),
            _display_days(r.get("sellable_days")),
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col, value=value)
        _set_wrap(ws, row_idx, name_col)
        ws.row_dimensions[row_idx].height = _row_height_for_name(r.get("sku_name"))
        _add_picture(ws, row_idx, 1, r.get("sku"))
        row_idx += 1


def build_workbook(payload: dict[str, Any]) -> bytes:
    """把补货清单装配成 xlsx 字节流（两个 sheet + 商品图嵌入）。

    Args:
        payload: 补货数据（与清单接口同一套装配结果）：
            {"date", "params", "rows"(长表), "city_groups", "grand_total", "sku_count"}。

    Returns:
        bytes: xlsx 文件内容。
    """
    wb = Workbook()
    wb.remove(wb.active)  # 去掉默认空 sheet
    # sheet 顺序：按SKU 在前（明细长表是主用途）、按城市 在后（按城分组带小计）
    _sku_sheet(wb, payload)
    _city_sheet(wb, payload)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


