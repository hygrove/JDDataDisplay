# -*- coding: utf-8 -*-
"""AI 查询工具层测试：同源断言、参数容错、零写库静态扫描、数据概览。

运行：仓库根 `python -m unittest backend.tests.test_chat_tools -v`

设计说明：
- **同源断言**（工单 02 验收核心）：同一输入下，工具输出与公共 HTTP 端点
  同数字——证明接地层只是"包装"而不是"另写一份查询"（ADR-0016）。
  需要真实 DB：POP / 库存各自空库时对应测试 skip（不是失败）。
- **零写库静态扫描**：chat 三模块源码不出现任何 SQL 写动词——把
  "chat 只读"从人工纪律固化成可跑的检查（ai-rules 元规则：禁令能自动化就自动化）。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import chat_service as svc  # noqa: E402
from backend.app import chat_tools as ct  # noqa: E402
from backend.app.main import app  # noqa: E402

_CLIENT = TestClient(app)


def _pop_ready() -> bool:
    """POP 库是否有数据（manifest 200 = 有；404 = 空库）。"""
    return _CLIENT.get("/api/manifest").status_code == 200


def _inv_ready() -> bool:
    """库存库是否有数据（meta 的 shops 非空 = 有）。"""
    try:
        return bool(_CLIENT.get("/api/inventory/meta").json().get("shops"))
    except Exception:  # noqa: BLE001
        return False


class TestExecuteToolBasics(unittest.TestCase):
    """execute_tool 的分发与容错（不需要 DB）。"""

    def test_unknown_tool(self):
        result = ct.execute_tool("drop_all_tables", {})
        self.assertIn("error", result)
        self.assertIn("未知工具", result["error"])
        # 错误信息里列出可用工具，模型下一轮能自我纠正
        self.assertIn("pop_rows", result["error"])

    def test_args_not_dict_treated_as_empty(self):
        result = ct.execute_tool("inventory_alerts", "not-a-dict")
        # 库存空库时是 error 结果；有库时是正常数据——两种都不该抛异常
        self.assertIsInstance(result, dict)

    def test_pop_spu_analysis_requires_spu(self):
        result = ct.execute_tool("pop_spu_analysis", {})
        self.assertIn("error", result)
        self.assertIn("spu", result["error"])

    def test_schema_names_match_handlers(self):
        """schema 声明的工具名与分发表一一对应（漂移 = 模型调到不存在的工具）。"""
        schema_names = {t["function"]["name"] for t in ct.TOOLS_SCHEMA}
        self.assertEqual(schema_names, set(ct._HANDLERS))
        self.assertEqual(len(schema_names), 7)

    def test_round_floats_stable(self):
        self.assertEqual(ct._round_floats({"a": 1.23456, "b": [2.0, "x", None, True]}),
                         {"a": 1.23, "b": [2.0, "x", None, True]})

    def test_pop_interval_metrics_formula(self):
        """区间比率 = 先求和再相除（SSOT），分母为 0 时为 None。"""
        from backend.app.api import DayMetric
        from backend.jobs.models import MetricRecord

        days = [
            DayMetric(date="2026-10-01", metrics=MetricRecord(visitors=100, buyers=10, amount=500)),
            DayMetric(date="2026-10-02", metrics=MetricRecord(visitors=50, buyers=20, amount=700)),
        ]
        m = ct._pop_interval_metrics(days)
        self.assertEqual(m["visitors"], 150.0)
        self.assertEqual(m["buyers"], 30.0)
        # 转化率 = (10+20)/(100+50) = 0.2 —— 绝不是 (0.1+0.4)/2 = 0.25
        self.assertEqual(m["conversion_rate"], 0.2)
        # 客单价 = (500+700)/30
        self.assertEqual(m["avg_price"], round(1200 / 30, 4))
        # 无推广数据 → ROI 为 None（不是 0、不是异常）
        self.assertIsNone(m["roi"])


class TestZeroWriteScan(unittest.TestCase):
    """chat 链路零写库的静态检查（可自动化的禁令，ai-rules 元规则）。"""

    def test_chat_modules_contain_no_write_sql(self):
        pattern = re.compile(
            r"(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)",
            re.IGNORECASE,
        )
        for name in ("chat_api.py", "chat_service.py", "chat_tools.py"):
            path = Path(_REPO_ROOT / "backend" / "app" / name)
            src = path.read_text(encoding="utf-8")
            hits = pattern.findall(src)
            self.assertEqual(hits, [], f"{name} 出现 SQL 写动词：{hits}")


class TestSameSource(unittest.TestCase):
    """同源断言：工具输出与公共端点同数字（需要真实 DB，空库 skip）。"""

    def test_pop_rows_matches_endpoint(self):
        if not _pop_ready():
            self.skipTest("POP 库为空（未跑批处理），跳过同源断言")
        tool = ct.execute_tool("pop_rows", {"sort_by": "amount", "page": 1, "page_size": 5})
        if "error" in tool:
            self.skipTest(f"工具返回 error：{tool['error']}")
        resp = _CLIENT.get(
            "/api/module/pop_spu_detail/rows",
            params={"sort_by": "amount", "page": 1, "page_size": 5},
        ).json()
        self.assertEqual(tool["total"], resp["total"])
        self.assertEqual(len(tool["rows"]), len(resp["rows"]))
        if resp["rows"]:
            # 排序口径一致：第一名是同一个 SPU
            self.assertEqual(tool["rows"][0]["spu"], resp["rows"][0]["spu"])
            # 金额区间合计一致（工具四舍五入到 2 位，容差 0.01）
            endpoint_amount = sum(
                (d["metrics"].get("amount") or 0) for d in resp["rows"][0]["days"]
            )
            self.assertAlmostEqual(
                tool["rows"][0]["metrics"]["amount"], endpoint_amount, delta=0.01
            )

    def test_inventory_summary_matches_endpoint(self):
        if not _inv_ready():
            self.skipTest("库存库为空，跳过同源断言")
        tool = ct.execute_tool("inventory_summary", {})
        resp = _CLIENT.get("/api/inventory/summary").json()
        if "error" in tool:
            self.skipTest(f"工具返回 error：{tool['error']}")
        # 逐数值键对比（工具只做了四舍五入，不允许出现端点没有的数字）
        for key, value in resp.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.assertAlmostEqual(
                    tool[key], value, delta=0.01, msg=f"KPI {key} 与端点不一致"
                )

    def test_restock_matches_endpoint(self):
        if not _inv_ready():
            self.skipTest("库存库为空，跳过同源断言")
        tool = ct.execute_tool("inventory_restock", {})
        if "error" in tool:
            self.skipTest(f"工具返回 error：{tool['error']}")
        resp = _CLIENT.get("/api/inventory/restock").json()
        self.assertEqual(tool["row_count"], len(resp["rows"]))
        if resp["rows"]:
            first = resp["rows"][0]
            tool_first = tool["rows"][0]
            self.assertEqual(tool_first.get("sku"), first.get("sku"))
            self.assertEqual(tool_first.get("qty"), first.get("qty"))
            self.assertEqual(tool_first.get("city"), first.get("city"))

    def test_empty_db_tools_return_error_not_crash(self):
        """工具对"查不到"的响应是 error 结果字典，绝不抛异常。

        用一个必不存在的 SPU 触发端点 404 路径（不需要空库）。
        """
        result = ct.execute_tool("pop_spu_analysis", {"spu": "999999999999999999"})
        self.assertIn("error", result)  # 404 detail 被转成模型可读结果
        self.assertNotIn("daily", result)


class TestOverviewAndPrompt(unittest.TestCase):
    """动态数据概览与系统提示词组装。"""

    def test_overview_always_returns_text(self):
        text = ct.build_data_overview()
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0)
        # 有库时含店铺/模块信息；空库时含"尚未摄入"——两种都是合法输出
        self.assertTrue(
            ("尚未摄入" in text) or ("POP" in text) or ("库存" in text)
        )

    def test_system_prompt_contains_boundary_and_overview(self):
        prompt = svc.build_system_prompt()
        self.assertIn("数据边界", prompt)
        self.assertIn("数据概览", prompt)
        self.assertIn("查询工具", prompt)
        # 概览段落确实嵌进了模板
        self.assertIn(ct.build_data_overview().splitlines()[0], prompt)


if __name__ == "__main__":
    unittest.main()
