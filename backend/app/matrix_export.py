# -*- coding: utf-8 -*-
"""库存矩阵导出：后端生成 xlsx（两个 sheet + 商品图嵌入）。

与 restock_export 的分工：那个模块导「补货清单」（建议量口径，页面所见即所得）；
本模块导「库存矩阵」——矩阵表的所见即所得，**指标列由「指标配置」勾选动态决定**
（参考模版只演示了几个指标，实际列随勾选变化，见 inventory_metrics.METRICS）。

两个 sheet（格式参考需求模版「库存明细模版.xlsx」）：
- 「按SKU」：一个矩阵行 = 身份列（合并单元格，模版同款）+ 8 个城市行 + 合计行；
  每城指标一列（值取该城，城市由「城市」列标识），全国指标一列（值合并到块首行）。
- 「按城市」：按 8 城分组（分组头 + 数据行 + 每城小计 + 总计行），
  每行 = 一个 SKU 在该城的指标值；小计/总计只对 aggregatable 指标求和
  （与矩阵表 tfoot 同一口径，率值指标如「数量周转」「预计可售天数」不合计）。

单元格口径（_display_number / _text_cell / _erp_cell / 嵌图 / 行高）直接复用
restock_export，保证「页面 = 补货导出 = 矩阵导出」同一套显示规则，不二次实现。

商品图：模版按SKU sheet 用 WPS 的 DISPIMG 公式（Excel 打不开），这里统一嵌**真实图片**
（64px jpg 缩略图，与补货导出同链路）。商品图放在**块的合并单元格**上（每个 SKU 块的
身份区 A 列 9 行 合并成一个格），与模版 DISPIMG 的视觉位置一致。

⚠️ **嵌图不需要加高行高**（2026-10-26 用户纠正）：Excel 里的图片是**浮在工作表网格之上**
的 OneCellAnchor（锚到单元格、尺寸自带），它不参与也不受行高约束，比行高大的部分会
自然往下方**同一列的空白行**溢出——单品模块导出 `pop_spu_单品数据_*.xlsx` 就是这么做的
（120×120px 的图压着 15pt 的默认行，全程未设行高）。补货导出那套给每行加 52pt 的行高
是它自己的约定（每行都有图，加高防止图片互相压），不是 Excel 的要求，本模块不继承。
所以本模块的按SKU sheet **行高统一 20pt**（模版口径）+ 正常嵌图两者可以同时成立。

按城市 sheet 每个 SKU 只在首次出现处嵌图：openpyxl 3.1 对相同字节的图片**不去重**，
逐行嵌会让文件膨胀数倍（全量日期约 2000 行 × 8 城）。
"""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from . import restock_export as _re

# 身份列（顺序即展示顺序，与模版两个 sheet 各自的首部列一致）
_SKU_IDENTITY = ["商品图", "商品名称", "SKU编码", "SPU编码", "店铺", "上下柜状态", "三级类目", "ERP编码", "销售员名称"]
_CITY_IDENTITY = ["商品图", "SKU编码", "商品名称", "店铺", "上下柜状态", "三级类目", "SPU编码", "销售员名称", "ERP编码"]

# 列宽（模版口径；商品图列与指标列取导出通用值）
_SKU_WIDTHS = [_re.PIC_COL_WIDTH, 34, 16, 18, 10, 10, 14, 18, 12, 8]
_CITY_WIDTHS = [_re.PIC_COL_WIDTH, 16, 34, 10, 10, 14, 18, 12, 18]
_METRIC_WIDTH = 11

# 按SKU sheet 的统一行高（磅，模版口径）：块内 8 个城市行 + 合计行一律同高，
# 不给任何行特殊加高（嵌图加的 52pt 已按用户反馈去掉，见模块 docstring）。
ROW_HEIGHT = 20.0

# 合并单元格的对齐：内容居中（水平 + 垂直）。
# 不设对齐时 openpyxl 默认底部对齐——合并区比内容高时文字沉底，正是用户反馈的现象；
# 商品名称列额外开自动换行（长描述在合并区里折行，垂直方向仍居中）。
_CENTER = Alignment(horizontal="center", vertical="center")
_CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)

# 合计 / 小计 / 总计行的强调样式（模版里这几行是纯文本，这里加粗便于扫读；
# 值写成**数字**而不是「合计:88」文本——下游要在 Excel 里继续算，别自断后路）
_TOTAL_FONT = Font(bold=True)


