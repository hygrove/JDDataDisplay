# -*- coding: utf-8 -*-
"""补货清单导出：后端生成 xlsx（两个 sheet + 商品图嵌入）。

为什么在后端生成（而不是前端 SheetJS）：
  - SheetJS 不能在 xlsx 里嵌图片，而需求要求导出带商品图；
  - 后端用 openpyxl 生成，既能嵌图，又能保证「导出 = 页面所见」——
    导出用的是与清单接口**同一套**行装配与参数夹取逻辑（见 inventory_api）。

两个 sheet：
  - 「按城市」：城市分组头 → 数据行 → 每组小计 → 8 城总计（与城市视图同构）；
  - 「按 SKU」：明细长表，一行一个 SKU × 城市（与 SKU 视图同构，身份列逐行重复）。

页面上的「关注」徽章（断货 / 缺口两档已合并为一个「关注」）与它的说明句在导出里
也要看得见：
  - 徽章 → 「关注」列用紫色字 + 浅紫底标出（页面 .st-gap 同色）；
  - 说明句 → 「估算缺货损失」列写全句（「周期14 天仅 3 天有货（缺货 11 天），
    估算缺货损失 15.6 件」），同时挂进「关注」单元格的**批注**（鼠标悬停即见，
    Excel 没有 title 属性）；批注文案与页面 title 逐字同口径，见 _gap_detail_text。

列序（两个 sheet 一致）：… 周期有货天数（带周期）→ 目标库存 → 建议补货量 →
预计可售天数 → 状态 → 估算缺货损失 → 关注。

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
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..jobs import config
from ..jobs.alert_rules import GAP_FLAG

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

# 缺口关注徽章样式（与页面 .st-gap 同色：violet-700 字 #6d28d9 + 浅紫底 #f5f3ff）
_GAP_FILL = PatternFill("solid", fgColor="F5F3FF")
_GAP_FONT = Font(bold=True, color="6D28D9")

# 单元格批注（Excel 里承接页面 title 悬浮说明）的作者名
_COMMENT_AUTHOR = "补货清单"


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


def _to_float(value: Any) -> float:
    """数值兜底解析：非数 / 非有限值 → 0（与前端 ``Number.isFinite(x) ? Number(x) : 0`` 同口径）。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def _js_num(value: Any) -> str:
    """按 JS ``String(number)`` 的口径把数值转成文本（悬浮说明里要用）。

    JS 里 ``1.0`` 就是 ``1``（模板字符串得到 "1"），Python 的 ``f"{1.0}"`` 会得到
    "1.0"——页面与 Excel 的说明文字因此会差一截。这里统一成 JS 口径：
    整数值不带小数点，非整数值用最短往返表示（与 V8 一致）。
    """
    n = _to_float(value)
    if n == int(n) and abs(n) < 1e16:
        return str(int(n))
    return repr(n)


def _gap_detail_text(flag: Any, lost_sales: Any, avail: Any, window: Any) -> str:
    """缺口关注说明句（页面悬浮说明与导出说明列共用同一句文案）。

    形如「周期14 天仅 3 天有货（缺货 11 天），估算缺货损失 15.6 件」：
    把"周期多长、其中几天有货、缺了几天、损失几件"一次说清，运营不用再回查
    其它列去拼上下文（2026-10-26 用户诉求：估算缺货损失列要更详细）。

    与前端 ``modules/inventory.ts:gapFlagTitle`` **逐字同口径**（两边各有断言锁住）。

    Args:
        flag: 关注档位（服务端判定；空 = 不关注）。
        lost_sales: 估算缺货损失（件）。
        avail: 窗口有货天数。
        window: 窗口天数。

    Returns:
        str: 说明句；不关注的行返回空串（不留空白说明）。
    """
    if not str(flag or "").strip():
        return ""
    w = _to_float(window)
    a = _to_float(avail)
    loss = _to_float(lost_sales)
    stockout = max(0.0, w - a)
    return (
        f"周期{_js_num(w)} 天仅 {_js_num(a)} 天有货（缺货 {_js_num(stockout)} 天），"
        f"估算缺货损失 {_js_num(loss)} 件"
    )


