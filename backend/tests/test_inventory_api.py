# -*- coding: utf-8 -*-
"""库存 API 的接口级测试（stdlib unittest + fastapi TestClient，零新依赖）。

运行：仓库根 `python -m unittest backend.tests.test_inventory_api -v`
（或 backend 目录下 `python -m unittest tests.test_inventory_api -v`）。

被测接缝（公共 HTTP 接口）：
- /api/inventory/restock 的关键词过滤：命中 SKU / 商品名称 / SPU；
  命中不到时返回空清单。
- 关键词口径与 /api/inventory/rows 一致（两个端点共用同一套过滤）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 让本文件在任何工作目录下都能 import 到 backend 包
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402


def _restock_rows(client: TestClient, query: str = "") -> list[dict]:
    """打一次补货清单接口，返回打平后的行。"""
    resp = client.get(f"/api/inventory/restock{query}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return [row for group in data["groups"] for row in group["rows"]]


class TestRestockKeyword(unittest.TestCase):
    """补货清单的关键词过滤。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_keyword_narrows_the_list(self):
        rows = _restock_rows(self.client)
        if not rows:
            self.skipTest("库存库暂无补货行")
        target = str(rows[0]["sku"])
        narrowed = _restock_rows(self.client, f"?keyword={target}")
        self.assertLessEqual(len(narrowed), len(rows))
        for row in narrowed:
            hit = (
                target.lower() in str(row.get("sku") or "").lower()
                or target.lower() in str(row.get("sku_name") or "").lower()
                or target.lower() in str(row.get("spu") or "").lower()
            )
            self.assertTrue(hit, f"行不匹配关键词口径：{row}")

    def test_unmatched_keyword_returns_empty_list(self):
        rows = _restock_rows(self.client, "?keyword=__不存在的关键词__")
        self.assertEqual(rows, [])

    def test_keyword_scope_matches_matrix_rows(self):
        """关键词口径必须与矩阵表一致（两个端点共用同一套过滤函数）。"""
        keyword = "滤芯"
        resp = self.client.get(f"/api/inventory/rows?keyword={keyword}&page_size=500")
        self.assertEqual(resp.status_code, 200)
        matrix_skus = {r["sku"] for r in resp.json()["rows"]}
        if not matrix_skus:
            self.skipTest("当前库存库没有命中该关键词的矩阵行")
        for row in _restock_rows(self.client, f"?keyword={keyword}"):
            self.assertIn(row["sku"], matrix_skus)