def _metric_columns(metrics: list[dict]) -> list[dict]:
    """导出要渲染的指标列（保持 meta 顺序）。

    @remarks
    身份列（identity）由两个 sheet 内置写死，不依赖勾选，故这里过滤掉；
    调用方传入的 metrics 已是解析后的选中清单（per_city + national）。
    """
    return [m for m in metrics if m.get("kind") != "identity"]


def _write_total_row_label(ws: Any, row_idx: int, col_idx: int, label: str) -> None:
    """写合计行的行标签（「合计」/「小计」/「总计」，加粗）。"""
    cell = ws.cell(row=row_idx, column=col_idx, value=label)
    cell.font = _TOTAL_FONT


def _sku_sheet(wb: Workbook, payload: dict[str, Any]) -> None:
    """「按SKU」sheet：一个矩阵行一块（身份列合并 + 8 城市行 + 合计行）。"""
    ws = wb.create_sheet("按SKU")
    metrics = _metric_columns(payload["metrics"])
    cities = payload["cities"]
    erp = payload.get("erp_codes") or {}

    headers = _SKU_IDENTITY + ["城市"] + [m["title"] for m in metrics]
    widths = _SKU_WIDTHS + [_METRIC_WIDTH] * len(metrics)
    _re._write_header(ws, headers, widths)

    n_id = len(_SKU_IDENTITY)          # 身份列数（前 n_id 列，块内合并）
    city_col = n_id + 1                # 「城市」列
    first_metric_col = n_id + 2        # 第一个指标列

    row_idx = 2
    for r in payload["rows"]:
        block_start = row_idx
        sku = str(r.get("sku") or "")
        # 身份列只写块首行，随后跨 8 个城市行合并（模版同款）
        identity = [
            None,  # 商品图占位（嵌图用，见块尾 _add_picture）
            _re._text_cell(r.get("sku_name")),
            sku,
            _re._text_cell(r.get("spu")),
            _re._text_cell(r.get("shop")),
            _re._text_cell(r.get("shelf_status")),
            _re._text_cell(r.get("category")),
            _re._erp_cell(erp.get(sku)),
            _re._text_cell(r.get("seller")),
        ]
        for col, value in enumerate(identity, start=1):
            cell = ws.cell(row=row_idx, column=col, value=value)
            # 合并区内容居中；商品名称（第 2 列）开自动换行防长描述被裁
            cell.alignment = _CENTER_WRAP if col == 2 else _CENTER
        # 8 个城市行
        for ci, city in enumerate(cities):
            ws.cell(row=row_idx, column=city_col, value=city)
            for mi, m in enumerate(metrics):
                col = first_metric_col + mi
                cell = ws.cell(row=row_idx, column=col)
                if m["kind"] == "per_city":
                    cell.value = _re._display_number(r.get(m["fields"][ci]))
                elif ci == 0:
                    # 全国指标：整块一个值，写在首行（随后与身份列一起合并）
                    cell.value = _re._display_number(r.get(m["fields"][0]))
                    cell.alignment = _CENTER
            row_idx += 1
        block_end = row_idx - 1  # 最后一个城市行
        # 合计行（模版结构）：身份列不合并进来，指标值单独一行
        _write_total_row_label(ws, row_idx, city_col, "合计")
        for mi, m in enumerate(metrics):
            if not m["aggregatable"]:
                continue  # 率值指标不合计（与矩阵表 tfoot 同口径）
            col = first_metric_col + mi
            total = sum(_re._to_float(r.get(f)) for f in m["fields"])
            cell = ws.cell(row=row_idx, column=col, value=_re._display_number(total))
            cell.font = _TOTAL_FONT
        # 合并：身份列 + 全国指标列，跨 8 个城市行（不含合计行）
        merge_cols = list(range(1, n_id + 1)) + [
            first_metric_col + mi for mi, m in enumerate(metrics) if m["kind"] != "per_city"
        ]
        for col in merge_cols:
            ws.merge_cells(start_row=block_start, start_column=col, end_row=block_end, end_column=col)
        # 商品图：锚在块首行（落在合并的商品图单元格上），图片浮网格之上，不影响行高
        _re._add_picture(ws, block_start, 1, sku)
        # 行高统一：块首行（北京）与其它城市行、合计行同高（用户 2026-10-26 反馈）。
        # 注意别照搬补货导出的 52pt——嵌图不需要加高行高（见模块 docstring）。
        for r in range(block_start, row_idx + 1):
            ws.row_dimensions[r].height = ROW_HEIGHT
        row_idx += 1


