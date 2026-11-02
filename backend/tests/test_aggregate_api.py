# -*- coding: utf-8 -*-
"""POP 区间汇总端点的接口级测试（stdlib unittest + fastapi TestClient，零新依赖）。

运行：仓库根 `python -m unittest backend.tests.test_aggregate_api -v`

被测接缝（公共 HTTP 接口）：
- GET /api/module/{id}/aggregate 的口径正确性：
  sum 类 = 逐日之和；ratio 类 = 分子分母总量相除（而非逐日比率平均）；
- 过滤链与 /rows 一致：同参数下 aggregate.total 与 rows 全量翻页求和相等；
- 降级行为：无命中 → counts=0 / total 为 null 而非报错；分母 0 → ratio null；
- 未知模块 404。

⚠️ 绝不在测试里硬编码日期（先例教训见 test_inventory_api.py）：
   日期一律动态取自 /api/manifest 的 date_range。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

MODULE = "pop_spu_detail"

# aggregate.total 中参与「与 rows 求和比对」的 sum 类字段
SUM_KEYS = [
    "visitors", "buyers", "orders", "items", "amount",
    "search_impressions", "search_clicks",
    "promotion_cost", "promotion_amount",
    "refund_orders", "refund_amount",
]
# ratio 类字段 -> (分子, 分母)，口径 = 先求和再相除
RATIOS = {
    "conversion_rate": ("buyers", "visitors"),
    "avg_price": ("amount", "buyers"),
    "search_click_rate": ("search_clicks", "search_impressions"),
    "roi": ("promotion_amount", "promotion_cost"),
    "promotion_ratio": ("promotion_cost", "amount"),
}


def _sum_field(rows: list[dict], key: str) -> float:
    """把 rows 接口返回的全部行（逐日 days）中某个原始字段求和；None 跳过。"""
    s = 0.0
    for r in rows:
        for d in r["days"]:
            v = d["metrics"].get(key)
            if v is not None:
                s += v
    return s


def _fetch_all_rows(client: TestClient, params: dict) -> list[dict]:
    """按 rows 接口全量翻页取回所有行（保证与 aggregate 比的是完整集合）。"""
    out: list[dict] = []
    page = 1
    while True:
        resp = client.get(f"/api/module/{MODULE}/rows", params={**params, "page": page, "page_size": 500})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        out.extend(data["rows"])
        if len(out) >= data["total"] or not data["rows"]:
            return out
        page += 1


class TestAggregateApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        manifest = cls.client.get("/api/manifest")
        if manifest.status_code == 404:
            raise unittest.SkipTest("库中无数据（未跑批处理），跳过聚合端点测试")
        mod = next(m for m in manifest.json()["modules"] if m["module_id"] == MODULE)
        cls.date_range = mod["date_range"]
        cls.shops = mod["shops"]
        # 动态区间：取库内最后 7 个自然日（不足 7 天就用全区间）
        lo, hi = cls.date_range
        from datetime import date as _d, timedelta

        hi_d = _d.fromisoformat(hi)
        lo_d = max(_d.fromisoformat(lo), hi_d - timedelta(days=6))
        cls.params = {"start": lo_d.isoformat(), "end": hi}

    # ---------- 口径正确 ----------
    def test_total_matches_rows_full_pagination(self):
        """同参数下 aggregate.total 的 sum 类字段 == rows 全量翻页逐日求和。"""
        agg = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params)
        self.assertEqual(agg.status_code, 200, agg.text)
        total = agg.json()["total"]
        rows = _fetch_all_rows(self.client, self.params)
        for key in SUM_KEYS:
            with self.subTest(metric=key):
                self.assertAlmostEqual(total[key] or 0.0, _sum_field(rows, key), places=4)

    def test_ratio_is_sum_then_divide(self):
        """ratio 类 = 分子分母总量相除；分母为 0/None 时必须为 null（绝不 Infinity）。"""
        total = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params).json()["total"]
        for key, (num, den) in RATIOS.items():
            with self.subTest(ratio=key):
                n, d = total.get(num), total.get(den)
                if not d:
                    self.assertIsNone(total[key])
                else:
                    self.assertAlmostEqual(total[key], n / d, places=9)

    def test_series_daily_amount_matches_rows(self):
        """series 逐日 amount == rows 该日全部行的 amount 之和；日期升序、无重复。"""
        body = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params).json()
        rows = _fetch_all_rows(self.client, self.params)
        per_day: dict[str, float] = {}
        for r in rows:
            for d in r["days"]:
                v = d["metrics"].get("amount")
                if v is not None:
                    per_day[d["date"]] = per_day.get(d["date"], 0.0) + v
        dates = [p["date"] for p in body["series"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), len(set(dates)))
        for p in body["series"]:
            if p["date"] in per_day:
                self.assertAlmostEqual(p["amount"] or 0.0, per_day[p["date"]], places=4)

    def test_counts_and_days(self):
        """counts = 命中店铺数 / (shop,spu) 对数；days = 请求区间自然天数。"""
        body = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params).json()
        rows = _fetch_all_rows(self.client, self.params)
        self.assertEqual(body["counts"]["shops"], len({r["shop"] for r in rows}))
        self.assertEqual(body["counts"]["spus"], len({(r["shop"], r["spu"]) for r in rows}))
        from datetime import date as _d

        expect_days = (_d.fromisoformat(self.params["end"]) - _d.fromisoformat(self.params["start"])).days + 1
        self.assertEqual(body["days"], expect_days)

    # ---------- 过滤链一致 ----------
    def test_shop_filter_same_as_rows(self):
        """带 shop 参数时 total 与该店 rows 求和一致（过滤链共用）。"""
        if not self.shops:
            self.skipTest("manifest 无店铺")
        params = {**self.params, "shop": self.shops[0]}
        total = self.client.get(f"/api/module/{MODULE}/aggregate", params=params).json()["total"]
        rows = _fetch_all_rows(self.client, params)
        self.assertTrue(rows, "首个店铺在动态区间内应有数据；若无则该库数据形态异常")
        for key in SUM_KEYS:
            with self.subTest(metric=key):
                self.assertAlmostEqual(total[key] or 0.0, _sum_field(rows, key), places=4)

    def test_keyword_filter_same_as_rows(self):
        """关键词过滤与 rows 同口径：取一个真实 SPU 号做精确搜索，两边命中一致。"""
        rows = _fetch_all_rows(self.client, self.params)
        if not rows:
            self.skipTest("动态区间内无数据")
        kw = rows[0]["spu"]
        params = {**self.params, "keyword": kw}
        body = self.client.get(f"/api/module/{MODULE}/aggregate", params=params).json()
        kw_rows = _fetch_all_rows(self.client, params)
        self.assertEqual(body["counts"]["spus"], len({(r["shop"], r["spu"]) for r in kw_rows}))
        self.assertAlmostEqual(body["total"]["amount"] or 0.0, _sum_field(kw_rows, "amount"), places=4)

    # ---------- 降级行为 ----------
    def test_no_match_returns_zero_counts_not_error(self):
        """无命中关键词：200 + counts=0 + total 全 null + series 空，而非 4xx/5xx。"""
        params = {**self.params, "keyword": "zzz-绝不存在的关键词-zzz"}
        resp = self.client.get(f"/api/module/{MODULE}/aggregate", params=params)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["counts"], {"shops": 0, "spus": 0})
        self.assertEqual(body["series"], [])
        self.assertIsNone(body["total"]["amount"])
        self.assertIsNone(body["total"]["roi"])

    # ---------- 环比 prev（上一个等长紧邻区间） ----------
    def test_prev_is_adjacent_equal_length_window(self):
        """prev 应等于「紧邻当前区间之前、等长」那段区间的 total（边界=等长 / 紧邻 / 不重叠）。"""
        from datetime import date as _d, timedelta

        cur = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params).json()
        days = cur["days"]
        cur_start = _d.fromisoformat(self.params["start"])
        prev_end = cur_start - timedelta(days=1)  # 紧邻：上一区间止于当前起点的昨天
        prev_start = prev_end - timedelta(days=days - 1)  # 等长
        # 直接对那段区间再打一次 aggregate 取 total 作外部基准（不依赖实现细节）
        ref = self.client.get(
            f"/api/module/{MODULE}/aggregate",
            params={"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        ).json()
        if ref["counts"]["spus"] == 0:
            self.assertIsNone(cur["prev"], "上一区间无数据时 prev 应整体为 null")
            return
        prev = cur["prev"]
        self.assertIsNotNone(prev)
        for key in [*SUM_KEYS, *RATIOS]:
            with self.subTest(field=key):
                a, b = prev.get(key), ref["total"].get(key)
                if a is None or b is None:
                    self.assertEqual(a, b)
                else:
                    self.assertAlmostEqual(a, b, places=6)

    def test_prev_null_when_prior_window_out_of_range(self):
        """当前区间贴着数据起点时，上一区间在数据之外 → prev 整体为 null（非报错 / 非零值）。"""
        from datetime import date as _d, timedelta

        lo = _d.fromisoformat(self.date_range[0])
        end = lo + timedelta(days=2)
        body = self.client.get(
            f"/api/module/{MODULE}/aggregate",
            params={"start": lo.isoformat(), "end": end.isoformat()},
        ).json()
        # 当前区间自身应有数据，确保 prev=null 是因为上期在范围外，而非整段无数据
        self.assertGreater(body["counts"]["spus"], 0, "首日窗口应命中数据")
        self.assertIsNone(body["prev"], "上一区间应在数据范围之外，prev 必须为 null")

    def test_prev_ratio_uses_sum_then_divide(self):
        """prev 的比率口径与 total 完全相同：分子分母总量相除，而非逐日比率取平均。"""
        cur = self.client.get(f"/api/module/{MODULE}/aggregate", params=self.params).json()
        prev = cur["prev"]
        if prev is None:
            self.skipTest("上一区间无数据，无法验证 prev 比率口径")
        for key, (num, den) in RATIOS.items():
            with self.subTest(ratio=key):
                n, d = prev.get(num), prev.get(den)
                if not d:
                    self.assertIsNone(prev[key])
                else:
                    self.assertAlmostEqual(prev[key], n / d, places=9)

    def test_prev_single_day_is_yesterday(self):
        """单日模式（start==end）：prev = 前一天的同口径 total（由等长公式自然得出）。"""
        from datetime import date as _d, timedelta

        hi = self.date_range[1]
        prev_d = _d.fromisoformat(hi) - timedelta(days=1)
        cur = self.client.get(
            f"/api/module/{MODULE}/aggregate", params={"start": hi, "end": hi}
        ).json()
        self.assertEqual(cur["days"], 1, "单日模式 days 应为 1")
        ref = self.client.get(
            f"/api/module/{MODULE}/aggregate",
            params={"start": prev_d.isoformat(), "end": prev_d.isoformat()},
        ).json()
        if ref["counts"]["spus"] == 0:
            self.skipTest("前一天无数据，跳过单日 prev 对照")
        prev = cur["prev"]
        self.assertIsNotNone(prev, "前一天有数据时单日 prev 不应为 null")
        for key in [*SUM_KEYS, *RATIOS]:
            with self.subTest(field=key):
                a, b = prev.get(key), ref["total"].get(key)
                self.assertEqual(a is None, b is None)
                if a is not None:
                    self.assertAlmostEqual(a, b, places=6)

    def test_prev_null_when_window_truncated_by_data_start(self):
        """上期窗口贴数据起点被截断（7 天里只有 1 天在数据内）→ prev=None，
        绝不返回「1 天当 7 天」的假 total（story #4：不显示错误值）。"""
        from datetime import date as _d, timedelta

        lo_d = _d.fromisoformat(self.date_range[0])
        # cur 窗口长 7 天从次日开始 → prev 窗口 [lo-6, lo] 只有 lo 那天落在数据内
        start = (lo_d + timedelta(days=1)).isoformat()
        end = (lo_d + timedelta(days=7)).isoformat()
        body = self.client.get(
            f"/api/module/{MODULE}/aggregate", params={"start": start, "end": end}
        ).json()
        self.assertEqual(body["days"], 7)
        self.assertGreater(body["counts"]["spus"], 0, "当前窗口应命中数据")
        self.assertIsNone(body["prev"], "上期窗口被数据起点截断时应为 null，避免假暴跌/暴涨")

    def test_unknown_module_404(self):
        resp = self.client.get("/api/module/no_such_module/aggregate", params=self.params)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
