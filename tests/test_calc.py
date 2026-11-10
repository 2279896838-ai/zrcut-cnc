"""计算引擎单元测试。

覆盖《提示词工程规范》3.6 节的每一条公式，并验证：
    - 正算与反算互为逆运算
    - 边界与非法输入的处理
    - compute_all 与单项函数结果一致
"""

from __future__ import annotations

import math

from tests import ZrCutTestCase
from zrcut import calc
from zrcut.domain import MACHINE, get_material, get_tool


class TestSpindleSpeed(ZrCutTestCase):
    """主轴转速公式 n = 1000·vc/(π·D)。"""

    def test_basic(self) -> None:
        """M1+T3 标准算例。"""
        got = calc.spindle_speed(168.0, 16.0)
        self.assertAlmostEqualRel(got, 3342.253, rel=1e-4)

    def test_matches_manual_formula(self) -> None:
        """与手算一致。"""
        vc, d = 180.0, 20.0
        self.assertAlmostEqualRel(calc.spindle_speed(vc, d), 1000 * vc / (math.pi * d), 1e-9)

    def test_inverse_of_cutting_speed(self) -> None:
        """与 cutting_speed_from_rpm 互逆。"""
        vc, d = 150.0, 12.0
        n = calc.spindle_speed(vc, d)
        self.assertAlmostEqualRel(calc.cutting_speed_from_rpm(n, d), vc, 1e-9)

    def test_rejects_zero_diameter(self) -> None:
        """直径为 0 必须报错。"""
        with self.assertRaises(calc.CalculationError):
            calc.spindle_speed(100.0, 0.0)

    def test_rejects_negative_vc(self) -> None:
        """负切削速度必须报错。"""
        with self.assertRaises(calc.CalculationError):
            calc.spindle_speed(-10.0, 16.0)


class TestFeedRate(ZrCutTestCase):
    """进给速度 vf = fz·z·n。"""

    def test_basic(self) -> None:
        n = calc.spindle_speed(168.0, 16.0)
        self.assertAlmostEqualRel(calc.feed_rate(0.12, 4, n), 1604.28, rel=1e-4)

    def test_proportional_to_fz(self) -> None:
        """vf 与 fz 成正比。"""
        a = calc.feed_rate(0.10, 4, 3000.0)
        b = calc.feed_rate(0.20, 4, 3000.0)
        self.assertAlmostEqualRel(b / a, 2.0, 1e-9)

    def test_rejects_zero_teeth(self) -> None:
        with self.assertRaises(calc.CalculationError):
            calc.feed_rate(0.1, 0, 3000.0)


class TestMaterialRemovalRate(ZrCutTestCase):
    """材料去除率 MRR = ap·ae·vf。"""

    def test_basic(self) -> None:
        self.assertAlmostEqualRel(calc.material_removal_rate(8.0, 4.0, 1604.28), 51336.96, rel=1e-6)

    def test_rejects_zero_ap(self) -> None:
        with self.assertRaises(calc.CalculationError):
            calc.material_removal_rate(0.0, 4.0, 1000.0)


class TestCuttingPower(ZrCutTestCase):
    """切削功率 Pc = MRR·Kc/(60×10⁶)。"""

    def test_basic_m1(self) -> None:
        mrr = 51336.96
        self.assertAlmostEqualRel(calc.cutting_power(mrr, 2000.0), 1.7112, rel=1e-3)

    def test_power_limit_case(self) -> None:
        """M1+T4 越限算例：Pc 应超过 11 kW。"""
        mrr = 20.0 * 14.0 * 1375.1
        pc = calc.cutting_power(mrr, 2000.0)
        self.assertGreater(pc, MACHINE.power_continuous_kw)
        self.assertAlmostEqualRel(pc, 12.834, rel=1e-3)

    def test_low_power_face_milling(self) -> None:
        """M4+T6 面铣为低功率工况，不得误判为越限。"""
        n = calc.spindle_speed(150.0, 80.0)
        vf = calc.feed_rate(0.12, 5, n)
        mrr = calc.material_removal_rate(2.0, 40.0, vf)
        pc = calc.cutting_power(mrr, 1500.0)
        self.assertLess(pc, 1.0)
        self.assertAlmostEqualRel(pc, 0.716, rel=0.02)


class TestRoughness(ZrCutTestCase):
    """表面粗糙度公式。"""

    def test_end_mill_formula(self) -> None:
        """侧铣 Ra_th = fz²/(32·rε)×1000。"""
        self.assertAlmostEqualRel(calc.theoretical_roughness(0.12, 0.8), 0.5625, 1e-6)

    def test_ball_mill_formula(self) -> None:
        """球头 Ra_th = fz²/(8·R)×1000。"""
        self.assertAlmostEqualRel(
            calc.theoretical_roughness(0.05, 5.0, is_ball=True), 0.0625, 1e-6
        )

    def test_ball_and_end_differ_by_factor_4(self) -> None:
        """球头与端铣分母比 8:32，结果差 4 倍。"""
        a = calc.theoretical_roughness(0.08, 2.0, is_ball=False)
        b = calc.theoretical_roughness(0.08, 2.0, is_ball=True)
        self.assertAlmostEqualRel(b / a, 4.0, 1e-9)

    def test_predicted_applies_kra(self) -> None:
        ra_th = calc.theoretical_roughness(0.12, 0.8)
        self.assertAlmostEqualRel(calc.predicted_roughness(ra_th, 1.6), 0.9, 1e-6)

    def test_max_fz_inverse(self) -> None:
        """反解 fz 上限后代入应恰好达到 Ra 要求。"""
        fz = calc.max_fz_for_roughness(1.6, 0.8, 1.6)
        ra_th = calc.theoretical_roughness(fz, 0.8)
        self.assertAlmostEqualRel(calc.predicted_roughness(ra_th, 1.6), 1.6, 1e-6)


