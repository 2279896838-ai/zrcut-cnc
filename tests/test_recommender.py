"""参数推荐器测试。

覆盖八步生成顺序、策略模式差异、置信度分级、风险提示，
以及推荐结果的数值可验算性。
"""

from __future__ import annotations

from tests import ZrCutTestCase
from zrcut import calc
from zrcut.domain import (
    MATERIALS,
    OPERATIONS,
    TOOLS,
    Confidence,
    MaterialId,
    OperationId,
    Strategy,
    ToolId,
)
from zrcut.recommender import (
    CoolantRequiredError,
    RecommendationError,
    RecommendationRequest,
    RoughnessUnachievableError,
    ToolOperationMismatchError,
    recommend,
)


def _req(**kw):
    """构造默认推荐请求，便于用例覆盖单字段。"""
    defaults = dict(
        material=MaterialId.M1,
        tool=ToolId.T3,
        operation=OperationId.OP2,
        ra_req=1.6,
    )
    defaults.update(kw)
    return RecommendationRequest(**defaults)


class TestRequestValidation(ZrCutTestCase):
    """请求参数的合法性校验。"""

    def test_rejects_zero_ra(self) -> None:
        """Ra 要求为 0 必须拒绝。"""
        with self.assertRaises(RecommendationError):
            _req(ra_req=0.0)

    def test_rejects_negative_volume(self) -> None:
        """负体积必须拒绝。"""
        with self.assertRaises(RecommendationError):
            _req(volume=-1.0)

    def test_rejects_zero_wall(self) -> None:
        """壁厚为 0 必须拒绝。"""
        with self.assertRaises(RecommendationError):
            _req(wall_thickness=0.0)


class TestToolOperationMatch(ZrCutTestCase):
    """刀具与工序的匹配拦截。"""

    def test_face_mill_rejects_end_mill(self) -> None:
        """OP1 端面铣只能用 T6，用立铣刀必须拒绝且给出可读原因。"""
        with self.assertRaises(ToolOperationMismatchError) as ctx:
            recommend(_req(tool=ToolId.T1, operation=OperationId.OP1))
        self.assertIn("不适用于工序", str(ctx.exception))

    def test_curved_finish_rejects_end_mill(self) -> None:
        """OP4 曲面精加工只能用 T5 球头刀。"""
        with self.assertRaises(ToolOperationMismatchError):
            recommend(_req(tool=ToolId.T3, operation=OperationId.OP4))

    def test_side_milling_accepts_all_end_mills(self) -> None:
        """OP2 侧铣接受全部立铣刀 T1-T4。"""
        for tid in (ToolId.T1, ToolId.T2, ToolId.T3, ToolId.T4):
            with self.subTest(tool=tid.value):
                rec = recommend(_req(tool=tid, operation=OperationId.OP2))
                self.assertIsNotNone(rec.primary)