def _mark_gap_cell(ws: Any, row_idx: int, col_idx: int, row: dict[str, Any], window: Any) -> None:
    """给「关注」单元格写徽章文案 + 紫色样式 + 悬停说明批注。

    @remarks
    页面上的关注标记是紫色徽章（``st-gap``）；导出只写纯文本看不出是"标记"，
    所以这里补紫色字 + 浅紫底，并把说明句搬进批注（Excel 的悬停提示）。
    档位文案统一为「关注」（原「断货 / 缺口」两档合并，见 alert_rules.GAP_FLAG）；
    不关注的行不写文案、不套样式、不写批注（与页面"不渲染徽章"一致）。
    """
    cell = ws.cell(row=row_idx, column=col_idx)
    if not str(row.get("gap_flag") or "").strip():
        cell.value = ""
        return
    cell.value = GAP_FLAG
    cell.fill = _GAP_FILL
    cell.font = _GAP_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
    detail = _gap_detail_text(row.get("gap_flag"), row.get("lost_sales"), row.get("avail"), window)
    if detail:
        cell.comment = Comment(detail, _COMMENT_AUTHOR, height=90, width=340)


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
    window = payload["params"]["window"]
    headers = [
        "商品图", "SKU编码", "商品名称", "店铺", "上下柜状态", "三级类目",
        "SPU编码", "销售员名称", "ERP编码", "现货", "在途", "日均销速",
        # 表头里带上周期：这个列的数值只是"有货天数"，脱离周期看不出好坏
        f"周期有货天数（{window}日窗）", "目标库存", "建议补货量", "预计可售天数",
        "状态", "估算缺货损失", "关注",
    ]
    widths = [PIC_COL_WIDTH, 16, 34, 10, 10, 14, 18, 12, 18, 8, 8, 10, 16, 10, 12, 12, 8, 48, 10]
    _write_header(ws, headers, widths)
    name_col = headers.index("商品名称") + 1
    gap_col = headers.index("关注") + 1
    detail_col = headers.index("估算缺货损失") + 1

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
                _display_days(r.get("sellable_days")),
                _status_text(r.get("status")),
                # 关注说明句：亮灯行写全句，不关注的行留空（不留"0 件"的噪音）
                _gap_detail_text(r.get("gap_flag"), r.get("lost_sales"), r.get("avail"), window),
                "",
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row_idx, column=col, value=value)
            _set_wrap(ws, row_idx, name_col)
            _set_wrap(ws, row_idx, detail_col)
            ws.row_dimensions[row_idx].height = _row_height_for_name(r.get("sku_name"))
            _add_picture(ws, row_idx, 1, r.get("sku"))
            _mark_gap_cell(ws, row_idx, gap_col, r, window)
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
    window = payload["params"]["window"]
    headers = [
        "商品图", "商品名称", "SKU编码", "店铺", "上下柜状态", "三级类目",
        "ERP编码", "销售员名称", "SPU编码", "城市", "现货", "在途", "日均销速",
        # 表头里带上周期：这个列的数值只是"有货天数"，脱离周期看不出好坏
        f"周期有货天数（{window}日窗）", "目标库存", "建议补货量", "预计可售天数",
        "状态", "估算缺货损失", "关注",
    ]
    widths = [PIC_COL_WIDTH, 34, 16, 10, 10, 14, 18, 12, 18, 8, 8, 8, 10, 16, 10, 12, 12, 8, 48, 10]
    _write_header(ws, headers, widths)
    name_col = headers.index("商品名称") + 1
    gap_col = headers.index("关注") + 1
    detail_col = headers.index("估算缺货损失") + 1

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
            _display_days(r.get("sellable_days")),
            _status_text(r.get("status")),
            # 关注说明句：亮灯行写全句，不关注的行留空（不留"0 件"的噪音）
            _gap_detail_text(r.get("gap_flag"), r.get("lost_sales"), r.get("avail"), window),
            "",
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(row=row_idx, column=col, value=value)
        _set_wrap(ws, row_idx, name_col)
        _set_wrap(ws, row_idx, detail_col)
        ws.row_dimensions[row_idx].height = _row_height_for_name(r.get("sku_name"))
        _add_picture(ws, row_idx, 1, r.get("sku"))
        _mark_gap_cell(ws, row_idx, gap_col, r, window)
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


