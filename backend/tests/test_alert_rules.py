# -*- coding: utf-8 -*-
"""alert_rules 纯函数的单元测试（stdlib unittest，零依赖可跑）。

运行：backend 目录下 `python -m unittest tests.test_alert_rules -v`
（或仓库根 `python -m unittest backend.tests.test_alert_rules -v`）。

覆盖的边界（与 .scratch 计划方案 4.1/4.2 一一对应）：
- 三档判定矩阵：健康 / 在途 / 预警的边界值（= 阈值归属）
- 阈值 0 = 不检测（任何库存都算健康）
- 下柜判定：含「下」即排除，空值/None 不排除
- avail = 0 → 销速 0 → 不建议
- ceil 边界：目标库存恰为整数 / 带小数
- 参数夹取：days/safety/window 非法回默认
"""
from __future__ import annotations

import unittest

from jobs.alert_rules import (
    ALLOWED_WINDOWS,
    DEFAULT_DAYS,
    DEFAULT_SAFETY,
    DEFAULT_WINDOW,
    alert_status,
    clamp_params,
    effective_velocity,
    is_offshelf,
    restock_qty,
)

from jobs.alert_rules import ALERT, HEALTHY, INCOMING


class TestAlertStatus(unittest.TestCase):
    """三档判定：阈值边界值归属。"""

    def test_healthy_when_stock_at_threshold(self):
        # 现货 = 阈值 → 健康（≥ 语义的边界归属）
        self.assertEqual(alert_status(10, 10, 0), HEALTHY)

    def test_healthy_when_stock_above(self):
        self.assertEqual(alert_status(10, 11, 0), HEALTHY)

    def test_incoming_when_purchase_covers_gap(self):
        # 现货 < 阈值，但现货 + 在途 = 阈值 → 在途（≥ 语义的边界归属）
        self.assertEqual(alert_status(10, 6, 4), INCOMING)

    def test_incoming_when_purchase_exceeds(self):
        self.assertEqual(alert_status(10, 6, 5), INCOMING)

    def test_alert_when_purchase_insufficient(self):
        # 现货 + 在途 < 阈值 → 预警
        self.assertEqual(alert_status(10, 6, 3), ALERT)

    def test_alert_when_no_purchase(self):
        self.assertEqual(alert_status(10, 6, 0), ALERT)

    def test_zero_threshold_disables_detection(self):
        # 阈值 0 = 该城不检测：现货 0 也算健康（0 ≥ 0）
        self.assertEqual(alert_status(0, 0, 0), HEALTHY)
        self.assertEqual(alert_status(0, 5, 0), HEALTHY)


class TestOffshelf(unittest.TestCase):
    """下柜判定：与前端 includes("下") 口径一致。"""

    def test_offshelf_variants(self):
        self.assertTrue(is_offshelf("下柜"))
        self.assertTrue(is_offshelf("已下柜"))

    def test_onsale_and_empty_not_offshelf(self):
        self.assertFalse(is_offshelf("上柜"))
        self.assertFalse(is_offshelf(""))
        self.assertFalse(is_offshelf(None))


class TestVelocity(unittest.TestCase):
    """有效日均销速：avail 修正。"""

    def test_velocity_normal(self):
        self.assertEqual(effective_velocity(140, 14), 10.0)

    def test_velocity_avail_partial(self):
        # 缺货期不计入分母：14 天卖了 70 件但只有 7 天有货 → 日销 10
        self.assertEqual(effective_velocity(70, 7), 10.0)

    def test_velocity_zero_avail(self):
        # 一直缺货（有货天数 0）→ 无销速依据 → 0
        self.assertEqual(effective_velocity(100, 0), 0.0)

    def test_velocity_negative_avail(self):
        self.assertEqual(effective_velocity(100, -3), 0.0)


class TestRestockQty(unittest.TestCase):
    """补货量：目标库存 − 现货 − 在途，ceil / 非负。"""

    def test_typical(self):
        # 日销 10 × 9 天 × 1.2 = 108 目标；现货 42、在途 20 → 补 46
        self.assertEqual(restock_qty(140, 14, 9, 0.2, 42, 20), 46)

    def test_target_is_integer_ceiling(self):
        # 日销 10 × 9 × 1.2 = 108（恰为整数）；现货 0 在途 0 → 补 108
        self.assertEqual(restock_qty(140, 14, 9, 0.2, 0, 0), 108)

    def test_fractional_target_rounds_up(self):
        # 日销 1 × 9 × 1.2 = 10.8 → ceil 11
        self.assertEqual(restock_qty(14, 14, 9, 0.2, 0, 0), 11)

    def test_already_covered_is_zero(self):
        self.assertEqual(restock_qty(14, 14, 9, 0.2, 100, 50), 0)

    def test_no_velocity_no_suggestion(self):
        # avail = 0 → 销速 0 → 目标 0 → 建议量 0（缺货新品不盲目建议）
        self.assertEqual(restock_qty(0, 0, 9, 0.2, 0, 0), 0)

    def test_purchase_counts_as_coverage(self):
        # 在途抵扣目标：现货 50 在途 100 目标 108 → 补 0
        self.assertEqual(restock_qty(140, 14, 9, 0.2, 50, 100), 0)

    def test_zero_safety(self):
        # 安全系数 0：目标 = 日销 × days
        self.assertEqual(restock_qty(140, 14, 9, 0, 0, 0), 90)


class TestClampParams(unittest.TestCase):
    """参数夹取：非法/缺失回默认，不抛错。"""

    def test_defaults(self):
        d, s, w = clamp_params(None, None, None)
        self.assertEqual((d, s, w), (DEFAULT_DAYS, DEFAULT_SAFETY, DEFAULT_WINDOW))

    def test_valid_passthrough(self):
        d, s, w = clamp_params(12, 0.5, 30)
        self.assertEqual((d, s, w), (12.0, 0.5, 30))

    def test_window_out_of_whitelist(self):
        # 非白名单窗口（如 13）回默认 14
        _, _, w = clamp_params(9, 0.2, 13)
        self.assertEqual(w, DEFAULT_WINDOW)
        self.assertIn(w, ALLOWED_WINDOWS)

    def test_days_out_of_range(self):
        d, _, _ = clamp_params(999, 0.2, 14)
        self.assertEqual(d, DEFAULT_DAYS)
        d, _, _ = clamp_params(0, 0.2, 14)
        self.assertEqual(d, DEFAULT_DAYS)

    def test_safety_out_of_range(self):
        _, s, _ = clamp_params(9, 6, 14)
        self.assertEqual(s, DEFAULT_SAFETY)
        _, s, _ = clamp_params(9, -0.5, 14)
        self.assertEqual(s, DEFAULT_SAFETY)

    def test_garbage_strings_fall_back(self):
        d, s, w = clamp_params("abc", "x", "y")
        self.assertEqual((d, s, w), (DEFAULT_DAYS, DEFAULT_SAFETY, DEFAULT_WINDOW))


if __name__ == "__main__":
    unittest.main()
