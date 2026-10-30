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


def _live_date() -> str:
    """库存库里**当前存在的**快照日期（库里最新的一天）。

    ⚠️ ⛔ 绝不要在测试里硬编码日期：库存按「每店保留最新一份」滚动淘汰旧快照
    （ADR-0015），一跑真删，旧日期就没了 —— 硬编码的测试会**整片变红**，
    而那不是代码回归，是测试数据过期（2026-10-28 实测：23 处硬编码 →
    5 failed + 4 skipped；改成动态取日期后 35 项全过）。

    Returns:
        str: 库里最新的快照日期；库为空时返回空串。
    """
    import sqlite3

    from backend.jobs import config

    db = Path(config.DB_PATH)
    if not db.exists():
        return ""
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT MAX(date) FROM inventory_daily").fetchone()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return row[0] or ""


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
        resp = self.client.get("/api/inventory/restock")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
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
        """两种装配同源：城市分组里的行必须能在平铺长表里原样找到。

        2026-10-28 方案 A：接口一次返回两种装配（rows + groups），
        前端切换视图只做本地装配、不再请求后端——这条断言锁住「两装配同源」，
        防两套装配各自演化出口径漂移。
        """
        data = self.client.get("/api/inventory/restock").json()
        sku_keys = {
            (str(r["sku"]), r["city"], str(r["shop"]), r["qty"]) for r in data["rows"]
        }
        for group in data["groups"]:
            for r in group["rows"]:
                self.assertIn(
                    (str(r["sku"]), group["city"], str(r["shop"]), r["qty"]),
                    sku_keys,
                    "城市视图里的行必须在 SKU 视图里原样出现",
                )

    def test_response_carries_both_assemblies(self):
        """接口一次给全两种装配：rows（平铺长表）+ groups（城市分组）。

        2026-10-28 方案 A：前端切换视图改为本地装配，前提是响应里两种装配都在。
        少任何一个，切换都会退化成「切了但没数据」——所以这条断言是契约的锁。
        """
        resp = self.client.get("/api/inventory/restock")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("rows", data, "响应必须带平铺长表 rows")
        self.assertIn("groups", data, "响应必须带城市分组 groups")
        self.assertIn("sku_count", data, "响应必须带 SKU 个数 sku_count")
        for group in data["groups"]:
            for r in group["rows"]:
                self.assertGreater(r["qty"], 0, "城市分组只应含建议量 > 0 的行")
        if data["rows"]:
            self.assertTrue(
                any(r["qty"] == 0 for r in data["rows"]),
                "平铺长表应保留建议量为 0 的行",
            )

    def test_offshelf_skus_are_excluded_in_both_views(self):
        sku_rows = self._sku_rows()
        data = self.client.get("/api/inventory/restock").json()
        rows = list(sku_rows)
        for group in data["groups"]:
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

        resp = self.client.post(f"/api/inventory/restock/export?date={_live_date()}")
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

        resp = self.client.post(f"/api/inventory/restock/export?date={_live_date()}")
        self.assertEqual(resp.status_code, 200)
        sku_view = self.client.get(f"/api/inventory/restock?view=sku&date={_live_date()}").json()
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

        resp = self.client.post(f"/api/inventory/restock/export?date={_live_date()}")
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

        resp = self.client.get(f"/api/inventory/restock/export?date={_live_date()}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("no-store", resp.headers.get("cache-control", ""), "导出不能被浏览器缓存")
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        post = self.client.post(f"/api/inventory/restock/export?date={_live_date()}")
        wb_post = openpyxl.load_workbook(io.BytesIO(post.content))
        self.assertEqual(wb.sheetnames, wb_post.sheetnames, "GET 与 POST 必须导出同样的 sheet")
        self.assertEqual(
            wb[wb.sheetnames[1]].max_row,
            wb_post[wb_post.sheetnames[1]].max_row,
            "GET 与 POST 的 SKU sheet 行数必须一致",
        )

    def test_gap_detail_text_matches_page(self):
        """导出说明列 / 批注的文字必须与页面 title 逐字一致（前端 gapFlagTitle 同口径）。"""
        from backend.app import restock_export

        self.assertEqual(
            restock_export._gap_detail_text("关注", 83.6, 1, 14),
            "周期14 天仅 1 天有货（缺货 13 天），估算缺货损失 83.6 件",
            "关注说明句",
        )
        self.assertEqual(
            restock_export._gap_detail_text("关注", 15.6, 3, 14),
            "周期14 天仅 3 天有货（缺货 11 天），估算缺货损失 15.6 件",
            "关注说明句（运营给的样例口径）",
        )
        self.assertEqual(
            restock_export._gap_detail_text("", 83.6, 1, 14),
            "",
            "不关注的行不写说明",
        )
        # 整数天数不能写成 "1.0"（JS 的 String(1) 是 "1"，页面如此）
        self.assertEqual(restock_export._js_num(1.0), "1")
        self.assertEqual(restock_export._js_num(0), "0")
        self.assertEqual(restock_export._js_num(83.6), "83.6")

    def test_export_gap_flag_cell_has_badge_and_hover_note(self):
        """「关注」列要像页面徽章一样被标出，说明句进「估算缺货损失」列并挂成批注。

        页面：状态列旁的紫色「关注」徽章 + title 悬浮说明；
        导出：同一列写成紫色字 + 浅紫底 + 批注（Excel 的悬停提示），
        「估算缺货损失」列写全句（周期 / 有货天数 / 缺货天数 / 损失件数）。
        """
        import io

        import openpyxl

        from backend.app import restock_export

        def _row(**kw) -> dict:
            base = {
                "sku": "TEST-SKU-1",
                "sku_name": "测试商品",
                "shop": "测试店",
                "city": "北京",
                "category": "测试类目",
                "shelf_status": "在架",
                "spu": "TEST-SPU-1",
                "seller": "张三",
                "erp_code": "E001",
                "sellable_days": 3.5,
                "stock": 1,
                "purchase": 0,
                "out": 20,
                "velocity": 6.43,
                "avail": 1,
                "velocity_kind": "measured",
                "gap_flag": "关注",
                "lost_sales": 83.6,
                "target": 70,
                "qty": 60,
                "status": "alert",
            }
            base.update(kw)
            return base

        flagged = _row()
        plain = _row(sku="TEST-SKU-2", gap_flag="", lost_sales=0.0)
        payload = {
            "date": "2026-10-26",
            "params": {"days": 9, "safety": 0.2, "window": 14},
            "rows": [flagged, plain],
            "city_groups": [
                {
                    "city": "北京",
                    "code": "bj",
                    "threshold": 10,
                    "rows": [flagged],
                    "subtotal": 60,
                }
            ],
            "grand_total": 60,
            "sku_count": 1,
        }
        detail_text = "周期14 天仅 1 天有货（缺货 13 天），估算缺货损失 83.6 件"
        wb = openpyxl.load_workbook(io.BytesIO(restock_export.build_workbook(payload)))
        for sheet_name in ("按SKU", "按城市"):
            ws = wb[sheet_name]
            head = [c.value for c in ws[1]]
            self.assertIn("关注", head, f"{sheet_name} 缺「关注」列")
            self.assertIn("估算缺货损失", head, f"{sheet_name} 缺「估算缺货损失」列")
            # 周期必须写在表头里：这个列的数值只是"有货天数"，脱离周期看不出好坏
            avail_head = [h for h in head if isinstance(h, str) and h.startswith("周期有货天数")]
            self.assertEqual(len(avail_head), 1, f"{sheet_name} 应有且仅有一个「周期有货天数」列")
            self.assertIn("14", avail_head[0], f"{sheet_name} 周期有货天数表头必须带周期：{avail_head[0]}")
            # 「预计可售天数」排在「状态」前面
            self.assertLess(
                head.index("预计可售天数"),
                head.index("状态"),
                f"{sheet_name} 预计可售天数必须在状态列之前",
            )
            gap_col = head.index("关注") + 1
            detail_col = head.index("估算缺货损失") + 1
            # 按城市 sheet 第 1 行是城市分组头，数据行从第 2 行起；这里按文案定位，不写死行号
            flagged_cells = [
                ws.cell(row=i, column=gap_col)
                for i in range(2, ws.max_row + 1)
                if (ws.cell(row=i, column=gap_col).value or "") == "关注"
            ]
            self.assertEqual(len(flagged_cells), 1, f"{sheet_name} 应有且仅有一个亮灯行")
            cell = flagged_cells[0]
            color = (cell.font.color.rgb if cell.font and cell.font.color else "") or ""
            self.assertTrue(
                str(color).endswith("6D28D9"),
                f"{sheet_name} 关注徽章字色必须是页面同款紫色：{color}",
            )
            self.assertTrue(
                str(cell.fill.fgColor.rgb or "").endswith("F5F3FF"),
                f"{sheet_name} 关注徽章底色必须是页面同款浅紫",
            )
            self.assertIsNotNone(cell.comment, f"{sheet_name} 关注徽章缺悬停批注")
            self.assertEqual(
                cell.comment.text,
                detail_text,
                f"{sheet_name} 批注文案与页面 title 不一致",
            )
            # 说明句写在「估算缺货损失」列（运营诉求：要一眼看出缺了几天、损失几件）
            self.assertEqual(
                ws.cell(row=cell.row, column=detail_col).value,
                detail_text,
                f"{sheet_name} 估算缺货损失列必须写完整说明句",
            )
        # 无档位的行不套样式、不写批注、不写说明（与页面"不渲染徽章"一致）
        sku_ws = wb["按SKU"]
        head = [c.value for c in sku_ws[1]]
        gap_col = head.index("关注") + 1
        detail_col = head.index("估算缺货损失") + 1
        sku_col = head.index("SKU编码") + 1
        plain_cells = [
            sku_ws.cell(row=i, column=gap_col)
            for i in range(2, sku_ws.max_row + 1)
            if sku_ws.cell(row=i, column=sku_col).value == "TEST-SKU-2"
        ]
        self.assertEqual(len(plain_cells), 1, "应有一行无档位的 SKU")
        plain_cell = plain_cells[0]
        self.assertEqual(plain_cell.value or "", "", "无档位不该写文案")
        self.assertIsNone(plain_cell.comment, "无档位不该写悬停批注")
        self.assertEqual(
            sku_ws.cell(row=plain_cell.row, column=detail_col).value or "",
            "",
            "无档位不该写说明句",
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
            resp = self.client.get(f"/api/inventory/restock/export?date={_live_date()}")
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


class TestMatrixExport(unittest.TestCase):
    """矩阵导出：两个 sheet + 指标列随「指标配置」勾选变化。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _export(self, query: str = ""):
        import io

        import openpyxl

        resp = self.client.post(f"/api/inventory/matrix/export{query}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("spreadsheetml", resp.headers.get("content-type", ""))
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("no-store", resp.headers.get("cache-control", ""), "导出不能被浏览器缓存")
        return openpyxl.load_workbook(io.BytesIO(resp.content))

    def test_workbook_has_two_sheets_and_identity_columns(self):
        wb = self._export(f"?date={_live_date()}")
        self.assertEqual(wb.sheetnames, ["按SKU", "按城市"], "sheet 顺序：按SKU 在前、按城市 在后")
        sku_head = [c.value for c in wb["按SKU"][1]]
        city_head = [c.value for c in wb["按城市"][1]]
        for key in ("商品图", "商品名称", "SKU编码", "SPU编码", "店铺", "上下柜状态", "三级类目", "ERP编码", "销售员名称"):
            self.assertIn(key, sku_head, f"按SKU sheet 缺身份列：{key}")
        for key in ("SKU编码", "商品名称", "店铺", "三级类目", "SPU编码", "销售员名称", "ERP编码"):
            self.assertIn(key, city_head, f"按城市 sheet 缺身份列：{key}")
        self.assertIn("城市", sku_head, "按SKU sheet 必须有城市列")
        # 嵌图不需要加高行高（图片浮网格之上），所以两个 sheet 都嵌图且行高仍统一
        self.assertGreater(len(wb["按SKU"]._images), 0, "按SKU sheet 应嵌入商品图")
        self.assertGreater(len(wb["按城市"]._images), 0, "按城市 sheet 应嵌入商品图")

    def test_merged_cells_are_centered(self):
        """合并单元格的内容必须居中（水平 + 垂直）。

        不设对齐时 openpyxl 默认底部对齐：合并区比内容高时文字沉底，
        正是用户反馈的现象（2026-10-26）。
        """
        wb = self._export(f"?date={_live_date()}&metrics=city_stock,nat_slow")
        for name in ("按SKU", "按城市"):
            ws = wb[name]
            checked = 0
            for rng in ws.merged_cells.ranges:
                cell = ws.cell(row=rng.min_row, column=rng.min_col)
                if cell.value in (None, ""):
                    continue
                self.assertEqual(cell.alignment.horizontal, "center", f"{name} {rng} 未水平居中")
                self.assertEqual(cell.alignment.vertical, "center", f"{name} {rng} 未垂直居中")
                checked += 1
            self.assertGreater(checked, 0, f"{name} 没有可校验的合并单元格")

    def test_sku_sheet_rows_uniform_height(self):
        """按SKU sheet 块内行高一律一致：首行（北京）不许比其它城市行高。

        原先照搬补货导出的 52pt（图片独占一行），用户反馈"第一行太高"（2026-10-26）；
        现在统一 ROW_HEIGHT，同时**仍然嵌图**——图片浮在网格之上，不必给它加高行高。
        """
        from backend.app import matrix_export

        wb = self._export(f"?date={_live_date()}&metrics=city_stock")
        ws = wb["按SKU"]
        for block_start in range(2, 20, 9):
            heights = [ws.row_dimensions[r].height for r in range(block_start, block_start + 9)]
            self.assertEqual(len(set(heights)), 1, f"块 {block_start} 行高不一致：{heights}")
            self.assertEqual(heights[0], matrix_export.ROW_HEIGHT)

    def test_metric_columns_follow_selection(self):
        """勾选决定列：只选 city_stock 时不该出现其它指标列（模版只演示几个指标的泛化）。"""
        wb = self._export(f"?date={_live_date()}&metrics=city_stock")
        head = [c.value for c in wb["按SKU"][1]]
        self.assertIn("现货库存", head)
        self.assertNotIn("采购未到货", head)
        self.assertNotIn("全国滞销件数", head)
        # 全国指标同样可勾选（单列，不按城市展开）
        wb2 = self._export(f"?date={_live_date()}&metrics=nat_slow")
        head2 = [c.value for c in wb2["按SKU"][1]]
        self.assertIn("全国滞销件数", head2)
        self.assertNotIn("现货库存", head2)

    def test_default_columns_match_default_visible(self):
        from backend.jobs.inventory_metrics import METRICS

        wb = self._export(f"?date={_live_date()}")
        head = [c.value for c in wb["按SKU"][1]]
        for m in METRICS:
            if m["locked"]:
                continue
            if m["default_visible"]:
                self.assertIn(m["title"], head, f"默认勾选指标缺列：{m['title']}")
            else:
                self.assertNotIn(m["title"], head, f"未勾选指标不该出现：{m['title']}")

    def test_unknown_metric_key_ignored(self):
        wb = self._export(f"?date={_live_date()}&metrics=__nope__,city_stock")
        head = [c.value for c in wb["按SKU"][1]]
        self.assertIn("现货库存", head, "合法 key 不受非法 key 牵连")

    def test_sku_sheet_row_count_matches_matrix_rows(self):
        rows = self.client.get(f"/api/inventory/rows?date={_live_date()}&page_size=500").json()["rows"]
        if not rows:
            self.skipTest("库存库暂无矩阵行")
        wb = self._export(f"?date={_live_date()}&metrics=city_stock")
        ws = wb["按SKU"]
        # 每个矩阵行 = 8 个城市行 + 1 行合计
        self.assertEqual(ws.max_row - 1, len(rows) * 9, "按SKU sheet 行数必须与矩阵行数一致")

    def test_filters_match_matrix_rows(self):
        """筛选口径必须与 /rows 一致（导出的是当前筛选下的全量矩阵行）。"""
        total = self.client.get(
            f"/api/inventory/rows?keyword=%E6%BB%A4%E8%8A%AF&date={_live_date()}&page_size=1"
        ).json()["total"]
        if not total:
            self.skipTest("当前库存库没有命中该关键词的矩阵行")
        wb = self._export(f"?date={_live_date()}&keyword=%E6%BB%A4%E8%8A%AF&metrics=city_stock")
        self.assertEqual(wb["按SKU"].max_row - 1, total * 9, "导出行数必须与矩阵筛选结果一致")

    def test_city_sheet_groups_have_subtotals(self):
        rows = self.client.get(f"/api/inventory/rows?date={_live_date()}&page_size=500").json()["rows"]
        if not rows:
            self.skipTest("库存库暂无矩阵行")
        wb = self._export(f"?date={_live_date()}&metrics=city_stock")
        ws = wb["按城市"]
        head = [c.value for c in ws[1]]
        stock_col = head.index("现货库存") + 1
        # 行结构：1 行表头 + 8 城 × (分组头 + N 行 + 小计) + 1 行总计
        self.assertEqual(ws.max_row, 1 + 8 * (len(rows) + 2) + 1, "按城市 sheet 行数不符合分组结构")
        # 每个小计行的现货库存列必须是数字（率值列不合计的规则另见 aggregatable 测试）
        subtotal_rows = [i for i in range(2, ws.max_row + 1) if ws.cell(row=i, column=2).value == "小计"]
        self.assertEqual(len(subtotal_rows), 8, "8 个分组各一个小计行")
        for i in subtotal_rows:
            self.assertIsInstance(
                ws.cell(row=i, column=stock_col).value,
                (int, float),
                f"第 {i} 行小计必须是数字",
            )

    def test_ratio_metrics_not_totaled(self):
        """率值指标（预计可售天数）不参与合计行——与矩阵表 tfoot 同口径。"""
        wb = self._export(f"?date={_live_date()}&metrics=city_sellable_days")
        ws = wb["按SKU"]
        head = [c.value for c in ws[1]]
        days_col = head.index("预计可售天数") + 1
        # 合计行（每块第 10 行）：城市列写「合计」，率值列必须留空
        for block_start in range(2, min(ws.max_row, 20) + 1, 9):
            total_row = block_start + 8
            self.assertEqual(ws.cell(row=total_row, column=10).value, "合计")
            self.assertIn(
                ws.cell(row=total_row, column=days_col).value,
                (None, ""),
                "预计可售天数不该出现在合计行",
            )

    def test_export_supports_get_for_native_browser_download(self):
        """GET 也要能导出（前端用 <a href> 触发浏览器原生下载）。"""
        resp = self.client.get(f"/api/inventory/matrix/export?date={_live_date()}&metrics=city_stock")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("no-store", resp.headers.get("cache-control", ""))

    def test_failure_returns_attachment_txt(self):
        """生成失败也必须返回附件：否则浏览器会把当前页面导航到错误页。"""
        from unittest import mock

        with mock.patch(
            "backend.app.matrix_export.build_workbook",
            side_effect=RuntimeError("模拟生成失败"),
        ):
            resp = self.client.get(f"/api/inventory/matrix/export?date={_live_date()}")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("attachment", resp.headers.get("content-disposition", ""))
        self.assertIn("导出失败", resp.content.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