class TestEightStepOrder(ZrCutTestCase):
    """八步生成顺序的可验算性。"""

    def test_primary_metrics_recomputable(self) -> None:
        """主方案每个派生量都必须能用规范公式复算。"""
        rec = recommend(_req(volume=288000.0))
        p = rec.primary
        m, t = rec.material, rec.tool
        assert m is not None and t is not None

        n = calc.spindle_speed(p.vc, t.diameter)
        vf = calc.feed_rate(p.fz, t.teeth, n)
        mrr = calc.material_removal_rate(p.ap, p.ae, vf)

        self.assertAlmostEqualRel(p.n, n, 1e-6, "主轴转速")
        self.assertAlmostEqualRel(p.vf, vf, 1e-6, "进给速度")
        self.assertAlmostEqualRel(p.mrr, mrr, 1e-6, "材料去除率")
        self.assertAlmostEqualRel(p.pc, calc.cutting_power(mrr, m.kc), 1e-6, "切削功率")
        self.assertAlmostEqualRel(
            p.ra_th,
            calc.theoretical_roughness(p.fz, t.corner_radius, t.is_ball),
            1e-6,
            "理论粗糙度",
        )
        self.assertAlmostEqualRel(p.ra_pred, p.ra_th * m.k_ra, 1e-6, "预测粗糙度")
        self.assertAlmostEqualRel(
            p.tool_life, calc.tool_life(p.vc, m.taylor_c, m.taylor_n), 1e-6, "刀具寿命"
        )

    def test_machining_time_when_volume_given(self) -> None:
        """给体积时必须算出工时，且等于体积除以去除率。"""
        rec = recommend(_req(volume=288000.0))
        p = rec.primary
        self.assertIsNotNone(p.machining_time)
        self.assertAlmostEqualRel(p.machining_time, 288000.0 / p.mrr, 1e-6)

    def test_no_time_without_volume(self) -> None:
        """不给体积时工时应为空值而非零。"""
        rec = recommend(_req())
        self.assertIsNone(rec.primary.machining_time)

    def test_primary_always_passes_constraints(self) -> None:
        """主方案必须通过全部硬约束。"""
        rec = recommend(_req())
        self.assertIsNotNone(rec.validation)
        self.assertTrue(rec.validation.passed_all)


class TestStrategies(ZrCutTestCase):
    """策略模式必须产生可区分的方案。"""

    def test_efficiency_has_higher_mrr_than_life(self) -> None:
        """效率优先的材料去除率应高于寿命优先。"""
        eff = recommend(_req(strategy=Strategy.EFFICIENCY))
        life = recommend(_req(strategy=Strategy.LIFE))
        self.assertGreater(eff.primary.mrr, life.primary.mrr)

    def test_life_has_longer_life_than_efficiency(self) -> None:
        """寿命优先的刀具寿命应长于效率优先。"""
        eff = recommend(_req(strategy=Strategy.EFFICIENCY))
        life = recommend(_req(strategy=Strategy.LIFE))
        self.assertGreater(life.primary.tool_life, eff.primary.tool_life)

    def test_quality_has_better_roughness(self) -> None:
        """质量优先的预测粗糙度应优于效率优先。"""
        eff = recommend(_req(strategy=Strategy.EFFICIENCY))
        qlt = recommend(_req(strategy=Strategy.QUALITY))
        self.assertLess(qlt.primary.ra_pred, eff.primary.ra_pred)

    def test_all_strategies_produce_valid_plans(self) -> None:
        """四种策略都必须给出通过校验的主方案。"""
        for s in Strategy:
            with self.subTest(strategy=s.value):
                rec = recommend(_req(strategy=s))
                self.assertTrue(rec.validation.passed_all, f"{s.value} 未通过校验")


class TestAlternatives(ZrCutTestCase):
    """备选方案。"""

    def test_at_least_one_alternative(self) -> None:
        """至少给出一套备选。"""
        rec = recommend(_req())
        self.assertGreaterEqual(len(rec.alternatives), 1)

    def test_alternatives_respect_power_limit(self) -> None:
        """备选方案同样不得超功率、不得零去除率。"""
        rec = recommend(_req())
        for alt in rec.alternatives:
            with self.subTest(label=alt.label):
                self.assertLessEqual(alt.pc, 11.0)
                self.assertGreater(alt.mrr, 0.0)

    def test_labels_unique(self) -> None:
        """主方案与备选的标签不得重复。"""
        rec = recommend(_req())
        labels = [rec.primary.label] + [a.label for a in rec.alternatives]
        self.assertEqual(len(labels), len(set(labels)))