class TestRestockRowFields(unittest.TestCase):
    """城市视图每行的身份列与辅助字段（工单 03）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_rows_carry_identity_and_helper_fields(self):
        rows = _restock_rows(self.client)
        if not rows:
            self.skipTest("库存库暂无补货行")
        for row in rows:
            for key in ("spu", "seller", "sku_pic", "sellable_days"):
                self.assertIn(key, row)
            self.assertTrue(row["spu"] is None or isinstance(row["spu"], str))
            self.assertTrue(row["seller"] is None or isinstance(row["seller"], str))
            self.assertTrue(row["sku_pic"] is None or isinstance(row["sku_pic"], str))
            self.assertTrue(
                row["sellable_days"] is None
                or isinstance(row["sellable_days"], (int, float))
            )

    def test_sellable_days_equals_city_metric_column(self):
        """预计可售天数必须取该城指标列的原值（率值，不参与任何合计）。"""
        import sqlite3

        from backend.jobs.config import DB_PATH
        from backend.jobs.inventory_metrics import CITY_CODES

        resp = self.client.get("/api/inventory/restock")
        data = resp.json()
        if not data["groups"]:
            self.skipTest("库存库暂无补货行")
        conn = sqlite3.connect(str(DB_PATH))
        checked = 0
        try:
            for group in data["groups"]:
                code = group["code"]
                self.assertIn(code, set(CITY_CODES.values()))
                for row in group["rows"][:3]:
                    found = conn.execute(
                        f"select {code}_sellable_days from inventory_daily "
                        "where date=? and shop=? and sku=?",
                        (data["date"], row["shop"], row["sku"]),
                    ).fetchone()
                    if found is None:
                        continue
                    self.assertEqual(row["sellable_days"], found[0])
                    checked += 1
        finally:
            conn.close()
        if checked == 0:
            self.skipTest("没有可核对的行")


class TestRestockSkuView(unittest.TestCase):
    """SKU 视图：平铺长表（工单 04）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _sku_rows(self) -> list[dict]:
        resp = self.client.get("/api/inventory/restock?view=sku")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["view"], "sku")
        rows = data["rows"]
        if not rows:
            self.skipTest("库存库暂无补货行")
        return rows

    def test_rows_sorted_by_sku_then_city_order(self):
        from backend.jobs.inventory_metrics import CITIES

        rows = self._sku_rows()
        skus = [str(r["sku"]) for r in rows]
        self.assertEqual(skus, sorted(skus), "行必须按 SKU 编码升序")
        order = {city: i for i, city in enumerate(CITIES)}
        by_sku: dict[str, list[str]] = {}
        for r in rows:
            by_sku.setdefault(str(r["sku"]), []).append(r["city"])
        for sku, cities in by_sku.items():
            self.assertEqual(
                cities, sorted(cities, key=lambda c: order[c]), f"{sku} 的城市顺序不对"
            )
            self.assertEqual(len(cities), len(set(cities)), f"{sku} 的城市行有重复")

    def test_keeps_zero_qty_rows_and_skips_empty_cities(self):
        rows = self._sku_rows()
        self.assertTrue(
            any(r["qty"] == 0 for r in rows), "SKU 视图应保留建议量为 0 的行"
        )
        for r in rows:
            # 行的存在依据是「该城窗口内有过出库/现货/在途」三者之一，
            # 与销速无关：ADR-0010 的 L0 规则（有货天数 ≤ 0 → 销速 0）会让
            # velocity 归零，但该城确实出过货，行必须保留。
            self.assertTrue(
                r["stock"] > 0 or r["purchase"] > 0 or r["out"] > 0,
                f"出现了完全没有数据的城市行：{r}",
            )

    def test_velocity_none_means_zero_velocity_and_no_avail(self):
        """L0 语义：有货天数 ≤ 0 → 销速 0（宁可不建议，不凭空估）。"""
        rows = self._sku_rows()
        for r in rows:
            if r["velocity_kind"] != "none":
                continue
            self.assertEqual(r["velocity"], 0, f"none 档销速必须为 0：{r}")
            self.assertEqual(r["avail"], 0, f"none 档有货天数必须为 0：{r}")
            self.assertEqual(r["target"], 0, f"none 档目标库存必须为 0：{r}")

    def test_two_views_are_the_same_calculation(self):
        city = self.client.get("/api/inventory/restock?view=city").json()
        sku = self.client.get("/api/inventory/restock?view=sku").json()
        self.assertEqual(city["grand_total"], sku["grand_total"], "两视图的建议量合计必须一致")
        sku_keys = {
            (str(r["sku"]), r["city"], str(r["shop"]), r["qty"]) for r in sku["rows"]
        }
        for group in city["groups"]:
            for r in group["rows"]:
                self.assertIn(
                    (str(r["sku"]), group["city"], str(r["shop"]), r["qty"]),
                    sku_keys,
                    "城市视图里的行必须在 SKU 视图里原样出现",
                )

    def test_offshelf_skus_are_excluded_in_both_views(self):
        sku_rows = self._sku_rows()
        city = self.client.get("/api/inventory/restock?view=city").json()
        rows = list(sku_rows)
        for group in city["groups"]:
            rows.extend(group["rows"])
        for r in rows:
            self.assertNotIn("下", str(r.get("shelf_status") or ""), "下柜 SKU 不该出现在清单里")


