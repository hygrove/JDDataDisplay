# -*- coding: utf-8 -*-
"""alert_rules 纯函数的单元测试（stdlib unittest，零依赖可跑）。

运行：backend 目录下 `python -m unittest tests.test_alert_rules -v`
（或仓库根 `python -m unittest backend.tests.test_alert_rules -v`）。

覆盖的边界（与 .scratch/restock-velocity-credibility 方案一一对应）：
- 三档判定矩阵：健康 / 在途 / 预警的边界值（= 阈值归属）
- 阈值 0 = 不检测（任何库存都算健康）
- 下柜判定：含「下」即排除，空值/None 不排除
- 可信销速三级估计器：L0 无依据 / L1 直接测得 / L2 收缩（突发封顶、
  参照分母垫底、可信度加权、参照缺失退化、零出库对称收缩、30 日窗自参照）；
  含失真用例（14 日窗 1 天有货出 66 件）
- 补货量：ceil / 非负 / 在途抵扣 / 安全系数 0 / 失真用例全链
- 参数夹取：days/safety/window 非法回默认
"""
from __future__ import annotations

import unittest

from jobs.alert_rules import (
    ALLOWED_WINDOWS,
    DEFAULT_DAYS,
    DEFAULT_SAFETY,
    DEFAULT_WINDOW,
    VELOCITY_MEASURED,
    VELOCITY_NONE,
    VELOCITY_SHRUNK,
    alert_status,
    clamp_params,
    credible_velocity,
    is_offshelf,
    reference_velocity,
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


class TestReferenceVelocity(unittest.TestCase):
    """参照销速：分母垫底，防参照窗自己把积压爆发当常态。"""

    def test_normal_reference_no_floor(self):
        # 30 日窗 20 天有货出 100：20 > 半窗 15，不触发垫底 → 100/20 = 5
        self.assertEqual(reference_velocity(100, 20, 30), 5.0)

    def test_floor_denominator_when_ref_always_stockout(self):
        # 参照窗也长期断货（仅 3 天有货）：分母垫到半窗 15 → 60/15 = 4（不是 60/3 = 20）
        self.assertEqual(reference_velocity(60, 3, 30), 4.0)

    def test_ref_avail_zero_falls_to_thin_floor(self):
        # 源数据矛盾（有出库却记 0 天有货）：退摊薄下界 60/30 = 2
        self.assertEqual(reference_velocity(60, 0, 30), 2.0)

    def test_ref_no_data_is_zero(self):
        self.assertEqual(reference_velocity(0, 0, 30), 0.0)


class TestCredibleVelocity(unittest.TestCase):
    """可信销速三级估计器。"""

    def test_measured_when_avail_enough(self):
        # 有货 14 天 ≥ 门槛 7 → 直接测得，不掺参照
        self.assertEqual(credible_velocity(140, 14), (10.0, VELOCITY_MEASURED))

    def test_measured_at_cred_boundary(self):
        # avail = 7 恰好达到可信门槛 → 直接测得（≥ 语义的边界归属）
        self.assertEqual(credible_velocity(70, 7), (10.0, VELOCITY_MEASURED))

    def test_none_when_no_stock_days(self):
        # 一直缺货（有货天数 0/负）→ 无销速依据 → 0
        self.assertEqual(credible_velocity(100, 0), (0.0, VELOCITY_NONE))
        self.assertEqual(credible_velocity(100, -3), (0.0, VELOCITY_NONE))

    def test_shrunk_distorted_case(self):
        # 失真用例（SKU 100311879912 形态）：14 日窗 1 天有货出 66 件，
        # 30 日参照出 80 / 有货 5 → v_ref = 80/15，封顶 min(66, 16) = 16，
        # w = 1/7 → v = 48/7 ≈ 6.86（直算 66 会把建议量顶到 700+）
        v, kind = credible_velocity(66, 1, 80, 5)
        self.assertEqual(kind, VELOCITY_SHRUNK)
        self.assertAlmostEqual(v, 48 / 7, places=9)

    def test_shrunk_burst_cap_not_binding(self):
        # 短窗销速本就低于封顶线（v_short=6 < v_ref×3=30）：不砍，只按可信度加权
        # v = 5/7×6 + 2/7×10 = 50/7
        v, kind = credible_velocity(30, 5, 300, 30)
        self.assertEqual(kind, VELOCITY_SHRUNK)
        self.assertAlmostEqual(v, 50 / 7, places=9)

    def test_shrunk_no_reference_degrades_to_weighted(self):
        # 新品无 30 日历史（参照 0）：跳过封顶，退化为可信度加权 66/7 ≈ 9.43
        v, kind = credible_velocity(66, 1, 0, 0)
        self.assertEqual(kind, VELOCITY_SHRUNK)
        self.assertAlmostEqual(v, 66 / 7, places=9)

    def test_shrunk_zero_out_pulls_up_to_reference(self):
        # 对称收缩（有意为之，见 ADR-0010 后果一节）：窗口零出库但仅 3 天样本
        # → 证据太弱，收缩后仍主要信参照。v = 3/7×0 + 4/7×7 = 4
        v, kind = credible_velocity(0, 3, 210, 30)
        self.assertEqual(kind, VELOCITY_SHRUNK)
        self.assertAlmostEqual(v, 4.0, places=9)

    def test_shrunk_self_reference_30_window(self):
        # 窗口选 30 日时参照 = 自身（分母垫底后）：出 66 / 1 天有货 →
        # v_ref = 66/15，封顶 min(66, 66/5) = 13.2，w = 1/7 → v = 39.6/7 ≈ 5.66
        v, kind = credible_velocity(66, 1, 66, 1)
        self.assertEqual(kind, VELOCITY_SHRUNK)
        self.assertAlmostEqual(v, 39.6 / 7, places=9)


class TestRestockQty(unittest.TestCase):
    """补货量：目标库存 − 现货 − 在途，ceil / 非负（销速由 credible_velocity 算得后传入）。"""

    def test_typical(self):
        # 日销 10 × 9 天 × 1.2 = 108 目标；现货 42、在途 20 → 补 46
        self.assertEqual(restock_qty(10.0, 9, 0.2, 42, 20), 46)

    def test_target_is_integer_ceiling(self):
        # 日销 10 × 9 × 1.2 = 108（恰为整数）；现货 0 在途 0 → 补 108
        self.assertEqual(restock_qty(10.0, 9, 0.2, 0, 0), 108)

    def test_fractional_target_rounds_up(self):
        # 日销 1 × 9 × 1.2 = 10.8 → ceil 11
        self.assertEqual(restock_qty(1.0, 9, 0.2, 0, 0), 11)

    def test_already_covered_is_zero(self):
        self.assertEqual(restock_qty(1.0, 9, 0.2, 100, 50), 0)

    def test_zero_velocity_no_suggestion(self):
        # 销速 0（L0 无依据）→ 目标 0 → 建议量 0（缺货新品不盲目建议）
        self.assertEqual(restock_qty(0.0, 9, 0.2, 0, 0), 0)

    def test_purchase_counts_as_coverage(self):
        # 在途抵扣目标：现货 50 在途 100 目标 108 → 补 0
        self.assertEqual(restock_qty(10.0, 9, 0.2, 50, 100), 0)

    def test_zero_safety(self):
        # 安全系数 0：目标 = 日销 × days
        self.assertEqual(restock_qty(10.0, 9, 0, 0, 0), 90)

    def test_distorted_sku_end_to_end(self):
        # 失真用例全链：收缩销速 48/7 × 9 × 1.2 ≈ 74.06 → ceil 75
        # （直算销速 66 × 9 × 1.2 = 713——回归前的失真值）
        v, _kind = credible_velocity(66, 1, 80, 5)
        self.assertEqual(restock_qty(v, 9, 0.2, 0, 0), 75)


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
