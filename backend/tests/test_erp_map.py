# -*- coding: utf-8 -*-
"""ERP 编码映射的单元测试（stdlib unittest，零依赖可跑）。

运行：仓库根 `python -m unittest backend.tests.test_erp_map -v`
（也可在 backend 目录下 `python -m unittest tests.test_erp_map -v`）。

被测接缝（公共接口 = jobs.erp_map 模块的公开函数，不测内部实现）：
- parse_erp_pairs：表头识别、SKU 规范化（Excel 把长数字读成 float）、空值跳过、
  同一 SKU 多次出现后者覆盖
- upsert_erp_codes / lookup_erp_codes：幂等写入、批量查、**未登记的 SKU 不返回**
  （有行=已登记，无行=未登记，见 ADR-0009）
- ingest_erp_map：读源表 → 落库；源表缺失 → 不动库；本表是源表的镜像
  （源表删掉的映射，库里也不留）
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# 让本文件在任何工作目录下都能 import 到 backend 里的 jobs 包
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
# 接口级测试要 import backend.app.main（仓库根在路径里）
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import erp_map  # noqa: E402


def _write_erp_xlsx(path: Path, rows: list[tuple]) -> None:
    """造一张 ERP 编码表 xlsx（表头 + 数据行）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["sku", "ERP编码"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)


class TestParseErpPairs(unittest.TestCase):
    """源表行 → {sku: erp_code} 的解析。"""

    def test_reads_sku_and_erp_columns(self):
        pairs = erp_map.parse_erp_pairs(
            [("sku", "ERP编码"), (100080186917, "TYLX-LTJS-TC-ZX")]
        )
        self.assertEqual(pairs, {"100080186917": "TYLX-LTJS-TC-ZX"})

    def test_normalizes_float_like_sku(self):
        # Excel 把长数字读成 float 时会带 ".0"，键必须与库存宽表的 SKU 逐字符相等
        pairs = erp_map.parse_erp_pairs([("sku", "ERP编码"), ("100149827444.0", "X")])
        self.assertEqual(pairs, {"100149827444": "X"})

    def test_skips_blank_erp_code_and_missing_sku(self):
        # 空 ERP 值 / 空 SKU 一律不写行（=未登记），不能写进 0
        pairs = erp_map.parse_erp_pairs(
            [("sku", "ERP编码"), ("1001", ""), (None, "X"), ("1002", "Y")]
        )
        self.assertEqual(pairs, {"1002": "Y"})

    def test_duplicate_sku_last_wins(self):
        pairs = erp_map.parse_erp_pairs(
            [("sku", "ERP编码"), ("1001", "OLD"), ("1001", "NEW")]
        )
        self.assertEqual(pairs, {"1001": "NEW"})

    def test_empty_source_yields_empty_mapping(self):
        self.assertEqual(erp_map.parse_erp_pairs([]), {})

    def test_drops_self_referential_code(self):
        # 表头识别走偏时（例如首行是标题而非表头）会解析出 sku → sku 的假编码；
        # 假编码会被下游当真值用，必须在解析层丢掉
        pairs = erp_map.parse_erp_pairs([("sku", "ERP编码"), ("1001", "1001")])
        self.assertEqual(pairs, {})

    def test_keeps_alphanumeric_sku(self):
        # 真实源表里存在 EMG 形态的 SKU（电商平台编码），它不是噪声，不能丢
        pairs = erp_map.parse_erp_pairs(
            [("sku", "ERP编码"), ("EMG4418927463633", "ZX-600GZDCXDSROJ")]
        )
        self.assertEqual(pairs, {"EMG4418927463633": "ZX-600GZDCXDSROJ"})

    def test_drops_hyphen_shaped_sku(self):
        # ERP 编码含连字符（256 行里 255 行）、SKU 不含：SKU 位出现连字符即说明列角色反了
        pairs = erp_map.parse_erp_pairs([("sku", "ERP编码"), ("ZX-600GZDCXDSROJ", "1001")])
        self.assertEqual(pairs, {})

    def test_normalizes_numeric_looking_code(self):
        # 源表把编码存成数字时会读出 "12345.0"，脏值不能进库
        pairs = erp_map.parse_erp_pairs([("sku", "ERP编码"), ("1001", 12345.0)])
        self.assertEqual(pairs, {"1001": "12345"})