class TestRestockExport(unittest.TestCase):
    """导出：两个 sheet + 商品图嵌入（工单 05）。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_export_returns_workbook_with_two_sheets(self):
        import io

        import openpyxl

        resp = self.client.post("/api/inventory/restock/export?date=2026-10-20")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp.headers.get("content-type", ""))
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertEqual(len(wb.sheetnames), 2, "导出必须是两个 sheet")
        self.assertEqual(wb.sheetnames, ["按SKU", "按城市"], "sheet 顺序：按SKU 在前、按城市 在后")
        sku_ws, city_ws = wb["按SKU"], wb["按城市"]
        city_head = [c.value for c in city_ws[1]]
        sku_head = [c.value for c in sku_ws[1]]
        for key in ("SKU编码", "商品名称", "店铺", "三级类目", "SPU编码", "销售员名称", "ERP编码", "预计可售天数"):
            self.assertIn(key, city_head, f"城市 sheet 缺列：{key}")
            self.assertIn(key, sku_head, f"SKU sheet 缺列：{key}")
        self.assertIn("城市", sku_head, "SKU sheet 必须有城市列")
        self.assertEqual(sku_head[0], "商品图", "按SKU sheet 的商品图必须在第一列")
        self.assertGreater(len(city_ws._images), 0, "城市 sheet 应嵌入商品图")
        self.assertGreater(len(sku_ws._images), 0, "SKU sheet 应嵌入商品图")

    def test_export_rows_match_sku_view(self):
        import io

        import openpyxl

        resp = self.client.post("/api/inventory/restock/export?date=2026-10-20")
        self.assertEqual(resp.status_code, 200)
        sku_view = self.client.get("/api/inventory/restock?view=sku&date=2026-10-20").json()
        rows = sku_view["rows"]
        if not rows:
            self.skipTest("库存库暂无补货行")
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        sku_ws = wb["按SKU"]
        # 第一行是表头，其后每行一个 SKU × 城市
        self.assertEqual(sku_ws.max_row - 1, len(rows), "SKU sheet 行数必须与 SKU 视图一致")
        head = [c.value for c in sku_ws[1]]
        sku_col = head.index("SKU编码") + 1
        erp_col = head.index("ERP编码") + 1
        exported_skus = [str(sku_ws.cell(row=i, column=sku_col).value) for i in range(2, sku_ws.max_row + 1)]
        self.assertEqual(exported_skus, [str(r["sku"]) for r in rows], "导出行序必须与 SKU 视图一致")
        # ERP 缺失 → 导出写 0（页面显示「--」）
        missing = sum(1 for r in rows if not r.get("erp_code"))
        if missing:
            zeros = sum(1 for i in range(2, sku_ws.max_row + 1) if sku_ws.cell(row=i, column=erp_col).value == 0)
            self.assertEqual(zeros, missing, "缺失的 ERP 编码必须写成 0")


    def test_product_name_column_wraps_text(self):
        """两个 sheet 的「商品名称」列都要开自动换行。

        商品名常有长描述（"钻头 龙井水水泥滤芯..."），不换行在 Excel 里会被
        邻列挡住或显示不全；开 wrap 后 Excel 会自己折行。
        """
        import io

        import openpyxl

        resp = self.client.post("/api/inventory/restock/export?date=2026-10-20")
        self.assertEqual(resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        for sheet_name in ("按SKU", "按城市"):
            ws = wb[sheet_name]
            head = [c.value for c in ws[1]]
            self.assertIn("商品名称", head)
            col = head.index("商品名称") + 1
            checked = 0
            for i in range(2, min(ws.max_row, 12) + 1):
                cell = ws.cell(row=i, column=col)
                if cell.value in (None, ""):
                    continue
                self.assertTrue(
                    cell.alignment.wrap_text,
                    f"{sheet_name} 第 {i} 行的商品名称单元格没有开自动换行",
                )
                checked += 1
            self.assertGreater(checked, 0, f"{sheet_name} 没有可校验的商品名称单元格")

    def test_row_height_follows_wrapped_name(self):
        """开了自动换行，行高就得跟着折行数走，否则多出来的行会被裁掉。"""
        from backend.app import restock_export

        short = restock_export._row_height_for_name("短名")
        self.assertEqual(short, restock_export.PIC_ROW_HEIGHT, "短名只用给图留的行高")
        # 当前真实数据里最长的商品名（约 72 字符）折 3 行，52pt 够用
        three_lines = "钻芯 通用美的净水器滤芯S1系列MRC1686a-75G/华凌50G/75G大麦MRO1790b-400g/MU135/M3 RO反渗透膜7"
        self.assertEqual(restock_export._row_height_for_name(three_lines), restock_export.PIC_ROW_HEIGHT)
        # 再长就必须长高，否则折出来的第 4 行会被裁掉
        tall = restock_export._row_height_for_name(three_lines * 2)
        self.assertGreater(tall, restock_export.PIC_ROW_HEIGHT, "长名要更高的行高")
        self.assertGreaterEqual(
            tall,
            4 * restock_export.LINE_HEIGHT_PT,
            "行高至少要容得下折出来的行",
        )
        self.assertEqual(restock_export._row_height_for_name(None), restock_export.PIC_ROW_HEIGHT)

    def test_export_supports_get_for_native_browser_download(self):
        """GET 也要能导出（前端用 <a href> 触发浏览器原生下载）。

        顺带钉住两条响应头：Content-Disposition 决定文件名、no-store 防止
        浏览器把"此刻的快照"缓存成旧文件。
        """
        import io

        import openpyxl

        resp = self.client.get("/api/inventory/restock/export?date=2026-10-20")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("no-store", resp.headers.get("cache-control", ""), "导出不能被浏览器缓存")
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        post = self.client.post("/api/inventory/restock/export?date=2026-10-20")
        wb_post = openpyxl.load_workbook(io.BytesIO(post.content))
        self.assertEqual(wb.sheetnames, wb_post.sheetnames, "GET 与 POST 必须导出同样的 sheet")
        self.assertEqual(
            wb[wb.sheetnames[1]].max_row,
            wb_post[wb_post.sheetnames[1]].max_row,
            "GET 与 POST 的 SKU sheet 行数必须一致",
        )


    def test_export_failure_returns_attachment_instead_of_json(self):
        """导出失败也必须返回附件。

        前端用 <a href> 触发浏览器原生下载；若失败时返回普通 JSON 错误，
        浏览器会把当前工作台页面导航到那个错误页上，用户的筛选状态全丢。
        """
        from unittest import mock

        with mock.patch(
            "backend.app.restock_export.build_workbook",
            side_effect=RuntimeError("模拟生成失败"),
        ):
            resp = self.client.get("/api/inventory/restock/export?date=2026-10-20")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("导出失败", resp.content.decode("utf-8"))


class TestExportDisplayRounding(unittest.TestCase):
    """导出的显示口径必须与页面逐位一致。

    工单 06 逐列核对时抓到过一处真实差异：预计可售天数 71.25，
    Python ``round``（银行家舍入）给 71.2，JS ``toFixed(1)`` 给 71.3——
    同一份数据在页面和 Excel 里差 0.1。这里的期望值取自 V8 实测。
    """

    def test_days_rounding_matches_javascript_to_fixed(self):
        from backend.app import restock_export

        cases = [
            (71.25, 71.3),
            (-71.25, -71.3),
            (71.15, 71.2),
            (12.35, 12.3),
            (0.125, 0.1),
            (0.0, 0.0),
            (8.0, 8.0),
        ]
        for raw, expected in cases:
            self.assertEqual(restock_export._display_days(raw), expected, f"天数口径不一致：{raw}")

    def test_days_missing_stays_blank(self):
        from backend.app import restock_export

        self.assertEqual(restock_export._display_days(None), "")
        self.assertEqual(restock_export._display_days(""), "")

    def test_number_rounding_matches_javascript_fmt(self):
        from backend.app import restock_export

        cases = [(12, 12), (0.125, 0.13), (12.345, 12.35), (1.005, 1.0), (-2.5, -2.5), (-2.567, -2.57)]
        for raw, expected in cases:
            self.assertEqual(restock_export._display_number(raw), expected, f"数值口径不一致：{raw}")

    def test_text_cell_uses_page_placeholder(self):
        from backend.app import restock_export

        self.assertEqual(restock_export._text_cell(None), "—")
        self.assertEqual(restock_export._text_cell("  "), "—")
        self.assertEqual(restock_export._text_cell("北京"), "北京")


if __name__ == "__main__":
    unittest.main()