class TestToolLife(ZrCutTestCase):
    """Taylor 寿命 T = (C/vc)^(1/n)。"""

    def test_basic_m1(self) -> None:
        m = get_material("M1")
        self.assertAlmostEqualRel(
            calc.tool_life(168.0, m.taylor_c, m.taylor_n), 18.84, rel=1e-3
        )

    def test_lower_vc_longer_life(self) -> None:
        """降低 vc 必然延长寿命。"""
        m = get_material("M1")
        a = calc.tool_life(180.0, m.taylor_c, m.taylor_n)
        b = calc.tool_life(120.0, m.taylor_c, m.taylor_n)
        self.assertGreater(b, a)

    def test_tic4_short_life(self) -> None:
        """钛合金寿命显著短于钢件。"""
        m5 = get_material("M5")
        m1 = get_material("M1")
        t5 = calc.tool_life(45.0, m5.taylor_c, m5.taylor_n)
        t1 = calc.tool_life(45.0, m1.taylor_c, m1.taylor_n)
        self.assertAlmostEqualRel(t5, 24.45, rel=0.01)
        self.assertLess(t5, t1)


class TestMachiningTime(ZrCutTestCase):
    """加工时间 Tm = V/(ap·ae·vf)。"""

    def test_basic(self) -> None:
        self.assertAlmostEqualRel(calc.machining_time(288000.0, 8.0, 4.0, 1604.28), 5.611, rel=1e-3)

    def test_against_result_variant(self) -> None:
        """两个入口结果一致。"""
        m = get_material("M1")
        t = get_tool("T3")
        res = calc.compute_all(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0)
        a = calc.machining_time(288000.0, 8.0, 4.0, res.vf)
        b = calc.machining_time_for(288000.0, res)
        self.assertAlmostEqualRel(a, b, 1e-9)


class TestComputeAll(ZrCutTestCase):
    """compute_all 汇总一致性。"""

    def test_consistency_with_individual(self) -> None:
        """汇总结果与单项函数完全一致。"""
        m = get_material("M1")
        t = get_tool("T3")
        res = calc.compute_all(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0)
        n = calc.spindle_speed(168, 16.0)
        vf = calc.feed_rate(0.12, 4, n)
        mrr = calc.material_removal_rate(8.0, 4.0, vf)
        self.assertAlmostEqualRel(res.n, n, 1e-9)
        self.assertAlmostEqualRel(res.vf, vf, 1e-9)
        self.assertAlmostEqualRel(res.mrr, mrr, 1e-9)
        self.assertAlmostEqualRel(res.pc, calc.cutting_power(mrr, m.kc), 1e-9)
        self.assertAlmostEqualRel(res.ra_th, calc.theoretical_roughness(0.12, 0.8), 1e-9)
        self.assertAlmostEqualRel(res.ra_pred, res.ra_th * m.k_ra, 1e-9)

    def test_overhang_ratio(self) -> None:
        """悬伸比等于 L/D。"""
        t = get_tool("T3")
        res = calc.compute_all(get_material("M1"), t, vc=168, fz=0.12, ap=8.0, ae=4.0)
        self.assertAlmostEqualRel(res.overhang_ratio, 55.0 / 16.0, 1e-9)

    def test_tooth_pass_frequency(self) -> None:
        """齿通过频率 f_t = z·n/60。"""
        m = get_material("M1")
        t = get_tool("T3")
        res = calc.compute_all(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0)
        self.assertAlmostEqualRel(res.tooth_pass_hz, 4 * res.n / 60.0, 1e-9)


class TestBounds(ZrCutTestCase):
    """切深与切宽上限计算。"""

    def test_ap_bound_basic(self) -> None:
        self.assertAlmostEqualRel(calc.ap_upper_bound(get_material("M1"), get_tool("T3")), 16.0, 1e-9)

    def test_ap_bound_long_overhang(self) -> None:
        """T5 悬伸比 5.0，落入折减区间，ap 上限打 0.7 折。"""
        m = get_material("M1")
        t = get_tool("T5")
        base = m.ap_d_max * t.diameter
        self.assertAlmostEqualRel(calc.ap_upper_bound(m, t), base * 0.7, 1e-9)

    def test_ap_bound_thin_wall(self) -> None:
        """薄壁件再打 0.5 折。"""
        m = get_material("M1")
        t = get_tool("T3")
        self.assertAlmostEqualRel(
            calc.ap_upper_bound(m, t, 2.0), 16.0 * 0.5, 1e-9
        )

    def test_ae_bound(self) -> None:
        self.assertAlmostEqualRel(calc.ae_upper_bound(get_material("M1"), get_tool("T3")), 8.0, 1e-9)
