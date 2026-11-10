"""约束校验器与收敛算法测试。

覆盖 C-01..C-10 每条约束的正负例、颤振三级判定，
以及收敛算法在多种越限形态下的行为。
"""

from __future__ import annotations

from tests import ZrCutTestCase
from zrcut import calc
from zrcut.constraints import (
    CheckId,
    ValidationContext,
    _diagnose,
    classify_chatter,
    converge,
    validate,
)
from zrcut.domain import ChatterRisk, ToolId, get_material, get_tool


def _ctx(ra: float = 1.6, **kw) -> ValidationContext:
    """构造默认校验上下文。"""
    return ValidationContext(ra_req=ra, **kw)


def _by_id(results, cid: CheckId):
    """按编号取判定结果。"""
    return next(r for r in results if r.check_id is cid)


class TestConstraintBasics(ZrCutTestCase):
    """十条约束的正例与结构完整性。"""

    def test_returns_all_ten_checks(self) -> None:
        """必须返回且仅返回 C-01..C-10 十条。"""
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertEqual(len(res), 10)
        self.assertEqual(
            [r.check_id.value for r in res], [f"C-{i:02d}" for i in range(1, 11)]
        )

    def test_standard_case_all_pass(self) -> None:
        """M1+T3 标准算例应全部通过。"""
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        failed = [r.check_id.value for r in res if r.applicable and not r.passed]
        self.assertEqual(failed, [])

    def test_each_result_has_required_fields(self) -> None:
        """每条结果必须含 actual、limit、detail。"""
        m, t = get_material("M1"), get_tool("T3")
        for r in validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx()):
            self.assertIsNotNone(r.actual)
            self.assertIsNotNone(r.limit)
            self.assertTrue(r.detail)
            self.assertTrue(r.constraint)