class TestUpsertAndLookup(unittest.TestCase):
    """映射表的写入与查询。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(erp_map.erp_map_schema_sql())

    def tearDown(self):
        self.conn.close()

    def test_upsert_is_idempotent(self):
        erp_map.upsert_erp_codes(self.conn, {"1001": "A"})
        erp_map.upsert_erp_codes(self.conn, {"1001": "A"})
        rows = self.conn.execute("SELECT sku, erp_code FROM erp_map").fetchall()
        self.assertEqual(len(rows), 1)

    def test_upsert_updates_existing_mapping(self):
        erp_map.upsert_erp_codes(self.conn, {"1001": "A"})
        erp_map.upsert_erp_codes(self.conn, {"1001": "B"})
        found = erp_map.lookup_erp_codes(self.conn, ["1001"])
        self.assertEqual(found, {"1001": "B"})

    def test_lookup_returns_only_registered_skus(self):
        erp_map.upsert_erp_codes(self.conn, {"1001": "A"})
        found = erp_map.lookup_erp_codes(self.conn, ["1001", "1002"])
        self.assertEqual(found, {"1001": "A"})
        self.assertNotIn("1002", found)

    def test_lookup_ignores_empty_sku(self):
        erp_map.upsert_erp_codes(self.conn, {"1001": "A"})
        self.assertEqual(erp_map.lookup_erp_codes(self.conn, ["", None]), {})


class TestIngest(unittest.TestCase):
    """源表 → 映射表的摄入。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(erp_map.erp_map_schema_sql())
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_ingest_reads_source_file(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(100080186917, "A"), (1002, "B")])
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(
            erp_map.lookup_erp_codes(self.conn, ["100080186917", "1002"]),
            {"100080186917": "A", "1002": "B"},
        )

    def test_repeated_ingest_is_stable(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A"), (1002, "B")])
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        # 用公共接口断言内容（而不是直接查表）：两次摄入后映射集合完全相同
        self.assertEqual(
            erp_map.lookup_erp_codes(self.conn, ["1001", "1002", "1003"]),
            {"1001": "A", "1002": "B"},
        )

    def test_missing_source_keeps_table_untouched(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A")])
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        result = erp_map.ingest_erp_map(root=self.root / "空的", conn=self.conn)
        self.assertFalse(result["changed"])
        self.assertEqual(result["reason"], "source_missing")
        count = self.conn.execute("SELECT COUNT(*) FROM erp_map").fetchone()[0]
        self.assertEqual(count, 1)

    def test_table_mirrors_source_removals(self):
        # 本表是源表的镜像：源表删掉的映射，库里不留（否则库里存的是假事实）
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A"), (1002, "B")])
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A")])
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        found = erp_map.lookup_erp_codes(self.conn, ["1001", "1002"])
        self.assertEqual(found, {"1001": "A"})

    def test_shrink_guard_keeps_existing_mappings(self):
        # 源表条数断崖式减少（多半是表被改坏了）时不得清空已有映射
        _write_erp_xlsx(
            self.root / "ERP编码表.xlsx", [(i, f"CODE{i}") for i in range(100, 110)]
        )
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(100, "CODE100")])
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertEqual(result["reason"], "shrink_guard")
        self.assertEqual(result["removed"], 0)
        found = erp_map.lookup_erp_codes(self.conn, ["100", "101"])
        self.assertEqual(found, {"100": "CODE100", "101": "CODE101"})

    def test_changed_flag_reflects_real_difference(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A"), (1002, "B")])
        first = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        again = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertTrue(first["changed"])
        self.assertFalse(again["changed"])

    def test_changed_flag_true_when_code_updates(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A")])
        erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "B")])
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertTrue(result["changed"])
        self.assertEqual(erp_map.lookup_erp_codes(self.conn, ["1001"]), {"1001": "B"})

    def test_ingest_reports_source_effective_rows(self):
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", [(1001, "A"), (1002, ""), (1003, "C")])
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        # 空编码行不算已登记，rows 只数有效映射
        self.assertEqual(result["rows"], 2)

    def test_parse_guard_refuses_structurally_broken_source(self):
        # 源表列角色颠倒（SKU 位全是 ERP 编码形态）时不得往库里写假映射
        rows = [(f"CODE-{i}", f"V{i}") for i in range(8)] + [(f"100{i}", f"W{i}") for i in range(2)]
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", rows)
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertEqual(result["reason"], "parse_guard")
        self.assertEqual(erp_map.lookup_erp_codes(self.conn, ["1000", "1001"]), {})

    def test_parse_guard_refuses_swapped_columns(self):
        # 整表列角色颠倒：多数行因"SKU 含连字符"被解析层丢弃，
        # 剩下的一两条不足以代表源表 → 批量护栏必须拦下（不写库）
        rows = [(f"CODE-{i}", f"V{i}") for i in range(8)] + [("MYPPYL1J1X", "1009")]
        _write_erp_xlsx(self.root / "ERP编码表.xlsx", rows)
        result = erp_map.ingest_erp_map(root=self.root, conn=self.conn)
        self.assertEqual(result["reason"], "parse_guard")
        self.assertEqual(erp_map.lookup_erp_codes(self.conn, ["MYPPYL1J1X"]), {})


class TestRestockEndpoint(unittest.TestCase):
    """接口层：补货清单每行的 ERP 编码字段（真实库，不依赖具体数据）。"""

    def test_rows_carry_erp_code_consistent_with_mapping_table(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as client:
            resp = client.get("/api/inventory/restock")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        rows = [row for g in data["groups"] for row in g["rows"]]
        for row in rows:
            self.assertIn("erp_code", row)
            self.assertTrue(row["erp_code"] is None or isinstance(row["erp_code"], str))
            if row["erp_code"] is not None:
                self.assertNotEqual(row["erp_code"], str(row["sku"]))  # 不是自指的假编码


if __name__ == "__main__":
    unittest.main()