class TestConfidence(ZrCutTestCase):
    """置信度分级。"""

    def test_confidence_is_enum(self) -> None:
        rec = recommend(_req())
        self.assertIsInstance(rec.primary.confidence, Confidence)

    def test_chatter_high_never_high_confidence(self) -> None:
        """颤振 HIGH 时置信度不得为 HIGH。"""
        rec = recommend(_req())
        if rec.primary.chatter_risk == "HIGH":
            self.assertIsNot(rec.primary.confidence, Confidence.HIGH)

    def test_confidence_is_differentiated(self) -> None:
        """全部合法组合中应出现多种置信度，否则分级失去意义。"""
        seen: set[str] = set()
        for mid in MATERIALS:
            for tid, to in TOOLS.items():
                for oid, op in OPERATIONS.items():
                    if tid not in op.typical_tools:
                        continue
                    try:
                        rec = recommend(
                            RecommendationRequest(
                                material=mid, tool=tid, operation=oid, ra_req=1.6
                            )
                        )
                    except RecommendationError:
                        continue
                    seen.add(rec.primary.confidence.value)
        self.assertGreaterEqual(len(seen), 2, f"置信度分级过于单一：{seen}")


class TestWarnings(ZrCutTestCase):
    """风险提示必须覆盖关键场景。"""

    def test_missing_coolant_raises_coolant_error(self) -> None:
        """不锈钢缺冷却必须明确拒答，而不是笼统报「无可行方案」。

        按规范 3.7 节 C-09，M3/M5 不满足冷却条件时不得给出参数建议。
        """
        with self.assertRaises(CoolantRequiredError) as ctx:
            recommend(
                _req(
                    material=MaterialId.M3,
                    tool=ToolId.T1,
                    ra_req=0.8,
                    coolant_available=False,
                )
            )
        self.assertIn("内冷", str(ctx.exception))

    def test_missing_coolant_not_raised_when_available(self) -> None:
        """具备冷却时同一组合应正常给出方案。"""
        rec = recommend(
            _req(
                material=MaterialId.M3,
                tool=ToolId.T1,
                ra_req=0.8,
                coolant_available=True,
            )
        )
        self.assertTrue(rec.validation.passed_all)

    def test_thin_wall_warns(self) -> None:
        """薄壁件必须告警。"""
        rec = recommend(_req(wall_thickness=2.0))
        self.assertTrue(any("薄壁" in w for w in rec.warnings))

    def test_long_overhang_warns(self) -> None:
        """T5 悬伸比 5.0 必须告警。"""
        rec = recommend(_req(tool=ToolId.T5, operation=OperationId.OP4, ra_req=0.4))
        self.assertTrue(any("悬伸" in w for w in rec.warnings))

    def test_converged_params_warn_about_iteration(self) -> None:
        """发生过收敛时必须提示。"""
        rec = recommend(
            _req(material=MaterialId.M1, tool=ToolId.T4, ra_req=1.6)
        )
        if rec.validation and rec.validation.iterations > 0:
            self.assertTrue(any("收敛" in w for w in rec.warnings))


class TestRationality(ZrCutTestCase):
    """参数合理性说明的可追溯性。"""

    def test_contains_every_formula(self) -> None:
        """说明必须逐步给出全部公式代入。"""
        rec = recommend(_req())
        text = rec.rationality
        for token in (
            "n = 1000 × vc",
            "vf = fz × z × n",
            "MRR = ap × ae × vf",
            "Pc = MRR × Kc",
            "Ra_th = fz²",
            "Ra_pred = Ra_th × K_ra",
            "T = (C / vc)",
        ):
            with self.subTest(formula=token):
                self.assertIn(token, text)

    def test_key_numbers_traceable(self) -> None:
        """说明中的转速与功率必须与主方案一致。"""
        rec = recommend(_req())
        p = rec.primary
        self.assertIn(f"{p.n:.1f}", rec.rationality)
        self.assertIn(f"{p.pc:.3f}", rec.rationality)


class TestUnachievable(ZrCutTestCase):
    """不可达请求的可诊断报错。"""

    def test_extremely_strict_ra_raises_with_reason(self) -> None:
        """Ra 极严时必须明确说明无法达到，而非静默降级。"""
        with self.assertRaises(RoughnessUnachievableError) as ctx:
            recommend(_req(material=MaterialId.M1, tool=ToolId.T4, ra_req=0.01))
        self.assertIn("无法达到", str(ctx.exception))