class TestC01SpindleLimit(ZrCutTestCase):
    """C-01 主轴转速上限。"""

    def test_detects_over_speed(self) -> None:
        """超高转速必须失败。"""
        m, t = get_material("M2"), get_tool("T1")
        res = validate(m, t, vc=800.0, fz=0.1, ap=5.0, ae=3.0, ctx=_ctx(3.2))
        self.assertFalse(_by_id(res, CheckId.C01).passed)

    def test_passes_normal_speed(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertTrue(_by_id(res, CheckId.C01).passed)


class TestC03Power(ZrCutTestCase):
    """C-03 主轴功率。"""

    def test_detects_power_overrun(self) -> None:
        """M1+T4 大切宽大切深应超 11 kW。"""
        m, t = get_material("M1"), get_tool("T4")
        res = validate(m, t, vc=180, fz=0.12, ap=20.0, ae=14.0, ctx=_ctx())
        c03 = _by_id(res, CheckId.C03)
        self.assertFalse(c03.passed)
        self.assertAlmostEqualRel(c03.actual, 12.834, rel=0.01)

    def test_face_milling_not_overrun(self) -> None:
        """M4+T6 面铣功率宽裕，C-03 必须通过。

        这是对「大直径刀具必然高功率」直觉的反例：
        D=80、z=5 时 n 仅 596.8 r/min，vf 仅 358.1 mm/min，Pc 不足 1 kW。
        """
        m, t = get_material("M4"), get_tool("T6")
        res = validate(m, t, vc=150, fz=0.12, ap=2.0, ae=40.0, ctx=_ctx(3.2))
        c03 = _by_id(res, CheckId.C03)
        self.assertTrue(c03.passed)
        self.assertLess(c03.actual, 1.0)


class TestC04Overhang(ZrCutTestCase):
    """C-04 悬伸刚性。"""

    def test_passes_short_overhang(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertTrue(_by_id(res, CheckId.C04).passed)

    def test_t5_overhang_in_warn_band(self) -> None:
        """T5 悬伸比 5.0，处于 4-6 警戒区，应提示折减但仍通过。"""
        m, t = get_material("M1"), get_tool("T5")
        res = validate(m, t, vc=120, fz=0.1, ap=2.0, ae=2.0, ctx=_ctx())
        c04 = _by_id(res, CheckId.C04)
        self.assertTrue(c04.passed)
        self.assertAlmostEqualRel(c04.actual, 5.0, 1e-9)


class TestC05Band(ZrCutTestCase):
    """C-05 材料参数区间。"""

    def test_detects_vc_above_band(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=300.0, fz=0.1, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertFalse(_by_id(res, CheckId.C05).passed)

    def test_detects_fz_above_band(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168.0, fz=0.5, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertFalse(_by_id(res, CheckId.C05).passed)


class TestC06DepthWidth(ZrCutTestCase):
    """C-06 切深切宽。"""

    def test_detects_ae_over(self) -> None:
        """ae/D 超过材料上限必须失败。"""
        m, t = get_material("M1"), get_tool("T4")
        res = validate(m, t, vc=180, fz=0.12, ap=20.0, ae=14.0, ctx=_ctx())
        self.assertFalse(_by_id(res, CheckId.C06).passed)

    def test_overhang_reduces_ap_limit(self) -> None:
        """T5 悬伸折减后，相同的 ap/D 更易超限。"""
        m, t = get_material("M1"), get_tool("T5")
        res = validate(m, t, vc=120, fz=0.08, ap=3.0, ae=2.0, ctx=_ctx())
        c06 = _by_id(res, CheckId.C06)
        self.assertIn("0.7", c06.limit)


class TestC07Chatter(ZrCutTestCase):
    """C-07 颤振风险分级。"""

    def test_low_when_in_stable_band(self) -> None:
        """转速落在稳定带且 ae/D≤0.5 应判 LOW。"""
        t = get_tool("T3")
        risk, why = classify_chatter(t, 2400.0, ae=6.0, ap=8.0)
        self.assertIs(risk, ChatterRisk.LOW)
        self.assertIn("稳定带", why)

    def test_low_when_small_engagement(self) -> None:
        """小切宽低速切削振动能量低，应判 LOW。"""
        t = get_tool("T1")
        risk, _ = classify_chatter(t, 1432.4, ae=0.3, ap=1.0)
        self.assertIs(risk, ChatterRisk.LOW)

    def test_high_when_wide_cut_off_band(self) -> None:
        """不在稳定带且 ae/D>0.5 应判 HIGH。"""
        t = get_tool("T4")
        risk, why = classify_chatter(t, 2864.8, ae=14.0, ap=17.0)
        self.assertIs(risk, ChatterRisk.HIGH)
        self.assertIn("ae/D", why)

    def test_high_contains_limitation_notice(self) -> None:
        """HIGH 判定必须带简化模型局限性声明。"""
        t = get_tool("T4")
        _, why = classify_chatter(t, 2864.8, ae=14.0, ap=17.0)
        self.assertIn("单自由度简化叶瓣模型", why)

    def test_medium_between(self) -> None:
        """转速不在带内但切宽中等，应判 MEDIUM。"""
        t = get_tool("T3")
        risk, _ = classify_chatter(t, 3400.0, ae=6.0, ap=7.0)
        self.assertIs(risk, ChatterRisk.MEDIUM)


class TestC08Roughness(ZrCutTestCase):
    """C-08 质量达成。"""

    def test_passes_with_margin(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx(1.6))
        self.assertTrue(_by_id(res, CheckId.C08).passed)

    def test_fails_when_requirement_strict(self) -> None:
        """Ra 要求过严必须失败。"""
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx(0.1))
        self.assertFalse(_by_id(res, CheckId.C08).passed)


class TestC09Coolant(ZrCutTestCase):
    """C-09 冷却适配。"""

    def test_m3_without_coolant_fails(self) -> None:
        m, t = get_material("M3"), get_tool("T1")
        res = validate(
            m, t, vc=80, fz=0.06, ap=2.0, ae=2.0,
            ctx=_ctx(0.8, coolant_available=False),
        )
        c09 = _by_id(res, CheckId.C09)
        self.assertTrue(c09.applicable)
        self.assertFalse(c09.passed)

    def test_m1_not_applicable(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        c09 = _by_id(res, CheckId.C09)
        self.assertFalse(c09.applicable)
        self.assertTrue(c09.passed)


class TestC10ThinWall(ZrCutTestCase):
    """C-10 薄壁装夹。"""

    def test_thin_wall_detects_over_depth(self) -> None:
        """薄壁件切深超过折减上限必须失败。

        M1+T3 无悬伸折减，薄壁折减后 ap 上限 = 1.0×16×0.5 = 8.0mm。
        """
        m, t = get_material("M1"), get_tool("T3")
        res = validate(
            m, t, vc=168, fz=0.12, ap=10.0, ae=4.0,
            ctx=_ctx(1.6, wall_thickness=2.0),
        )
        c10 = _by_id(res, CheckId.C10)
        self.assertTrue(c10.applicable)
        self.assertFalse(c10.passed)
        self.assertAlmostEqualRel(c10.limit, 8.0, 1e-9)

    def test_thin_wall_passes_at_boundary(self) -> None:
        """切深恰等于折减上限时按硬约束语义判通过。"""
        m, t = get_material("M1"), get_tool("T3")
        res = validate(
            m, t, vc=168, fz=0.12, ap=8.0, ae=4.0,
            ctx=_ctx(1.6, wall_thickness=2.0),
        )
        c10 = _by_id(res, CheckId.C10)
        self.assertTrue(c10.applicable)
        self.assertTrue(c10.passed)

    def test_thin_wall_limit_consistent_with_c06(self) -> None:
        """C-10 与 C-06 必须使用同一折减口径，不得出现两套上限。"""
        for tid in (ToolId.T3, ToolId.T5, ToolId.T4):
            for wall in (1.0, 2.0, 2.5):
                with self.subTest(tool=tid.value, wall=wall):
                    m, t = get_material("M1"), get_tool(tid)
                    res = validate(
                        m, t, vc=120, fz=0.08, ap=1.0, ae=1.0,
                        ctx=_ctx(1.6, wall_thickness=wall),
                    )
                    c10 = _by_id(res, CheckId.C10)
                    expected = calc.ap_upper_bound(m, t, wall)
                    self.assertAlmostEqualRel(c10.limit, expected, 1e-9)

    def test_thick_wall_not_applicable(self) -> None:
        m, t = get_material("M1"), get_tool("T3")
        res = validate(
            m, t, vc=168, fz=0.12, ap=8.0, ae=4.0,
            ctx=_ctx(1.6, wall_thickness=20.0),
        )
        self.assertFalse(_by_id(res, CheckId.C10).applicable)

    def test_thin_wall_combines_overhang_reduction(self) -> None:
        """T5 悬伸比 5.0 与薄壁折减应叠加，而非只取其一。"""
        m, t = get_material("M1"), get_tool("T5")
        res = validate(
            m, t, vc=120, fz=0.05, ap=0.5, ae=1.0,
            ctx=_ctx(1.6, wall_thickness=2.0),
        )
        c10 = _by_id(res, CheckId.C10)
        # 1.0×10×0.7(悬伸)×0.5(薄壁) = 3.5
        self.assertAlmostEqualRel(c10.limit, 3.5, 1e-9)


class TestConvergence(ZrCutTestCase):
    """收敛算法行为。"""

    def test_power_overrun_converges(self) -> None:
        """M1+T4 功率与颤振同时越限，应收敛到全部通过。"""
        m, t = get_material("M1"), get_tool("T4")
        rep = converge(m, t, vc=180, fz=0.12, ap=20.0, ae=14.0, ctx=_ctx())
        self.assertTrue(rep.converged, f"未收敛，历史：{rep.history}")
        p = rep.final_params
        res = calc.compute_all(m, t, vc=p["vc"], fz=p["fz"], ap=p["ap"], ae=p["ae"])
        self.assertLessEqual(res.pc, 11.0)

    def test_reduces_ae_before_vc_for_chatter(self) -> None:
        """颤振 HIGH 由 ae/D 引起时必须降 ae 而非降 ap。"""
        m, t = get_material("M1"), get_tool("T4")
        need = _diagnose(m, t, vc=180, fz=0.12, ap=17.0, ae=14.0, ctx=_ctx())
        self.assertIn("ae", need)
        self.assertNotIn("ap", need)

    def test_converges_aluminum_small_tool(self) -> None:
        """铝合金配 φ10 刀具：主轴极限迫使 vc 低于材料下限，仍须可收敛。"""
        m, t = get_material("M2"), get_tool("T1")
        rep = converge(m, t, vc=480, fz=0.19, ap=20.0, ae=7.0, ctx=_ctx(1.6))
        self.assertTrue(rep.converged, f"未收敛：{rep.history}")
        p = rep.final_params
        res = calc.compute_all(m, t, vc=p["vc"], fz=p["fz"], ap=p["ap"], ae=p["ae"])
        self.assertLessEqual(res.n, 8000.0)

    def test_already_valid_needs_no_iteration(self) -> None:
        """已合规参数不应触发任何迭代。"""
        m, t = get_material("M1"), get_tool("T3")
        rep = converge(m, t, vc=168, fz=0.12, ap=8.0, ae=4.0, ctx=_ctx())
        self.assertTrue(rep.converged)
        self.assertEqual(rep.iterations, 0)

    def test_all_valid_combos_converge(self) -> None:
        """全部合法材料刀具组合在标准工况下都必须收敛。"""
        from zrcut.domain import MATERIALS, TOOLS

        bad: list[str] = []
        for mid, mo in MATERIALS.items():
            for tid, to in TOOLS.items():
                rep = converge(
                    mo, to,
                    vc=(mo.vc_min + mo.vc_max) / 2,
                    fz=(mo.fz_min + mo.fz_max) / 2,
                    ap=mo.ap_d_max * to.diameter * 0.5,
                    ae=mo.ae_d_max * to.diameter * 0.5,
                    ctx=_ctx(3.2),
                )
                if not rep.converged:
                    bad.append(f"{mid.value}+{tid.value}")
        self.assertEqual(bad, [], f"以下组合未收敛：{bad}")

    def test_history_records_each_round(self) -> None:
        """历史记录应逐轮可追溯。"""
        m, t = get_material("M1"), get_tool("T4")
        rep = converge(m, t, vc=180, fz=0.12, ap=20.0, ae=14.0, ctx=_ctx())
        self.assertGreaterEqual(len(rep.history), 2)
        for h in rep.history:
            self.assertIn("iteration", h)
            self.assertIn("failed", h)