def _city_sheet(wb: Workbook, payload: dict[str, Any]) -> None:
    """「按城市」sheet：8 城分组（分组头 + 数据行 + 每城小计 + 总计行）。"""
    ws = wb.create_sheet("按城市")
    metrics = _metric_columns(payload["metrics"])
    cities = payload["cities"]
    rows = payload["rows"]
    erp = payload.get("erp_codes") or {}
    city_totals = payload.get("city_totals") or {}
    totals = payload.get("totals") or {}

    headers = _CITY_IDENTITY + [m["title"] for m in metrics]
    widths = _CITY_WIDTHS + [_METRIC_WIDTH] * len(metrics)
    _re._write_header(ws, headers, widths)

    n_id = len(_CITY_IDENTITY)
    first_metric_col = n_id + 1
    sku_col = 2  # 小计/总计标签写在 SKU 编码列

    row_idx = 2
    seen_sku: set[str] = set()
    for ci, city in enumerate(cities):
        # 分组头（模版同款：贯通整行的灰底条目，内容居中）
        head = ws.cell(row=row_idx, column=1, value=f"{city}　{len(rows)} 个 SKU")
        head.font = _re._GROUP_FONT
        head.fill = _re._GROUP_FILL
        head.alignment = _CENTER
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=len(headers))
        row_idx += 1
        for r in rows:
            sku = str(r.get("sku") or "")
            values = [
                None,  # 商品图占位
                sku,
                _re._text_cell(r.get("sku_name")),
                _re._text_cell(r.get("shop")),
                _re._text_cell(r.get("shelf_status")),
                _re._text_cell(r.get("category")),
                _re._text_cell(r.get("spu")),
                _re._text_cell(r.get("seller")),
                _re._erp_cell(erp.get(sku)),
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row_idx, column=col, value=value)
            for mi, m in enumerate(metrics):
                col = first_metric_col + mi
                if m["kind"] == "per_city":
                    ws.cell(row=row_idx, column=col, value=_re._display_number(r.get(m["fields"][ci])))
                else:
                    ws.cell(row=row_idx, column=col, value=_re._display_number(r.get(m["fields"][0])))
            _re._set_wrap(ws, row_idx, 3)  # 商品名称列
            ws.row_dimensions[row_idx].height = _re._row_height_for_name(r.get("sku_name"))
            # 嵌图去重：同一 SKU 只在首次出现处嵌（openpyxl 对相同字节图片不去重）
            if sku and sku not in seen_sku:
                seen_sku.add(sku)
                _re._add_picture(ws, row_idx, 1, sku)
            row_idx += 1
        # 每城小计：aggregatable 指标写该城合计（city_totals 与矩阵表列尾同口径）
        _write_total_row_label(ws, row_idx, sku_col, "小计")
        for mi, m in enumerate(metrics):
            if not m["aggregatable"]:
                continue
            col = first_metric_col + mi
            if m["kind"] == "per_city":
                per_city = city_totals.get(m["key"]) or []
                value = per_city[ci] if ci < len(per_city) else None
            else:
                value = sum(_re._to_float(r.get(m["fields"][0])) for r in rows)
            cell = ws.cell(row=row_idx, column=col, value=_re._display_number(value))
            cell.font = _TOTAL_FONT
        row_idx += 1
    # 总计行：per_city 指标写 8 城合计（totals 与矩阵表 tfoot 同口径）
    _write_total_row_label(ws, row_idx, sku_col, "总计")
    for mi, m in enumerate(metrics):
        if not m["aggregatable"]:
            continue
        col = first_metric_col + mi
        if m["kind"] == "per_city":
            value = totals.get(m["key"])
        else:
            value = sum(_re._to_float(r.get(m["fields"][0])) for r in rows)
        cell = ws.cell(row=row_idx, column=col, value=_re._display_number(value))
        cell.font = _TOTAL_FONT


def build_workbook(payload: dict[str, Any]) -> bytes:
    """把库存矩阵装配成 xlsx 字节流（两个 sheet + 商品图嵌入）。

    Args:
        payload: 矩阵导出数据：
            {"rows"(全列 dict 列表，已过滤/排序，不分页),
             "metrics"(选中指标，meta 顺序，不含 identity),
             "cities"(8 城白名单), "totals"(指标 key → 8 城合计),
             "city_totals"(指标 key → 每城合计数组), "erp_codes"(SKU → ERP 编码)}。

    Returns:
        bytes: xlsx 文件内容。
    """
    wb = Workbook()
    wb.remove(wb.active)  # 去掉默认空 sheet
    # sheet 顺序：按SKU 在前（明细是主用途）、按城市 在后（按城分组带小计），与模版一致
    _sku_sheet(wb, payload)
    _city_sheet(wb, payload)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
