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

    def test_unknown_module_404(self):
        resp = self.client.get("/api/module/no_such_module/aggregate", params=self.params)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
